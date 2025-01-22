from typing import Optional
from volcenginesdkarkruntime import Ark


class Doubao:
    def __init__(self, api_key, access_point, system_message: Optional[str] = None):
        self.access_point = access_point
        self.system_message = system_message
        self.client = Ark(
            api_key=api_key,
        )
        self.preserve_messages = []

    def construct_user_message(self, message):
        return {"role": "user", "content": message}

    def construct_assistant_message(self, message):
        return {"role": "assistant", "content": message}

    def construct_system_message(self, message):
        return {"role": "system", "content": message}

    @property
    def messages(self):
        return self.messages_system_part + self.preserve_messages

    @property
    def messages_system_part(self):
        if self.system_message:
            return [self.construct_system_message(self.system_message)]
        else:
            return []

    def chat(self, message, preserve_history=False):
        self.preserve_messages.append(self.construct_user_message(message))

        if preserve_history:
            messages = self.messages
        else:
            messages = self.messages_system_part + [self.construct_user_message(message)]

        completion = self.client.chat.completions.create(
            model=self.access_point,
            messages=messages,
        )

        response_content = completion.choices[0].message.content
        self.preserve_messages.append(self.construct_assistant_message(response_content))
        return response_content
