from typing import Optional
import logging
import time
import uuid
from volcenginesdkarkruntime import Ark
from utils.logging_util import body_summary, log_context, log_event, register_secrets


class Doubao:
    def __init__(self, api_key, access_point, system_message: Optional[str] = None):
        register_secrets(api_key=api_key)
        self.access_point = access_point
        self.add_system_message(system_message)
        self.client = Ark(
            api_key=api_key,
        )
        self.preserve_messages = []

    def add_system_message(self, message):
        self.system_message = message

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

        started_at = time.monotonic()
        with log_context(request_id=uuid.uuid4().hex, model=self.access_point):
            log_event("ai", "sdk_request_started", request_body=body_summary(messages))
            try:
                completion = self.client.chat.completions.create(
                    model=self.access_point,
                    messages=messages,
                )
                response_content = completion.choices[0].message.content
            except Exception as error:
                log_event("ai", "sdk_request_failed", level=logging.ERROR, error=error,
                          status=getattr(error, "status_code", None),
                          duration_ms=round((time.monotonic() - started_at) * 1000, 1))
                raise
            log_event("ai", "sdk_response_received", completion_id=getattr(completion, "id", None),
                      finish_reason=getattr(completion.choices[0], "finish_reason", None),
                      response_body=body_summary(response_content),
                      duration_ms=round((time.monotonic() - started_at) * 1000, 1))
        self.preserve_messages.append(self.construct_assistant_message(response_content))
        return response_content
