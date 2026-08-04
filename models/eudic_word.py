from datetime import datetime

from utils.datetime_util import is_last_x_days_range, parse_eudic_api_time


class Word:
    def __init__(self, data_dict: dict) -> None:
        self.word = data_dict["word"]
        self.explanation = data_dict["exp"]
        self.note: str | None = data_dict.get("note")
        self.add_datetime: datetime = self._fix_timezone(data_dict["add_time"])

    def __repr__(self):
        return f"{self.word} <- {self.add_datetime}"

    def is_in_last_days_range(self, days: int):
        if not self.add_datetime:
            return False
        else:
            return is_last_x_days_range(self.add_datetime, days)

    def _fix_timezone(self, misleading_timestamp_str: str) -> datetime:
        """
        因为欧陆词典返回的不是UTC时间，而是美区时间（UTC-8），所以这里需要修正一下
        """
        return parse_eudic_api_time(misleading_timestamp_str)

    @property
    def is_last_24h_range(self):
        return self.is_in_last_days_range(1)
