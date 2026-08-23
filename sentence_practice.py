import hashlib
import json
import random
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from constants.prompt import SYSTEM_SENTENCE_PRACTICE_REVIEWER
from dida365_project.models.task import Task
from utils.sentence_practice_db import (
    COMMENT_ROLE_SOURCE,
    COMMENT_ROLE_SYSTEM_CLARIFICATION,
    COMMENT_ROLE_USER_FOLLOWUP,
    INTERACTION_STATUS_APPLYING_BODY,
    INTERACTION_STATUS_AWAITING_CLARIFICATION,
    INTERACTION_STATUS_BODY_APPLIED,
    INTERACTION_STATUS_CLARIFICATION_PREPARED,
    INTERACTION_STATUS_DELETING_COMMENTS,
    INTERACTION_STATUS_DONE,
    INTERACTION_STATUS_PROCESSING,
    INTERACTION_STATUS_QUEUED,
    INTERACTION_STATUS_READY_TO_APPLY,
    INTERACTION_STATUS_SENDING_CLARIFICATION,
    TASK_STATUS_ACTIVE,
    TASK_STATUS_CLOSED,
    TASK_STATUS_CREATE_FAILED,
    TASK_STATUS_CREATING,
    TASK_STATUS_DELETED,
    SentencePracticeStateStore,
)


PRACTICE_TIMEZONE = ZoneInfo("Asia/Shanghai")
PRACTICE_TASK_TITLE_PREFIX = "每日单词组合造句"
CREATING_RECOVERY_GRACE = timedelta(minutes=5)
SYSTEM_MARKER_PATTERN = re.compile(r"\n?\[\[sentence-practice:clarification:[^\]]+\]\]\s*$")
GROUP_HEADING_PATTERN = re.compile(r"^## 第 (\d+) 组\s*$", re.MULTILINE)


class SentencePracticeError(RuntimeError):
    pass


def _group_sizes(word_count: int) -> list[int]:
    if word_count <= 0:
        return []
    if word_count <= 4:
        return [word_count]
    quotient, remainder = divmod(word_count, 3)
    if remainder == 0:
        return [3] * quotient
    if remainder == 1:
        return [3] * (quotient - 1) + [4]
    return [3] * quotient + [2]


def group_vocabulary_tasks(tasks, practice_date: str):
    candidates = sorted(
        ({"task_id": task.id, "word": task.title.strip()} for task in tasks),
        key=lambda item: item["task_id"],
    )
    seed_material = practice_date + "|" + "|".join(
        item["task_id"] for item in candidates
    )
    seed = int.from_bytes(
        hashlib.sha256(seed_material.encode("utf-8")).digest()[:8],
        "big",
    )
    random.Random(seed).shuffle(candidates)
    groups = []
    offset = 0
    for group_id, size in enumerate(_group_sizes(len(candidates)), start=1):
        members = candidates[offset : offset + size]
        groups.append(
            {
                "group_id": group_id,
                "words": [member["word"] for member in members],
                "task_ids": [member["task_id"] for member in members],
            }
        )
        offset += size
    return groups


def build_practice_task_body(practice_date: str, groups) -> str:
    word_count = sum(len(group["words"]) for group in groups)
    sections = [
        "# 每日单词组合造句",
        "",
        f"日期：{practice_date}　单词：{word_count} 个　分组：{len(groups)} 组",
        "",
        "请尽量把同一组的单词放进一个句子或一小段话中。语义可以荒诞、超现实或天马行空；重点是语法正确，并准确体现目标词的词义、词性和搭配。",
        "",
        "直接在评论区自由作答即可，无需标注组号。系统会结合单词和上下文识别对应分组；确实无法判断时，会在评论中追问。作答、点评和后续修改会整理到本正文中。",
    ]
    for group in groups:
        group_id = group["group_id"]
        words = "　·　".join(f"`{word}`" for word in group["words"])
        sections.extend(
            [
                "",
                f"## 第 {group_id} 组",
                "",
                f"**目标单词：** {words}",
                "",
                "### 练习记录",
                "",
                "_等待用户作答。_",
            ]
        )
    return "\n".join(sections).strip()


def parse_ai_review(raw_response: str, valid_group_ids: set[int]):
    text = raw_response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise SentencePracticeError("AI 点评没有返回有效 JSON") from error
        try:
            result = json.loads(text[start : end + 1])
        except json.JSONDecodeError as nested_error:
            raise SentencePracticeError("AI 点评没有返回有效 JSON") from nested_error
    if not isinstance(result, dict):
        raise SentencePracticeError("AI 点评 JSON 必须是对象")

    action = result.get("action")
    if action == "clarify":
        question = str(result.get("clarification_question") or "").strip()
        if not question:
            raise SentencePracticeError("AI 请求澄清但没有给出问题")
        return {"action": "clarify", "clarification_question": question}
    if action != "apply":
        raise SentencePracticeError(f"AI 返回了未知动作：{action}")

    updates = result.get("updates")
    if not isinstance(updates, list) or not updates:
        raise SentencePracticeError("AI 点评缺少分组更新")
    normalized_updates = []
    seen_group_ids = set()
    for update in updates:
        if not isinstance(update, dict):
            raise SentencePracticeError("AI 分组更新格式无效")
        try:
            group_id = int(update.get("group_id"))
        except (TypeError, ValueError) as error:
            raise SentencePracticeError("AI 分组编号无效") from error
        if group_id not in valid_group_ids:
            raise SentencePracticeError(f"AI 返回了不存在的第 {group_id} 组")
        feedback = str(update.get("feedback_markdown") or "").strip()
        if not feedback:
            raise SentencePracticeError(f"AI 没有给出第 {group_id} 组的点评")
        relevant_history = update.get("relevant_history")
        if not isinstance(relevant_history, list) or not relevant_history:
            raise SentencePracticeError(f"AI 没有给出第 {group_id} 组的相关互动原文")
        normalized_history = []
        allowed_roles = {
            COMMENT_ROLE_SOURCE,
            COMMENT_ROLE_SYSTEM_CLARIFICATION,
            COMMENT_ROLE_USER_FOLLOWUP,
        }
        for entry in relevant_history:
            if not isinstance(entry, dict):
                raise SentencePracticeError(f"AI 返回的第 {group_id} 组互动原文格式无效")
            role = entry.get("role")
            excerpt = str(entry.get("content") or "").strip()
            if role not in allowed_roles or not excerpt:
                raise SentencePracticeError(f"AI 返回的第 {group_id} 组互动原文格式无效")
            normalized_history.append({"role": role, "content": excerpt})
        if group_id in seen_group_ids:
            continue
        seen_group_ids.add(group_id)
        normalized_updates.append(
            {
                "group_id": group_id,
                "relevant_history": normalized_history,
                "feedback_markdown": feedback,
            }
        )
    return {"action": "apply", "updates": normalized_updates}


def _safe_body_text(text: str) -> str:
    text = text.replace("sentence-practice:", "sentence‑practice:")
    text = re.sub(r"^## 第 (\d+) 组\s*$", r"### 第 \1 组", text, flags=re.MULTILINE)
    text = re.sub(r"^### 互动记录 ·", "#### 互动记录 ·", text, flags=re.MULTILINE)
    return text.strip()


def _quote_markdown(text: str) -> str:
    lines = _safe_body_text(text).splitlines() or [""]
    return "\n".join(">" if not line else f"> {line}" for line in lines)


def _strip_system_marker(text: str) -> str:
    return SYSTEM_MARKER_PATTERN.sub("", text).strip()


def _relevant_history_markdown(relevant_history, comments) -> str:
    comments_by_role = {}
    for comment in comments:
        comments_by_role.setdefault(comment["role"], []).append(
            _strip_system_marker(comment["title"])
        )
    role_labels = {
        COMMENT_ROLE_SOURCE: "用户输入",
        COMMENT_ROLE_SYSTEM_CLARIFICATION: "AI 追问",
        COMMENT_ROLE_USER_FOLLOWUP: "用户补充",
    }
    sections = []
    for entry in relevant_history:
        role = entry["role"]
        excerpt = entry["content"]
        if not any(excerpt in original for original in comments_by_role.get(role, [])):
            raise SentencePracticeError("AI 返回的相关互动片段不在原始评论中")
        sections.extend(
            [f"**{role_labels[role]}：**", "", _quote_markdown(excerpt), ""]
        )
    return "\n".join(sections).rstrip()


def _group_bounds(content: str, group_id: int) -> tuple[int, int]:
    matches = list(GROUP_HEADING_PATTERN.finditer(content or ""))
    target_indexes = [
        index
        for index, match in enumerate(matches)
        if int(match.group(1)) == group_id
    ]
    if len(target_indexes) != 1:
        raise SentencePracticeError(f"任务正文缺少唯一的第 {group_id} 组标题")
    target_index = target_indexes[0]
    start = matches[target_index].start()
    end = matches[target_index + 1].start() if target_index + 1 < len(matches) else len(content)
    return start, end


def append_review_to_body(content, interaction, comments, ai_result):
    updated_content = content or ""
    for update in sorted(ai_result["updates"], key=lambda item: item["group_id"]):
        group_id = update["group_id"]
        start_index, end_index = _group_bounds(updated_content, group_id)
        group_segment = updated_content[start_index:end_index]
        if interaction["record_heading"] in group_segment:
            continue
        group_segment = group_segment.replace("\n_等待用户作答。_\n", "\n", 1)
        feedback = _safe_body_text(update["feedback_markdown"])
        conversation = _relevant_history_markdown(
            update["relevant_history"], comments
        )
        history_block = "\n".join(
            [
                "",
                interaction["record_heading"],
                "",
                conversation,
                "",
                "**AI 点评：**",
                "",
                feedback,
                "",
            ]
        )
        replacement = group_segment.rstrip() + "\n" + history_block
        updated_content = (
            updated_content[:start_index]
            + replacement
            + updated_content[end_index:]
        )
    return re.sub(r"\n{4,}", "\n\n\n", updated_content).strip()


class SentencePracticeService:
    def __init__(
        self,
        dida_agent,
        doubao,
        state_store: SentencePracticeStateStore | None = None,
        now_provider=None,
    ) -> None:
        self.dida_agent = dida_agent
        self.dida = dida_agent.dida
        self.doubao = doubao
        self.state = state_store or SentencePracticeStateStore()
        self.now_provider = now_provider or (lambda: datetime.now(PRACTICE_TIMEZONE))

    def generate_daily_task(self):
        practice_date = self.now_provider().astimezone(PRACTICE_TIMEZONE).date().isoformat()
        candidates = self.dida_agent.get_today_vocabulary_tasks()
        if not candidates:
            print(f"[每日造句] {practice_date} 没有当天到期单词，跳过创建。", flush=True)
            return None

        groups = group_vocabulary_tasks(candidates, practice_date)
        title = f"{PRACTICE_TASK_TITLE_PREFIX} · {practice_date}"
        existing = self.state.get_task_by_date(practice_date)
        if existing is None:
            remote_matches = [
                task for task in self.dida.active_tasks if task.title == title
            ]
            if len(remote_matches) > 1:
                raise SentencePracticeError(f"发现多个同名每日造句任务：{title}")
            task_id = remote_matches[0].id if remote_matches else uuid.uuid4().hex[:24]
            existing = self.state.reserve_daily_task(
                practice_date,
                task_id,
                VOCAB_BOOK_PROJECT_ID,
                title,
                groups,
            )

        remote = self.dida.get_task(existing["task_id"])
        if remote and not remote.get("deleted"):
            status = TASK_STATUS_ACTIVE if remote.get("status") == Task.STATUS_ACTIVE else TASK_STATUS_CLOSED
            self.state.update_task_observation(
                existing["task_id"],
                status=status,
                comment_count=remote.get("commentCount", 0),
                etag=remote.get("etag"),
            )
            return self.state.get_task_by_id(existing["task_id"])
        if remote and remote.get("deleted"):
            self.state.update_task_observation(existing["task_id"], status=TASK_STATUS_DELETED)
            return self.state.get_task_by_id(existing["task_id"])

        stored_groups = json.loads(existing["groups_json"])
        content = build_practice_task_body(practice_date, stored_groups)
        date_time = f"{practice_date}T00:00:00+08:00"
        task_dict = {
            "id": existing["task_id"],
            "projectId": VOCAB_BOOK_PROJECT_ID,
            "title": existing["title"],
            "content": content,
            "startDate": date_time,
            "dueDate": date_time,
            "kind": Task.KIND_TEXT,
            "status": Task.STATUS_ACTIVE,
            "priority": 0,
            "isAllDay": True,
            "timeZone": "Asia/Shanghai",
        }
        self.dida.post_task(Task.gen_add_data_payload(task_dict))
        verified = self.dida.get_task(existing["task_id"])
        if not verified or verified.get("deleted"):
            raise SentencePracticeError("每日造句任务创建后未能回读")
        if verified.get("title") != existing["title"] or verified.get("content") != content:
            raise SentencePracticeError("每日造句任务创建后的标题或正文与预期不一致")
        self.state.update_task_observation(
            existing["task_id"],
            status=TASK_STATUS_ACTIVE,
            comment_count=verified.get("commentCount", 0),
            etag=verified.get("etag"),
        )
        print(
            f"[每日造句] 已创建 {practice_date} 练习：{len(candidates)} 个单词，{len(groups)} 组。",
            flush=True,
        )
        return self.state.get_task_by_id(existing["task_id"])

    def poll_and_process(self, max_actions=20):
        self._scan_remote_comments()
        processed = 0
        while processed < max_actions:
            interaction = self.state.claim_next_action()
            if interaction is None:
                break
            resume_status = self._resume_status(interaction["status"])
            try:
                self._run_claimed_action(interaction)
            except Exception as error:
                self.state.record_failure(interaction["id"], resume_status, error)
                print(
                    f"[每日造句] 交互 {interaction['id']} 处理失败，将重试：{type(error).__name__}: {error}",
                    flush=True,
                )
            processed += 1
        return processed

    @staticmethod
    def _resume_status(claimed_status):
        return {
            INTERACTION_STATUS_PROCESSING: INTERACTION_STATUS_QUEUED,
            INTERACTION_STATUS_SENDING_CLARIFICATION: INTERACTION_STATUS_CLARIFICATION_PREPARED,
            INTERACTION_STATUS_APPLYING_BODY: INTERACTION_STATUS_READY_TO_APPLY,
            INTERACTION_STATUS_DELETING_COMMENTS: INTERACTION_STATUS_DELETING_COMMENTS,
        }[claimed_status]

    def _scan_remote_comments(self):
        # 一轮只同步一次任务列表，避免随着历史上仍未完成的每日练习增多，
        # 每 10 秒为每个任务各发一次详情请求。仅在同步结果缺失时单独回读。
        self.dida.get_latest_data()
        synchronized_tasks = {
            task.id: deepcopy(task.task_dict) for task in self.dida.active_tasks
        }
        for task_record in self.state.list_monitored_tasks():
            remote = synchronized_tasks.get(task_record["task_id"])
            if remote is None:
                remote = self.dida.get_task(task_record["task_id"])
            if remote is None:
                if task_record["status"] == TASK_STATUS_CREATING:
                    if self._creating_recovery_expired(task_record):
                        self.state.update_task_observation(
                            task_record["task_id"], status=TASK_STATUS_CREATE_FAILED
                        )
                        print(
                            f"[每日造句] {task_record['practice_date']} 的本地预留任务"
                            "超过 5 分钟仍不存在，已停止监控。",
                            flush=True,
                        )
                else:
                    self.state.update_task_observation(
                        task_record["task_id"], status=TASK_STATUS_DELETED
                    )
                continue
            if remote.get("deleted"):
                self.state.update_task_observation(task_record["task_id"], status=TASK_STATUS_DELETED)
                continue
            is_active = remote.get("status") == Task.STATUS_ACTIVE
            status = TASK_STATUS_ACTIVE if is_active else TASK_STATUS_CLOSED
            comment_count = remote.get("commentCount", 0)
            etag = remote.get("etag")
            changed = (
                task_record["last_comment_count"] is None
                or task_record["last_comment_count"] != comment_count
                or task_record["last_etag"] != etag
            )
            if is_active and changed:
                comments = self.dida.get_task_comments(
                    task_record["project_id"], task_record["task_id"]
                )
                self.state.record_remote_comments(
                    task_record["task_id"], task_record["project_id"], comments
                )
            self.state.update_task_observation(
                task_record["task_id"],
                status=status,
                comment_count=comment_count,
                etag=etag,
            )

    def _creating_recovery_expired(self, task_record):
        created_at = datetime.fromisoformat(task_record["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=PRACTICE_TIMEZONE)
        return now.astimezone(timezone.utc) - created_at.astimezone(
            timezone.utc
        ) >= CREATING_RECOVERY_GRACE

    def _run_claimed_action(self, interaction):
        if interaction["status"] == INTERACTION_STATUS_PROCESSING:
            self._ask_ai(interaction)
        elif interaction["status"] == INTERACTION_STATUS_SENDING_CLARIFICATION:
            self._send_clarification(interaction)
        elif interaction["status"] == INTERACTION_STATUS_APPLYING_BODY:
            self._apply_body(interaction)
        elif interaction["status"] == INTERACTION_STATUS_DELETING_COMMENTS:
            self._delete_comments(interaction)
        else:
            raise SentencePracticeError(f"无法处理交互状态：{interaction['status']}")

    def _ask_ai(self, interaction):
        task_record = self.state.get_task_by_id(interaction["task_id"])
        remote = self.dida.get_task(interaction["task_id"])
        if not remote or remote.get("deleted"):
            raise SentencePracticeError("待处理的每日造句任务已经被删除")
        groups = json.loads(task_record["groups_json"])
        comments = self.state.get_interaction_comments(interaction["id"])
        group_context = "\n".join(
            f"第 {group['group_id']} 组：{', '.join(group['words'])}" for group in groups
        )
        conversation = "\n\n".join(
            f"{comment['role']}：{_strip_system_marker(comment['title'])}"
            for comment in comments
        )
        user_prompt = "\n".join(
            [
                "请分析下面这次自由格式互动。",
                "",
                "【分组】",
                group_context,
                "",
                "【当前任务正文】",
                remote.get("content") or "",
                "",
                "【本次互动】",
                conversation,
            ]
        )
        raw_response = self.doubao.chat(
            user_prompt,
            system_message=SYSTEM_SENTENCE_PRACTICE_REVIEWER,
        )
        ai_result = parse_ai_review(
            raw_response,
            {group["group_id"] for group in groups},
        )
        if ai_result["action"] == "clarify":
            latest_user_comment = next(
                comment
                for comment in reversed(comments)
                if comment["role"] in {COMMENT_ROLE_SOURCE, COMMENT_ROLE_USER_FOLLOWUP}
            )
            self.state.prepare_clarification(
                interaction["id"],
                uuid.uuid4().hex[:24],
                ai_result["clarification_question"],
                latest_user_comment["comment_id"],
            )
            return
        self.state.prepare_body_update(interaction["id"], ai_result)

    def _send_clarification(self, interaction):
        comments = self.state.get_interaction_comments(interaction["id"])
        clarification = next(
            comment
            for comment in reversed(comments)
            if comment["role"] == COMMENT_ROLE_SYSTEM_CLARIFICATION
            and not comment["remote_deleted"]
        )
        remote_comments = self.dida.get_task_comments(
            interaction["project_id"], interaction["task_id"]
        )
        if not any(comment.get("id") == clarification["comment_id"] for comment in remote_comments):
            self.dida.create_task_comment(
                interaction["project_id"],
                interaction["task_id"],
                clarification["title"],
                comment_id=clarification["comment_id"],
                reply_comment_id=clarification["reply_comment_id"],
            )
            remote_comments = self.dida.get_task_comments(
                interaction["project_id"], interaction["task_id"]
            )
        if not any(comment.get("id") == clarification["comment_id"] for comment in remote_comments):
            raise SentencePracticeError("澄清评论发送后未能回读")
        self.state.set_interaction_status(
            interaction["id"], INTERACTION_STATUS_AWAITING_CLARIFICATION
        )

    def _apply_body(self, interaction):
        current = self.state.get_interaction(interaction["id"])
        ai_result = json.loads(current["ai_result_json"])
        comments = self.state.get_interaction_comments(interaction["id"])
        remote = self.dida.get_task(interaction["task_id"])
        if not remote or remote.get("deleted"):
            raise SentencePracticeError("写入正文时任务已经被删除")
        current_content = remote.get("content") or ""
        updated_content = append_review_to_body(
            current_content,
            current,
            comments,
            ai_result,
        )
        if updated_content.replace("\r\n", "\n") != current_content.replace("\r\n", "\n"):
            update_dict = deepcopy(remote)
            update_dict[Task.CONTENT] = updated_content
            response = self.dida.post_task(Task.gen_update_data_payload(update_dict))
            if isinstance(response, dict):
                error = (response.get("id2error") or {}).get(interaction["task_id"])
                if error:
                    raise SentencePracticeError(f"滴答拒绝正文更新：{error}")
            verified = self.dida.get_task(interaction["task_id"])
            verified_content = (verified or {}).get("content") or ""
            if verified_content.replace("\r\n", "\n") != updated_content.replace("\r\n", "\n"):
                raise SentencePracticeError("正文更新后回读内容不一致")
        self.state.set_interaction_status(
            interaction["id"], INTERACTION_STATUS_BODY_APPLIED
        )

    def _delete_comments(self, interaction):
        comments = self.state.get_interaction_comments(interaction["id"])
        remote_comments = self.dida.get_task_comments(
            interaction["project_id"], interaction["task_id"]
        )
        remote_ids = {comment.get("id") for comment in remote_comments}
        relevant_ids = {comment["comment_id"] for comment in comments}
        parent_by_id = {
            comment["comment_id"]: comment["reply_comment_id"] for comment in comments
        }

        def depth(comment_id):
            value = 0
            seen = set()
            parent = parent_by_id.get(comment_id)
            while parent in relevant_ids and parent not in seen:
                seen.add(parent)
                value += 1
                parent = parent_by_id.get(parent)
            return value

        ordered_comments = sorted(
            comments,
            key=lambda comment: (depth(comment["comment_id"]), comment["created_at"]),
            reverse=True,
        )
        for comment in ordered_comments:
            comment_id = comment["comment_id"]
            if comment_id in remote_ids:
                self.dida.delete_task_comment(
                    interaction["project_id"], interaction["task_id"], comment_id
                )
            self.state.mark_comment_deleted(comment_id)

        remaining = self.dida.get_task_comments(
            interaction["project_id"], interaction["task_id"]
        )
        remaining_ids = {comment.get("id") for comment in remaining}
        if relevant_ids & remaining_ids:
            raise SentencePracticeError("评论清理后仍有交互评论残留")
        self.state.set_interaction_status(interaction["id"], INTERACTION_STATUS_DONE)
        task = self.dida.get_task(interaction["task_id"])
        if task:
            self.state.update_task_observation(
                interaction["task_id"],
                comment_count=task.get("commentCount", len(remaining)),
                etag=task.get("etag"),
            )
