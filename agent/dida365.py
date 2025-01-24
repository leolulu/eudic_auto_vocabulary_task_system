import json
from typing import List, Optional, Tuple
import uuid
from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from dida365_project.api.dida365 import Dida365
from dida365_project.models.task import Task


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
