import copy
import json
import uuid
from io import BufferedReader

import requests
from retrying import retry

from ..models.project import Project
from ..models.task import Task
from ..models.upload_attachment import uploadAttachment


class Dida365:
    def __init__(self, username, password) -> None:
        self.session = requests.Session()
        self.base_url = "https://api.dida365.com/api/v2"
        self.headers = {
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
            "x-device": '{"platform":"web","os":"Windows 10","device":"Chrome 109.0.0.0","name":"","version":4411,"id":"63b0fb54363a786fba71cc80","channel":"website","campaign":"","websocket":""}',
        }
        self.login(username, password)
        self.get_latest_data()

    def login(self, username, password):
        url = self.base_url + "/user/signon?wc=true&remember=true"
        data = json.dumps({"username": username, "password": password})
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()

    def get_latest_data(self):
        self.get_data()
        self.enrich_info()

    def get_data(self):
        url = self.base_url + "/batch/check/0"
        r = self.session.get(url, headers=self.headers)
        r.raise_for_status()
        self.data = json.loads(r.content)
        self._get_projects()
        self._get_task()

    def search(self, keyword: str):
        url = self.base_url + "/search/all"
        params = {"keywords": keyword}
        r = self.session.get(url, headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def enrich_info(self):
        self._enrich_task_info()

    def _enrich_task_info(self):
        project_id_name_mapping = {p.id: p.name for p in self.projects}
        for task in self.active_tasks:
            task.project_name = project_id_name_mapping.get(task.project_id)

    def _get_task(self):
        tasks = self.data["syncTaskBean"]["update"]
        self.active_tasks = [Task(i) for i in tasks]

    def _get_projects(self):
        projects = self.data["projectProfiles"]
        self.projects = [Project(i) for i in projects]

    @retry(wait_fixed=4000, stop_max_attempt_number=5)
    def post_task(self, payload):
        url = self.base_url + "/batch/task"
        data = json.dumps(payload)
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()

    @retry(wait_fixed=4000, stop_max_attempt_number=5)
    def adjust_task_parent(self, payload):
        url = self.base_url + "/batch/taskParent"
        data = json.dumps(payload)
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()

    @retry(wait_fixed=60 * 1000, stop_max_attempt_number=30)
    def upload_attachment(self, *attachments: uploadAttachment):
        for attachment in attachments:
            url = "https://api.dida365.com/api/v1/attachment/upload/{project_id}/{task_id}/{uuid}".format(
                project_id=attachment.project_id, task_id=attachment.task_id, uuid=uuid.uuid1().hex
            )
            if attachment.file_bytes is not None:
                f = attachment.file_bytes
            elif attachment.file_path is not None:
                f = open(attachment.file_path, "rb")
            else:
                raise UserWarning(f"Attachment without neither file bytes nor file path!")
            files = [("file", (attachment.file_name, f, "application/octet-stream"))]
            headers = copy.copy(self.headers)
            headers.pop("content-type")
            try:
                r = self.session.request("POST", url, headers=headers, data={}, files=files)
                r.raise_for_status()
            except:
                raise
            finally:
                if isinstance(f, BufferedReader):
                    f.close()
