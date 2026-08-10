import io
import re
from time import sleep



import requests

from constants.dida365 import PROJECT_WORDS, VOCAB_BOOK_PROJECT_ID
from constants.eudic import EUDIC_NOTE_IMAGES_PLACEHOLDER
from constants.header import HEADER_CHROME_UA
from dida365_project.api.dida365 import Dida365
from dida365_project.models.task import Task
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import get_dictvoice_bytes
from dida365_project.utils.time_util import get_days_offset, get_today_arrow
from utils.phonetic_util import query_word_explanation_video


MEDIA_DOWNLOAD_TIMEOUT = (5, 60)


class Dida365Agent:
    def __init__(self, dida365_api: Dida365) -> None:
        self.dida = dida365_api

    def find_task(self, task_title, if_reload_data=False):
        if if_reload_data:
            self.dida.get_latest_data()
        tasks = [i for i in self.dida.active_tasks if i.title == task_title]
        if len(tasks) != 1:
            raise UserWarning(f"Task with title[{task_title}] duplicated, count: {len(tasks)}")
        return tasks[0]

    def add_task(
        self,
        title,
        content,
        project_id=VOCAB_BOOK_PROJECT_ID,
        tags: list | None = None,
        parent_id: str | None = None,
        note_image_files: list[tuple[str, io.BytesIO]] | None = None,
    ):
        note_image_files = note_image_files or []
        note_image_names = [filename.lower() for filename, _ in note_image_files]
        # 失败后的下一轮必须看到服务端已经创建的半成品任务，避免重复创建同名单词。
        self.dida.get_latest_data()
        existing_tasks = [task for task in self.dida.active_tasks if task.title == title]
        if existing_tasks:
            if len(existing_tasks) != 1:
                raise UserWarning(f"Task with title[{title}] duplicated, count: {len(existing_tasks)}")
            task = existing_tasks[0]
            if note_image_names and self._note_images_are_complete(task, note_image_names):
                return task
            if EUDIC_NOTE_IMAGES_PLACEHOLDER not in (task.content or ""):
                raise UserWarning(f"Task with title[{title}] already exists and cannot be resumed")
            print(f"发现未完成的欧路笔记图片任务 [{title}]，继续同步。")
        else:
            template_task = self.find_task("模板任务一")
            template_task.change_start_date_to_today()
            new_task_dict = template_task.task_dict
            new_task_dict[Task.PROJECT_ID] = project_id
            new_task_dict[Task.TITLE] = title
            new_task_dict[Task.CONTENT] = content
            new_task_dict[Task.ID] += "z"
            if tags:
                new_task_dict[Task.TAGS] = tags
            if parent_id:
                new_task_dict[Task.PARENT_ID] = parent_id
            self.dida.post_task(Task.gen_add_data_payload(new_task_dict))
            task = self.find_task(title, if_reload_data=True)

        self._gen_dictvoice_and_upload_to_task_and_rearrange_content(
            task,
            note_image_files=note_image_files,
        )
        return self.find_task(title, if_reload_data=True)

    @staticmethod
    def _note_images_are_complete(task: Task, note_image_names: list[str]) -> bool:
        attachments_by_name = {
            attachment.file_name.lower(): attachment
            for attachment in task.attachments
        }
        if any(name not in attachments_by_name for name in note_image_names):
            return False
        return all(
            attachments_by_name[name].content_file_string in (task.content or "")
            for name in note_image_names
        )

    def get_attachment_file_strings_from_task(self, task: Task) -> list[str] | None:
        n = 0
        max_retry_times = 2
        while n < max_retry_times:
            content = task.content
            attachments = task.attachments
            if re.search(uploadAttachment.FILE_PATTERN, content):
                file_strings = re.findall(uploadAttachment.FILE_PATTERN, content)
                return file_strings
            elif attachments:
                file_strings = [i.content_file_string for i in attachments]
                return file_strings
            else:
                n += 1
                sleep(5)
        return None

    def rearrange_content_put_dictvoice_ahead(
        self,
        title,
        note_image_names: list[str] | None = None,
        expected_attachment_names: list[str] | None = None,
    ):
        note_image_names = [name.lower() for name in (note_image_names or [])]
        expected_attachment_names = [
            name.lower()
            for name in (expected_attachment_names or note_image_names)
        ]

        def rearrange_content(task, leading_file_strings, note_image_file_strings):
            new_content = re.sub(uploadAttachment.FILE_PATTERN, "", task.content).strip()
            if note_image_names:
                if EUDIC_NOTE_IMAGES_PLACEHOLDER not in new_content:
                    raise RuntimeError("任务正文缺少欧路笔记图片占位符")
                new_content = new_content.replace(
                    EUDIC_NOTE_IMAGES_PLACEHOLDER,
                    "\n".join(note_image_file_strings),
                    1,
                )
            if leading_file_strings:
                new_content = "\n".join(leading_file_strings + ["", new_content])
            task.update_content(new_content)
            self.dida.post_task(Task.gen_update_data_payload(task.task_dict))

        print("Begin to rearrange content to put dictvoice ahead.")
        task = None
        for attempt in range(3):
            task = self.find_task(title, if_reload_data=True)
            attachments_by_name = {
                attachment.file_name.lower(): attachment
                for attachment in task.attachments
            }
            if expected_attachment_names:
                attachments_ready = all(
                    name in attachments_by_name
                    for name in expected_attachment_names
                )
            else:
                attachments_ready = bool(task.attachments)
            if attachments_ready:
                break
            if attempt < 2:
                sleep(2)
        if task is None:
            raise RuntimeError(f"无法重新读取任务 [{title}]")

        attachments_by_name = {
            attachment.file_name.lower(): attachment
            for attachment in task.attachments
        }
        missing_attachments = [
            name
            for name in expected_attachment_names
            if name not in attachments_by_name
        ]
        if missing_attachments:
            raise RuntimeError(
                f"任务 [{title}] 未能确认附件上传成功：{', '.join(missing_attachments)}"
            )
        note_image_name_set = set(note_image_names)
        note_image_file_strings = [
            attachments_by_name[name].content_file_string
            for name in note_image_names
        ]
        leading_file_strings = [
            attachment.content_file_string
            for attachment in task.attachments
            if attachment.file_name.lower() not in note_image_name_set
        ]
        if leading_file_strings or note_image_file_strings:
            try:
                rearrange_content(task, leading_file_strings, note_image_file_strings)
                print("Content rearranged, put dictvoice ahead.")
            except Exception as e:
                print(f"Error occurred when rearranging content: {e}")
                raise
        else:
            print("Can't find attachments, content not rearranged.")

    def update_task(self, task_dict):
        self.dida.post_task(Task.gen_update_data_payload(task_dict))

    def deactivate_task_attachments(self, task_title: str, attachment_ids: list[str], if_reload_data=True):
        task = self.find_task(task_title, if_reload_data=if_reload_data)
        for attachment_id in attachment_ids:
            task.mark_attachment_inactive(attachment_id)
        self.dida.post_task(Task.gen_attachment_inactive_payload(task.task_dict))

    def adjust_task_parent(self, task_name_to_parent_name: list[tuple[str, str]]):
        payload = []
        for task_name, parent_name in task_name_to_parent_name:
            task = self.find_task(task_name)
            parent_task = self.find_task(parent_name)
            payload.append(
                {
                    "taskId": task.id,
                    "projectId": parent_task.project_id,
                    "parentId": parent_task.id,
                }
            )
        self.dida.adjust_task_parent(payload)

    def search(self, keyword: str, project_id=None) -> list[Task]:
        result = self.dida.search(keyword)
        tasks = [Task(t) for t in result["tasks"]]
        active_tasks = [t for t in tasks if t.status == Task.STATUS_ACTIVE]
        if project_id:
            active_tasks = [t for t in active_tasks if t.project_id == project_id]
        return active_tasks

    def _get_task_attachments_bytes(self, word: str) -> list[tuple]:
        """获取任务附件的字节数据（语音和视频）"""

        def download_video(url: str) -> tuple[str, io.BytesIO] | None:
            """下载视频并返回文件名和字节流"""
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    headers = {"User-Agent": HEADER_CHROME_UA}
                    response = requests.get(url, headers=headers, timeout=MEDIA_DOWNLOAD_TIMEOUT)
                    response.raise_for_status()

                    # 从URL提取文件名
                    filename = url.split("/")[-1]
                    if not re.search(r"\.\w+$", filename):
                        filename += ".mp4"

                    return (filename, io.BytesIO(response.content))
                except Exception as e:
                    print(f"下载视频失败 [URL: {url}] (尝试 {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:  # 不是最后一次尝试
                        sleep(2**attempt)  # 指数退避
                    else:
                        return None

        result = []

        # 添加语音文件
        try:
            voice_files = get_dictvoice_bytes(word)
        except requests.RequestException as error:
            # 部分罕见词会被词典语音接口以 5xx 拒绝；语音属于可选增强，
            # 不能因此阻断欧路 Note 图片等其他附件。
            print(f"警告: 单词 '{word}' 的发音音频下载失败，将继续处理其他附件：{error}")
        else:
            result.extend(voice_files)

        # 添加视频文件
        video_urls = query_word_explanation_video(word)
        if not video_urls:
            print(f"警告: 单词 '{word}' 无视频资源")
        else:
            for url in video_urls:
                video_file = download_video(url)
                if video_file:
                    print(f"✅ 视频下载成功: {video_file[0]}")
                    result.append(video_file)

        return result

    def _gen_dictvoice_and_upload_to_task_and_rearrange_content(
        self,
        task: Task,
        note_image_files: list[tuple[str, io.BytesIO]] | None = None,
    ):
        # 欧路图片与既有语音/视频走同一个滴答附件接口；按文件名跳过已上传项，
        # 使“附件成功、正文更新失败”的任务可以在下一轮安全接续。
        note_image_files = note_image_files or []
        file_bytes_objs = [*note_image_files, *self._get_task_attachments_bytes(task.title)]
        existing_names = {attachment.file_name.lower() for attachment in task.attachments}
        missing_files = [
            file_bytes_obj
            for file_bytes_obj in file_bytes_objs
            if file_bytes_obj[0].lower() not in existing_names
        ]
        task.add_upload_attachment_post_payload_by_bytes(*missing_files)
        if task.attachments_to_upload:
            self.dida.upload_attachment(*task.attachments_to_upload)
        self.rearrange_content_put_dictvoice_ahead(
            task.title,
            note_image_names=[filename for filename, _ in note_image_files],
            expected_attachment_names=[filename for filename, _ in file_bytes_objs],
        )

    def fix_pronunciation_missing(self):
        self.dida.get_latest_data()
        active_task_in_vocab_book = [t for t in self.dida.active_tasks if t.project_id == VOCAB_BOOK_PROJECT_ID]
        for task in active_task_in_vocab_book:
            if not self.get_attachment_file_strings_from_task(task):
                print(f'Found task which missing pronunciation: "{task.title}", begin to fix.')
                self._gen_dictvoice_and_upload_to_task_and_rearrange_content(task)
                print(f'"{task.title}"\'s missing problem has been fixed.')

    def _get_target_words_task(self, start_day_offset):
        def condition(task: Task):
            return (
                task.repeat_flag
                and task.start_date
                and re.search(r".*FORGETTINGCURVE.*", task.repeat_flag)
                and get_days_offset(task.start_date, get_today_arrow()) == start_day_offset
            )

        tasks = filter(
            lambda task: re.search(r".*" + PROJECT_WORDS.decode("utf-8") + r"$", str(task.project_name)),
            self.dida.active_tasks,
        )
        tasks = filter(lambda task: condition(task), tasks)
        return list(tasks)

    def renew_overdue_task(self):
        overdue_tasks: list[Task] = []
        for i in range(3):
            i = -(i + 1)
            overdue_tasks.extend(self._get_target_words_task(i))
        for task in overdue_tasks:
            print(f"Renew task[{task.title}], original start date: {task.start_date}")
            task.change_start_date_to_today()
            self.dida.post_task(Task.gen_update_data_payload(task.task_dict))
