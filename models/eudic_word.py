from datetime import datetime
from typing import Dict
from zoneinfo import ZoneInfo

from utils.datetime_util import is_last_x_days_range


class Word:
    def __init__(self, data_dict: Dict) -> None:
        self.word = data_dict["word"]
        self.explanation = data_dict["exp"]
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
        naive_time_str = misleading_timestamp_str.rstrip("Z")
        naive_dt = datetime.fromisoformat(naive_time_str)
        source_tz = ZoneInfo("Etc/GMT+8")
        correct_source_dt = naive_dt.replace(tzinfo=source_tz)
        beijing_tz = ZoneInfo("Asia/Shanghai")
        beijing_dt = correct_source_dt.astimezone(beijing_tz)
        return beijing_dt

    @property
    def is_last_24h_range(self):
        return self.is_in_last_days_range(1)
