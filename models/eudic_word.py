from typing import Dict

from utils.datetime_util import is_last_24h_range


class Word:
    def __init__(self, data_dict: Dict) -> None:
        self.word = data_dict["word"]
        self.explanation = data_dict["exp"]
        self.add_datetime = data_dict["add_time"]

    def __repr__(self):
        return f"{self.word} <- {self.add_datetime}"

    def is_in_last_days_range(self, days: int):
        if not self.add_datetime:
            return False
        else:
            return is_last_24h_range(self.add_datetime, days)

    @property
    def is_last_24h_range(self):
        return self.is_in_last_days_range(1)
