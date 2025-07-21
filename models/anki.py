from dataclasses import dataclass


@dataclass
class UserQuery:
    id: int
    query: str
    note_content: str
