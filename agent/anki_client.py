import requests
from utils.logging_util import logged_request

from models.anki import UserQuery


class AnkiClient:
    def __init__(self, endpoint_url) -> None:
        self.url = endpoint_url

    def search_user_query(self):
        endpoint = f"{self.url}/search_user_query"
        res = logged_request("anki", requests.get, "GET", endpoint)
        res.raise_for_status()
        return [UserQuery(**i) for i in res.json()]

    def update_note_fields(self, note_id, field_and_contents: dict[str, str]):
        endpoint = f"{self.url}/update_note_fields"
        res = logged_request("anki", requests.post, "POST", endpoint, json={"note_id": note_id, "field_and_contents": field_and_contents})
        res.raise_for_status()
        return res.json()

    def add_note(self, word: str, content: str):
        endpoint = f"{self.url}/add_note"
        res = logged_request("anki", requests.post, "POST", endpoint, json={"word": word, "content": content})
        res.raise_for_status()
        return res.json()

    def search_note_existence(self, title: str) -> bool:
        endpoint = f"{self.url}/search_note_title"
        res = logged_request("anki", requests.get, "GET", endpoint, params={"title": title})
        res.raise_for_status()
        return res.json().get("found")

    def request_anki_to_sync(self):
        endpoint = f"{self.url}/sync_anki"
        res = logged_request("anki", requests.get, "GET", endpoint)
        res.raise_for_status()
        return res.json()
