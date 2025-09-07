from datetime import datetime, timedelta, timezone


def is_last_x_days_range(input_time: datetime, days: int = 1) -> bool:
    now = datetime.now(timezone.utc)
    range_start = now - timedelta(days=days)
    return range_start < input_time


def get_today_date_string():
    today = datetime.now()
    return f"{today.month}月{today.day}日"
