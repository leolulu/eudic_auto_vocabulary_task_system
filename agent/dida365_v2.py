import json

from dida365_project.main import Dida365 as ComprehensiveDida365


class Dida365(ComprehensiveDida365):
    def __init__(self, username, password, quick_scan_closed_task=False) -> None:
        self.username, self.password = username, password
        super().__init__(quick_scan_closed_task)

    def login(self):
        url = self.base_url + "/user/signon?wc=true&remember=true"
        data = json.dumps({"username": self.username, "password": self.password})
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()
