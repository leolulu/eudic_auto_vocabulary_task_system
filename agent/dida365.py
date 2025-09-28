import io
import re
from time import sleep
from typing import List, Optional, Tuple

import requests

from constants.dida365 import PROJECT_WORDS, VOCAB_BOOK_PROJECT_ID
from constants.header import HEADER_CHROME_UA
from dida365_project.api.dida365 import Dida365
from dida365_project.models.task import Task
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import get_dictvoice_bytes
from dida365_project.utils.time_util import get_days_offset, get_today_arrow
from utils.phonetic_util import query_word_explanation_video


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
        tags: Optional[list] = None,
        parent_id: Optional[str] = None,
    ):
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
        # Upload attachment
        task = self.find_task(title, if_reload_data=True)
        self._gen_dictvoice_and_upload_to_task_and_rearrange_content(task)

    def get_attachment_file_strings_from_task(self, task: Task) -> Optional[List[str]]:
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

    def rearrange_content_put_dictvoice_ahead(self, title):
        def rearrange_content(task, file_strings):
            new_content = re.sub(uploadAttachment.FILE_PATTERN, "", task.content).strip()
            new_content = "\n".join(file_strings + ["", new_content])  # ""是为了产生一个空行，如果使用"\n"的话，则会导致产生两个空行
            task.update_content(new_content)
            self.dida.post_task(Task.gen_update_data_payload(task.task_dict))

        print("Begin to rearrange content to put dictvoice ahead.")
        task = self.find_task(title, if_reload_data=True)
        file_strings = self.get_attachment_file_strings_from_task(task)
        if file_strings:
            try:
                rearrange_content(task, file_strings)
                print("Content rearranged, put dictvoice ahead.")
            except Exception as e:
                print(f"Error occurred when rearranging content: {e}")
        else:
            print("Can't find attachments, content not rearranged.")

    def update_task(self, task_dict):
        self.dida.post_task(Task.gen_update_data_payload(task_dict))

    def adjust_task_parent(self, task_name_to_parent_name: List[Tuple[str, str]]):
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

    def search(self, keyword: str, project_id=None) -> List[Task]:
        result = self.dida.search(keyword)
        tasks = [Task(t) for t in result["tasks"]]
        active_tasks = [t for t in tasks if t.status == Task.STATUS_ACTIVE]
        if project_id:
            active_tasks = [t for t in active_tasks if t.project_id == project_id]
        return active_tasks

    def _get_task_attachments_bytes(self, word: str) -> list[tuple]:
        """获取任务附件的字节数据（语音和视频）"""

        def download_video(url: str) -> Optional[Tuple[str, io.BytesIO]]:
            """下载视频并返回文件名和字节流"""
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    headers = {"User-Agent": HEADER_CHROME_UA}
                    response = requests.get(url, headers=headers)
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
        voice_files = get_dictvoice_bytes(word)
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

    def _gen_dictvoice_and_upload_to_task_and_rearrange_content(self, task: Task):
        # 使用新的语音+视频处理函数
        file_bytes_objs = self._get_task_attachments_bytes(task.title)
        task.add_upload_attachment_post_payload_by_bytes(*file_bytes_objs)
        self.dida.upload_attachment(*task.attachments_to_upload)
        self.rearrange_content_put_dictvoice_ahead(task.title)

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
