import requests

from constants.doubao import PAYLOAD_SYSTEM_MESSAGE, PAYLOAD_USER_MESSAGE


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
        res = requests.post(self.endpoint, json=Payload)
        res.raise_for_status()
        return res.text
