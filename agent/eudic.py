from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from constants.eudic import DEFAULT_VOCAB_BOOK_NAME, GET_WORD_URL, VOCAB_BOOK_BASE_URL, VOCAB_BOOK_ID, VOCAB_BOOK_NAME
from constants.header import HEADER_AUTHORIZATION, HEADER_USER_AGENT
from models.eudic_word import Word


class Eudic:
    def __init__(self, api_key) -> None:
        self.api_key = api_key
        self.headers = {
            HEADER_AUTHORIZATION: self.api_key,
            HEADER_USER_AGENT: "",  # 不带这一项会报错，值随便填，这里留空
        }

    def get_vocab_book(self):
        url = VOCAB_BOOK_BASE_URL
        params = {
            "language": "en",
        }
        res = requests.get(url, headers=self.headers, params=params)
        try:
            res.raise_for_status()
        except:
            print(res.text)
            raise
        return res.json()

    def get_default_vocab_book_id(self):
        data = self.get_vocab_book()["data"]
        for book_info in data:
            if book_info[VOCAB_BOOK_NAME] == DEFAULT_VOCAB_BOOK_NAME:
                return book_info[VOCAB_BOOK_ID]
        raise UserWarning(f"未找到默认生词本，请检查原始数据：{data}")

    def _parse_api_time(self, timestamp_str: str) -> datetime:
        """解析API返回的时间字符串，与Word._fix_timezone保持一致"""
        naive_time_str = timestamp_str.rstrip("Z")
        naive_dt = datetime.fromisoformat(naive_time_str)
        source_tz = ZoneInfo("Etc/GMT+8")
        correct_source_dt = naive_dt.replace(tzinfo=source_tz)
        beijing_tz = ZoneInfo("Asia/Shanghai")
        return correct_source_dt.astimezone(beijing_tz)

    def _fetch_page(self, vocab_book_id: str, page: int, page_size: int) -> list[dict]:
        """获取单页单词数据"""
        url = GET_WORD_URL
        params = {
            "language": "en",
            "category_id": vocab_book_id,
            "page": page,
            "page_size": page_size,
        }
        res = requests.get(url, headers=self.headers, params=params)
        try:
            res.raise_for_status()
        except:
            print(res.content)
            raise
        return res.json().get("data", [])

    def _find_last_page(self, vocab_book_id: str, page_size: int = 100) -> int:
        """二分查找最后一页（API限制最大page=50）"""
        low, high = 0, 50
        last_valid = -1

        while low <= high:
            mid = (low + high) // 2
            words = self._fetch_page(vocab_book_id, mid, page_size)
            if words:
                last_valid = mid
                low = mid + 1
            else:
                high = mid - 1

        return last_valid

    def get_words_in_book(self, vocab_book_id=None, days=1):
        book_id = vocab_book_id or self.get_default_vocab_book_id()
        page_size = 100

        # 1. 二分查找最后一页
        last_page = self._find_last_page(book_id, page_size)
        if last_page < 0:
            return []

        # 2. 计算截止时间（days天前的北京时间）
        now_beijing = datetime.now(ZoneInfo("Asia/Shanghai"))
        cutoff = now_beijing - timedelta(days=days)

        # 3. 从后往前取页，直到遇到超时的页
        all_data = []
        for page in range(last_page, -1, -1):
            words = self._fetch_page(book_id, page, page_size)
            if not words:
                continue

            # 该页最晚的单词（索引-1，页内升序）
            latest_time = self._parse_api_time(words[-1]["add_time"])

            # 如果这页最晚的都超出范围，前面更旧的页也不需要了
            if latest_time < cutoff:
                break

            all_data.extend(words)

        # 4. 转换为Word对象（精确过滤由调用方acquire_words处理）
        return [Word(w) for w in all_data]
