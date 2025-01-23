from datetime import datetime, timedelta


def is_last_24h_range(input_time_str: str, days: int = 1) -> bool:
    input_time = datetime.strptime(input_time_str, "%Y-%m-%dT%H:%M:%SZ")

    now = datetime.now()
    today_4am = datetime(now.year, now.month, now.day, 4, 0, 0)
    yesterday_4am = today_4am - timedelta(days=days)

    return yesterday_4am <= input_time < today_4am


def get_today_date_string():
    today = datetime.now()
    return f"{today.month}月{today.day}日"
