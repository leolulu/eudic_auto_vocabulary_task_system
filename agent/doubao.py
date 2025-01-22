from volcenginesdkarkruntime import Ark


class Doubao:
    def __init__(self, api_key, access_point, system_message=None):
        self.access_point = access_point
        self.system_message = system_message
        self.client = Ark(
            api_key=api_key,
        )

    def chat(self, message):
        messages = []
        if self.system_message:
            messages.append({"role": "system", "content": self.system_message})
        messages.append({"role": "user", "content": message})

        completion = self.client.chat.completions.create(
            model=self.access_point,
            messages=messages,
        )

        return completion.choices[0].message.content  # type: ignore
