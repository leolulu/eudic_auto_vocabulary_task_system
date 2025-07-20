import requests

from models.anki import UserQuery


class AnkiClient:
    def __init__(self, endpoint_url) -> None:
        self.url = endpoint_url

    def search_user_query(self):
        endpoint = f"{self.url}/search_user_query"
        res = requests.get(endpoint)
        res.raise_for_status()
        return [UserQuery(**i) for i in res.json()]

    def update_note_fields(self, note_id, field_and_contents: dict[str, str]):
        endpoint = f"{self.url}/update_note_fields"
        res = requests.post(endpoint, json={"note_id": note_id, "field_and_contents": field_and_contents})
        res.raise_for_status()
        return res.json()

    def add_note(self, word: str):
        endpoint = f"{self.url}/add_note"
        res = requests.post(endpoint, json={"word": word})
        res.raise_for_status()
        return res.json()

    def search_note_existence(self, title: str) -> bool:
        endpoint = f"{self.url}/search_note_title"
        res = requests.get(endpoint, params={"title": title})
        res.raise_for_status()
        return res.json().get("found")
