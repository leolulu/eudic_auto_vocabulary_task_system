from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_eudic_api_time(timestamp_str: str) -> datetime:
    """欧路时间带有 Z 后缀，但实际按 UTC-8 记录；统一修正为北京时间。"""
    naive_time_str = timestamp_str.rstrip("Z")
    naive_dt = datetime.fromisoformat(naive_time_str)
    source_tz = ZoneInfo("Etc/GMT+8")
    correct_source_dt = naive_dt.replace(tzinfo=source_tz)
    return correct_source_dt.astimezone(ZoneInfo("Asia/Shanghai"))


def is_last_x_days_range(input_time: datetime, days: int = 1) -> bool:
    now = datetime.now(timezone.utc)
    range_start = now - timedelta(days=days)
    return range_start < input_time


def get_today_date_string():
    today = datetime.now()
    return f"{today.month}月{today.day}日"
