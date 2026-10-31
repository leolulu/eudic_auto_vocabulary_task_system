import requests
from utils.logging_util import logged_request

from constants.doubao import PAYLOAD_SYSTEM_MESSAGE, PAYLOAD_USER_MESSAGE


DOUBAO_REQUEST_TIMEOUT = (5, 600)


class DoubaoOnline:
    def __init__(self, endpoint, system_message=None):
        self.endpoint = endpoint
        self.system_message = system_message

    def add_system_message(self, message):
        self.system_message = message

    def chat(self, user_message, system_message=None):
        if system_message:
            self.add_system_message(system_message)
        Payload = {
            PAYLOAD_SYSTEM_MESSAGE: self.system_message,
            PAYLOAD_USER_MESSAGE: user_message,
        }
        res = logged_request("ai", requests.post, "POST", self.endpoint, json=Payload, timeout=DOUBAO_REQUEST_TIMEOUT)
        res.raise_for_status()
        if not res.text.strip():
            raise ValueError("AI 返回内容为空，本轮处理失败。")
        return res.text
