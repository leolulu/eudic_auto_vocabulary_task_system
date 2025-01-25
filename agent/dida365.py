import re
from time import sleep
from typing import List, Optional, Tuple

from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from dida365_project.api.dida365 import Dida365
from dida365_project.models.task import Task
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import get_dictvoice_bytes


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
        new_task_dict = self.find_task("模板任务一").task_dict
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
        task.add_upload_attachment_post_payload_by_bytes(*get_dictvoice_bytes(title))
        self.dida.upload_attachment(*task.attachments_to_upload)
        # put dictvoice ahead
        self.rearrange_content_put_dictvoice_ahead(title)

    def rearrange_content_put_dictvoice_ahead(self, title):
        def rearrange_content(content, task, file_strings):
            new_content = re.sub(uploadAttachment.FILE_PATTERN, "", content).strip()
            new_content = "\n".join([new_content, "\n"] + file_strings)
            task.update_content(new_content)
            self.dida.post_task(Task.gen_update_data_payload(task.task_dict))

        print("Begin to rearrange content to put dictvoice behind.")
        n = 0
        max_retry_times = 30
        while n < max_retry_times:
            task = self.find_task(title, if_reload_data=True)
            content = task.content
            attachments = task.attachments
            if re.search(uploadAttachment.FILE_PATTERN, content):
                file_strings = re.findall(uploadAttachment.FILE_PATTERN, content)
                rearrange_content(content, task, file_strings)
                break
            elif attachments:
                file_strings = [i.content_file_string for i in attachments]
                rearrange_content(content, task, file_strings)
                break
            else:
                n += 1
                print(f"Searching for {n} times.")
                sleep(5)
        if n >= max_retry_times:
            print("Can't find attachments, content not rearranged.\n")
        else:
            print("Content rearranged, put dictvoice behind.\n")

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

    def search(self, keyword: str):
        result = self.dida.search(keyword)
        tasks = [Task(t) for t in result["tasks"]]
        active_tasks = [t for t in tasks if t.status == Task.STATUS_ACTIVE]
        return active_tasks
