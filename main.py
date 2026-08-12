import argparse
import getpass
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
import schedule

import constants.anki as anki_constants
import constants.dida365 as dida365_constants
from agent.agent import Agent
from agent.eudic import (
    Eudic,
    EudicNoteFetchError,
    EudicNoteImageDownloadError,
    EudicWordFetchError,
    EudicWriteError,
)
from agent.eudic_app_sync import EudicAppSyncClient, EudicAppSyncError
from constants.eudic import EUDIC_NOTE_IMAGES_PLACEHOLDER
from constants.prompt import SYSTEM_WORD_TEACHER, USER_ASK_EXP, USER_ASK_WORD
from constants.yaml import ANKI_PUSH_ENDPOINT, EUDIC_API_KEY
from dida365_project.api.dida365 import Dida365 as Dida365Api
from dida365_project.api.dida365 import DidaLoginCooldownError
from dida365_project.api.dida365 import DidaSessionValidationError
from dida365_project.api.dida365 import DidaSignInError
from dida365_project.models.task import Task
from models.anki import UserQuery
from utils.markdown_to_html_util import markdown_to_html
from utils.phonetic_util import get_all_phonetic
from utils.datetime_util import parse_eudic_api_time
from utils.word_his_db import add_word_to_his_set, if_exists_in_his_set
from utils.yaml_config_manager import YamlConfigManager


SCHEDULED_JOB_LAST_SUCCESS: dict[str, str] = {}
PLAYER_NOTE_PREFIX_PATTERN = re.compile(r"^\*\*来源：\*\*[ \t]*《")
GENERIC_NOTE_HEADING = "**生词语境：**"


def run_scheduled_job(name: str, job, *, log_success: bool = False):
    """记录任务边界；失败后继续抛出，让 systemd 按既有策略重启。"""
    started_at = time.monotonic()
    if log_success:
        print(f"[调度任务开始] {name}", flush=True)
    try:
        result = job()
    except Exception as error:
        elapsed = time.monotonic() - started_at
        print(
            f"[调度任务失败] {name}，耗时 {elapsed:.1f} 秒：{type(error).__name__}: {error}",
            file=sys.stderr,
            flush=True,
        )
        raise

    completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    SCHEDULED_JOB_LAST_SUCCESS[name] = completed_at
    if log_success:
        elapsed = time.monotonic() - started_at
        print(f"[调度任务完成] {name}，耗时 {elapsed:.1f} 秒", flush=True)
    return result


def log_scheduler_heartbeat():
    if SCHEDULED_JOB_LAST_SUCCESS:
        job_status = "；".join(
            f"{name}={completed_at}"
            for name, completed_at in sorted(SCHEDULED_JOB_LAST_SUCCESS.items())
        )
    else:
        job_status = "等待首次任务完成"
    print(f"[服务心跳] {job_status}", flush=True)


def format_note_for_task(note: str) -> str:
    normalized_note = note.replace("\r\n", "\n").replace("\r", "\n").strip()
    # 跨项目约定：字幕播放器用稳定的“**来源：**《”前缀标记已经排版的 Note。
    # 欧路中保留完整来源；生成滴答正文时隐藏首行文件名，只保留下方引用块。
    # 修改识别规则时，必须同步 subtitle_video_player/js/eudic_integration.js
    # 的 buildNoteFromContext() 及两个项目 README 中的说明。
    if PLAYER_NOTE_PREFIX_PATTERN.match(normalized_note):
        _, _, player_context = normalized_note.partition("\n")
        player_context = player_context.lstrip("\n")
        if not player_context:
            return GENERIC_NOTE_HEADING
        return "\n".join([GENERIC_NOTE_HEADING, player_context])

    quoted_lines = [
        ">" if not line.strip() else f"> {line}"
        for line in normalized_note.split("\n")
    ]
    return "\n".join([GENERIC_NOTE_HEADING, *quoted_lines])


def compose_word_task_content(
    phonetic: str,
    note: str | None,
    explanation: str,
    note_image_count: int = 0,
) -> str:
    sections = [phonetic]
    if note and note.strip():
        note_section = format_note_for_task(note)
        if note_image_count:
            note_section = "\n".join([note_section, EUDIC_NOTE_IMAGES_PLACEHOLDER])
        sections.append(note_section)
    elif note_image_count:
        sections.append("\n".join([GENERIC_NOTE_HEADING, EUDIC_NOTE_IMAGES_PLACEHOLDER]))
    sections.append(explanation)
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


class EudicPublishError(RuntimeError):
    pass


class EudicNoteConflictError(EudicPublishError):
    pass


def normalize_note_text(note: str) -> str:
    return note.replace("\r\n", "\n").replace("\r", "\n").strip()


def format_eudic_add_time(word_record: dict, subject: str = "") -> str:
    add_time = word_record.get("add_time")
    if not isinstance(add_time, str) or not add_time.strip():
        return f"{subject}添加时间未知"
    try:
        formatted_time = parse_eudic_api_time(add_time).strftime("%Y-%m-%d %H:%M:%S 北京时间")
    except ValueError:
        return f"{subject}添加时间未知"
    return f"{subject}添加于：{formatted_time}"


def _read_note_for_publish(eudic: Eudic, word: str) -> str | None:
    try:
        note = eudic.get_note(word)
    except EudicNoteFetchError as error:
        raise EudicPublishError(f"无法确认单词 [{word}] 的欧路笔记状态，未添加生词记录。") from error
    return normalize_note_text(note) if note is not None else None


def _read_note_data_for_publish(eudic: Eudic, word: str):
    try:
        return eudic.get_note_data(word)
    except EudicNoteFetchError as error:
        raise EudicPublishError(f"无法确认单词 [{word}] 的欧路笔记状态，未添加生词记录。") from error


def _note_data_matches(note_data, note: str, image_filenames: list[str]) -> bool:
    if note_data is None or normalize_note_text(note_data.text) != note:
        return False
    actual_filenames = [
        (image.original_filename or "").lower()
        for image in note_data.images
    ]
    return actual_filenames == [filename.lower() for filename in image_filenames]


def _read_word_for_publish(eudic: Eudic, word: str) -> dict | None:
    try:
        return eudic.get_word(word)
    except EudicWordFetchError as error:
        raise EudicPublishError(f"无法确认单词 [{word}] 的欧路生词状态，已停止操作。") from error


def publish_single_word(
    eudic: Eudic,
    word: str,
    note: str | None = None,
    note_image_paths: list[Path] | None = None,
) -> str:
    """将完整生词记录发布到欧路，滴答任务由常驻同步流程统一创建。"""
    word = word.strip().lower()
    if not word:
        raise ValueError("未提供有效单词。")
    note_image_paths = note_image_paths or []
    if note is not None:
        note = normalize_note_text(note)
        if not note:
            raise ValueError("笔记内容不能为空。")

    prepared_images = []
    if note_image_paths:
        try:
            prepared_images = EudicAppSyncClient.prepare_images(note_image_paths)
        except EudicAppSyncError as error:
            raise EudicPublishError(str(error)) from error
    image_filenames = [image.filename for image in prepared_images]
    has_note_payload = note is not None or bool(prepared_images)
    target_note = note or ""

    existing_word = _read_word_for_publish(eudic, word)
    if existing_word is not None:
        if has_note_payload:
            if prepared_images:
                existing_note_data = _read_note_data_for_publish(eudic, word)
                note_matches = _note_data_matches(
                    existing_note_data,
                    target_note,
                    image_filenames,
                )
                existing_note = existing_note_data.text if existing_note_data else None
            else:
                existing_note = _read_note_for_publish(eudic, word)
                note_matches = existing_note == note
            if existing_note is None and not note_matches:
                raise EudicNoteConflictError(
                    f"单词 [{word}] 已存在于欧路生词本（{format_eudic_add_time(existing_word)}），"
                    "但没有笔记；本命令不会给历史生词补写笔记。",
                )
            if not note_matches:
                conflict_subject = "欧路笔记文字或图片" if prepared_images else "欧路笔记"
                raise EudicNoteConflictError(
                    f"单词 [{word}] 已存在（{format_eudic_add_time(existing_word)}），"
                    f"但{conflict_subject}与本次输入不同；未覆盖现有笔记。",
                )
            print(
                f"单词 [{word}] 及笔记已完整存在于欧路词典"
                f"（{format_eudic_add_time(existing_word, subject='单词')}），无需重复添加。",
            )
        else:
            print(
                f"单词 [{word}] 已存在于欧路生词本"
                f"（{format_eudic_add_time(existing_word)}），无需重复添加。",
            )
        return "existing"

    if prepared_images:
        existing_note_data = _read_note_data_for_publish(eudic, word)
        if existing_note_data is None:
            try:
                EudicAppSyncClient().save_note_with_images(
                    word,
                    target_note,
                    prepared_images,
                )
            except EudicAppSyncError as write_error:
                # 私有同步响应异常也先走 OpenAPI 回读；服务端可能已经完整保存。
                if not _note_data_matches(
                    _read_note_data_for_publish(eudic, word),
                    target_note,
                    image_filenames,
                ):
                    raise EudicPublishError(
                        f"单词 [{word}] 的欧路图片笔记未能确认保存成功，未添加生词记录。"
                    ) from write_error
            verified_note_data = _read_note_data_for_publish(eudic, word)
            if not _note_data_matches(verified_note_data, target_note, image_filenames):
                raise EudicPublishError(
                    f"单词 [{word}] 的欧路图片笔记校验不一致，未添加生词记录。"
                )
        elif not _note_data_matches(existing_note_data, target_note, image_filenames):
            raise EudicNoteConflictError(
                f"单词 [{word}] 尚未加入生词本，但已经存在不同的欧路图片笔记；未覆盖现有笔记。"
            )
        else:
            print(f"单词 [{word}] 的欧路图片笔记已经保存，将继续添加生词记录。")
    elif note is not None:
        existing_note = _read_note_for_publish(eudic, word)
        if existing_note is None:
            try:
                eudic.save_note(word, note)
            except EudicWriteError as write_error:
                # 响应异常时先读回对账；服务端可能已经成功保存。
                if _read_note_for_publish(eudic, word) != note:
                    raise EudicPublishError(
                        f"单词 [{word}] 的欧路笔记未能确认保存成功，未添加生词记录。",
                    ) from write_error
            verified_note = _read_note_for_publish(eudic, word)
            if verified_note != note:
                raise EudicPublishError(
                    f"单词 [{word}] 的欧路笔记校验不一致，未添加生词记录。",
                )
        elif existing_note != note:
            raise EudicNoteConflictError(
                f"单词 [{word}] 尚未加入生词本，但已经存在不同的欧路笔记；未覆盖现有笔记。",
            )
        else:
            print(f"单词 [{word}] 的欧路笔记已经保存，将继续添加生词记录。")

    try:
        eudic.add_word(word)
    except EudicWriteError as write_error:
        # 写响应不确定时通过查询最终状态判断，避免盲目重试。
        if _read_word_for_publish(eudic, word) is None:
            raise EudicPublishError(
                f"单词 [{word}] 未能确认添加到欧路生词本，请稍后用相同命令重试。",
            ) from write_error

    if _read_word_for_publish(eudic, word) is None:
        raise EudicPublishError(
            f"单词 [{word}] 写入后未通过欧路查询校验，请稍后用相同命令重试。",
        )

    if has_note_payload:
        print(f"单词 [{word}] 及笔记已保存到欧路词典，等待后台同步到滴答清单。")
    else:
        print(f"单词 [{word}] 已保存到欧路词典，等待后台同步到滴答清单。")
    return "created"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="背单词单词任务系统")
    parser.add_argument(
        "--add-word",
        metavar="WORD",
        help='向欧路生词本添加单个单词（词组用英文双引号括起来，如 --add-word "be in a fix"）；由后台同步到滴答清单',
    )
    note_group = parser.add_mutually_exclusive_group()
    note_group.add_argument(
        "--note",
        metavar="TEXT",
        help="随 --add-word 保存的单行或较短笔记",
    )
    note_group.add_argument(
        "--note-file",
        metavar="PATH",
        help="从 UTF-8 文本文件读取随 --add-word 保存的多行笔记",
    )
    parser.add_argument(
        "--note-image",
        metavar="PATH",
        action="append",
        default=[],
        help="随 --add-word 上传的欧路笔记图片；可重复指定多张，依赖本机已登录的欧路桌面 App",
    )
    parser.add_argument(
        "--set-dida-t",
        action="store_true",
        help="安全导入并验证滴答清单 t 会话凭证；输入不回显，成功后立即退出",
    )
    return parser


def resolve_note_argument(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str | None:
    if (args.note is not None or args.note_file is not None or args.note_image) and not args.add_word:
        parser.error("--note、--note-file 和 --note-image 只能与 --add-word 一起使用")

    note = args.note
    if args.note_file is not None:
        try:
            note = Path(args.note_file).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            parser.error(f"无法读取笔记文件 [{args.note_file}]：{error}")

    if note is None:
        return None
    note = normalize_note_text(note)
    if not note:
        parser.error("笔记内容不能为空")
    return note


def resolve_note_image_arguments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[Path]:
    image_paths = [Path(path) for path in args.note_image]
    for path in image_paths:
        if not path.is_file():
            parser.error(f"无法读取笔记图片 [{path}]")
    return image_paths


class Bearer:
    def __init__(self) -> None:
        self.agent = Agent()

    def acquire_words(self, days: int, include_notes: bool = False):
        words = self.agent.eudic.get_words_in_book(days=days)
        words = [w for w in words if w.is_in_last_days_range(days)]
        words = [w for w in words if not if_exists_in_his_set(w.word)]
        if include_notes:
            words_with_notes = []
            for word in words:
                try:
                    note_data = self.agent.eudic.get_note_data(word.word)
                    word.note = note_data.text if note_data is not None else None
                    word.note_images = note_data.images if note_data is not None else ()
                    words_with_notes.append(word)
                except EudicNoteFetchError as error:
                    print(f"{error}，本轮跳过，下一轮继续重试。", flush=True)
            words = words_with_notes
        return list(words)

    def get_doubao_explanation_by_doubao(self, word: str):
        self.agent.doubao.add_system_message(SYSTEM_WORD_TEACHER)
        answer = self.agent.doubao.chat(USER_ASK_WORD.format(word=word))
        return answer

    def add_single_word(self, word: str, note: str | None = None):
        return publish_single_word(self.agent.eudic, word, note)

    def bear_eudic_to_dida365(self):
        """deprecated"""
        words = self.acquire_words(7, include_notes=True)
        if words:
            print(f"添加单词本生词:{words}", flush=True)
        for word in words:
            try:
                note_image_files = self.agent.eudic.download_note_images(word.note_images)
            except EudicNoteImageDownloadError as error:
                print(f"{error}，本轮跳过，下一轮继续重试。", flush=True)
                continue
            content = self.get_doubao_explanation_by_doubao(word.word)
            content += "\n\n[通过web添加anki生词](" + f"{YamlConfigManager().get_config(ANKI_PUSH_ENDPOINT)}?word={quote(word.word)}" + ")"
            content = compose_word_task_content(
                get_all_phonetic(word.word),
                word.note,
                content,
                note_image_count=len(note_image_files),
            )
            sync_succeeded = False
            try:
                self.agent.dida.add_task(
                    word.word,
                    content,
                    note_image_files=note_image_files,
                )
            except:  # noqa: E722
                traceback.print_exc()
            else:
                sync_succeeded = True
            # 图片任务只有在附件和正文引用都校验完成后才能进入历史；无图任务保留
            # 旧行为，以免发音或视频附件的既有容错语义发生无关变化。
            if sync_succeeded or not note_image_files:
                try:
                    self.agent.dida.find_task(word.word, if_reload_data=True)
                    add_word_to_his_set(word.word)
                except:  # noqa: E722
                    pass

    def bear_eudic_to_anki(self):
        words = self.acquire_words(7)
        print(f"添加单词本生词:{words}")
        for word in words:
            content = self.get_doubao_explanation_by_doubao(word.word)
            content = markdown_to_html(content)
            try:
                self.agent.anki_client.add_note(word.word, content)
            except:  # noqa: E722
                traceback.print_exc()
            finally:
                if self.agent.anki_client.search_note_existence(word.word):
                    add_word_to_his_set(word.word)

    def search_questions_from_dida365(self):
        """deprecated"""
        self.agent.dida.dida.get_latest_data()
        task_with_question: list[tuple[Task, list[str]]] = []
        for task in [t for t in self.agent.dida.dida.active_tasks if t.content and t.project_id == dida365_constants.VOCAB_BOOK_PROJECT_ID]:
            questions = [
                q for q in re.findall(dida365_constants.QUESTION_PREFIX + r"(.*?)" + dida365_constants.QUESTION_SUFFIX, task.content) if q
            ]
            if questions:
                task_with_question.append((task, questions))
        if task_with_question:
            print(f"搜索问题结果:{task_with_question}", flush=True)
        return task_with_question

    def answer_question_from_dida365(self):
        """deprecated"""
        for task, questions in self.search_questions_from_dida365():
            for question in questions:
                self.agent.substitute_new_doubao_agent()
                self.agent.doubao.add_system_message(
                    "{}{}{}".format(
                        re.sub(dida365_constants.QUESTION_PREFIX + r"(.*?)" + dida365_constants.QUESTION_SUFFIX, "", task.content),
                        "-" * 30,
                        USER_ASK_EXP,
                    )
                )
                answer = self.agent.doubao.chat(question)
                answer = answer.strip()
                task.update_content(
                    task.content.replace(
                        f"{dida365_constants.QUESTION_PREFIX}{question}{dida365_constants.QUESTION_SUFFIX}",
                        f"➡️Q:{question} ↔️ A:{answer}⬅️",
                    )
                )
            self.agent.dida.update_task(task.task_dict)

    def search_and_answer_questions_from_anki(self):
        note_with_question: list[UserQuery] = self.agent.anki_client.search_user_query()
        print(f"搜索Anki问题结果:{note_with_question}")
        for user_query in note_with_question:
            self.agent.substitute_new_doubao_agent()
            self.agent.doubao.add_system_message(
                "{}{}{}".format(
                    re.sub(anki_constants.QUESTION_PREFIX + r"(.*?)" + anki_constants.QUESTION_SUFFIX, "", user_query.note_content),
                    "-" * 30,
                    USER_ASK_EXP,
                )
            )
            answer = self.agent.doubao.chat(user_query.query)
            answer = answer.strip()
            answer = re.sub(r"\*\*\*\*(.*?)\*\*\*\*", r"<b><i>\1</i></b>", answer)
            answer = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", answer)
            answer = re.sub(r"\*(.*?)\*", r"<i>\1</i>", answer)
            user_query.note_content = user_query.note_content.replace(
                f"{anki_constants.QUESTION_PREFIX}{user_query.query}{anki_constants.QUESTION_SUFFIX}",
                f'<span style="background-color: #feecd0;">Q:{user_query.query}</span> <span style="background-color: #dbeafe;">A:{answer}</span>',
            )
            self.agent.anki_client.update_note_fields(user_query.id, {"答案": user_query.note_content})


if __name__ == "__main__":
    parser = build_argument_parser()
    args = parser.parse_args()
    note = resolve_note_argument(parser, args)
    note_image_paths = resolve_note_image_arguments(parser, args)

    if args.set_dida_t:
        t_value = getpass.getpass("请输入滴答清单 t 会话凭证：")
        try:
            Dida365Api.import_session_cookie(t_value)
        except ValueError as error:
            print(f"导入失败：{error}")
            raise SystemExit(1) from error
        except requests.RequestException as error:
            status_code = error.response.status_code if error.response is not None else "网络错误"
            print(f"暂时无法验证 t，会话文件未修改。状态：{status_code}")
            raise SystemExit(1) from error
        except OSError as error:
            print("无法安全写入滴答清单会话文件，请检查目录权限。")
            raise SystemExit(1) from error
        print("滴答清单 t 已验证并安全保存。")
        raise SystemExit(0)

    if args.add_word:
        eudic = Eudic(api_key=YamlConfigManager().get_config(EUDIC_API_KEY))
        try:
            publish_single_word(
                eudic,
                args.add_word,
                note,
                note_image_paths=note_image_paths,
            )
        except (EudicPublishError, ValueError) as error:
            print(f"添加失败：{error}", file=sys.stderr)
            raise SystemExit(1) from error
        raise SystemExit(0)

    try:
        b = Bearer()
    except (DidaLoginCooldownError, DidaSessionValidationError, DidaSignInError) as error:
        print(error)
        raise SystemExit(75) from error

    schedule.every(1).minutes.do(
        run_scheduled_job,
        "同步欧路生词到滴答",
        b.bear_eudic_to_dida365,
        log_success=True,
    )
    schedule.every(10).seconds.do(
        run_scheduled_job,
        "检查并回答滴答问题",
        b.answer_question_from_dida365,
    )
    schedule.every(1).day.at("00:01").do(
        run_scheduled_job,
        "续期逾期单词任务",
        b.agent.dida.renew_overdue_task,
        log_success=True,
    )
    schedule.every(1).minutes.do(log_scheduler_heartbeat)
    print("[服务启动] 定时任务调度已开始。", flush=True)

    for _ in range(3600):  # 只运行1小时
        schedule.run_pending()
        time.sleep(1)
    print("[服务退出] 已达到一小时运行周期。", flush=True)
