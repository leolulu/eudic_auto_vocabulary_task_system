import json
import re
from datetime import datetime, timedelta

import requests

from constants.eudic import (
    DEFAULT_VOCAB_BOOK_NAME,
    GET_NOTE_URL,
    GET_WORD_URL,
    VOCAB_BOOK_BASE_URL,
    VOCAB_BOOK_ID,
    VOCAB_BOOK_NAME,
    WORD_URL,
)
from constants.header import HEADER_AUTHORIZATION, HEADER_USER_AGENT
from models.eudic_word import Word
from utils.datetime_util import parse_eudic_api_time


class EudicNoteFetchError(RuntimeError):
    pass


class EudicWordFetchError(RuntimeError):
    pass


class EudicWriteError(RuntimeError):
    pass


EUDIC_NOTE_META_PREFIX = "<!--meta files"
EUDIC_NOTE_NBSP_PATTERN = re.compile(r"&(?:nbsp|#0*160|#x0*a0);", re.IGNORECASE)
EUDIC_REQUEST_TIMEOUT = (5, 30)


def strip_eudic_note_metadata(note: str) -> str:
    stripped_note = note.strip()
    if not stripped_note.startswith(EUDIC_NOTE_META_PREFIX):
        return stripped_note

    metadata_start = len(EUDIC_NOTE_META_PREFIX)
    if metadata_start >= len(stripped_note) or not stripped_note[metadata_start].isspace():
        return stripped_note
    while metadata_start < len(stripped_note) and stripped_note[metadata_start].isspace():
        metadata_start += 1

    try:
        metadata, metadata_end = json.JSONDecoder().raw_decode(stripped_note, metadata_start)
    except json.JSONDecodeError:
        return stripped_note
    if not isinstance(metadata, dict):
        return stripped_note

    comment_end = metadata_end
    while comment_end < len(stripped_note) and stripped_note[comment_end].isspace():
        comment_end += 1
    if not stripped_note.startswith("-->", comment_end):
        return stripped_note
    return stripped_note[comment_end + 3 :].strip()


def normalize_eudic_note(note: str) -> str:
    note = strip_eudic_note_metadata(note)
    # 欧路 App 编辑器会把普通词间空格序列化成不换行空格实体。
    # 这里只还原空格，避免通用 HTML 解码把其他实体变成会影响 Markdown 的字符。
    note = EUDIC_NOTE_NBSP_PATTERN.sub(" ", note).replace("\u00a0", " ")
    return note.strip()


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
        res = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=EUDIC_REQUEST_TIMEOUT,
        )
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

    def get_note(self, word: str) -> str | None:
        params = {
            "language": "en",
            "word": word,
        }
        try:
            res = requests.get(
                GET_NOTE_URL,
                headers=self.headers,
                params=params,
                timeout=EUDIC_REQUEST_TIMEOUT,
            )
            if res.status_code == 404:
                return None
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as error:
            raise EudicNoteFetchError(f"读取单词 [{word}] 的欧路笔记失败") from error

        if not isinstance(payload, dict):
            raise EudicNoteFetchError(f"单词 [{word}] 的欧路笔记响应格式异常")
        data = payload.get("data")
        if data is None:
            return None
        if not isinstance(data, dict):
            raise EudicNoteFetchError(f"单词 [{word}] 的欧路笔记响应格式异常")

        note = data.get("note")
        if note is None:
            return None
        if not isinstance(note, str):
            raise EudicNoteFetchError(f"单词 [{word}] 的欧路笔记格式异常")
        return normalize_eudic_note(note) or None

    def get_word(self, word: str) -> dict | None:
        params = {
            "language": "en",
            "word": word,
        }
        try:
            res = requests.get(
                WORD_URL,
                headers=self.headers,
                params=params,
                timeout=EUDIC_REQUEST_TIMEOUT,
            )
            if res.status_code == 404:
                return None
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as error:
            raise EudicWordFetchError(f"查询单词 [{word}] 的欧路生词记录失败") from error

        candidates = payload.get("data", payload) if isinstance(payload, dict) else payload
        if candidates is None:
            return None
        if isinstance(candidates, dict):
            candidates = [candidates]
        if not isinstance(candidates, list):
            raise EudicWordFetchError(f"单词 [{word}] 的欧路生词响应格式异常")

        normalized_word = word.strip().lower()
        for candidate in candidates:
            if isinstance(candidate, dict) and str(candidate.get("word") or "").strip().lower() == normalized_word:
                return candidate
        return None

    def save_note(self, word: str, note: str) -> None:
        try:
            res = requests.post(
                GET_NOTE_URL,
                headers=self.headers,
                json={
                    "language": "en",
                    "word": word,
                    "note": note,
                },
                timeout=EUDIC_REQUEST_TIMEOUT,
            )
            res.raise_for_status()
        except requests.RequestException as error:
            # 写请求的响应超时可能发生在服务端已经保存之后，由调用方通过 GET 对账。
            raise EudicWriteError(f"保存单词 [{word}] 的欧路笔记失败") from error

    def add_word(self, word: str) -> None:
        try:
            res = requests.post(
                WORD_URL,
                headers=self.headers,
                json={
                    "language": "en",
                    "word": word,
                },
                timeout=EUDIC_REQUEST_TIMEOUT,
            )
            res.raise_for_status()
        except requests.RequestException as error:
            # 不直接重试不确定的写请求，交给调用方先查询最终状态。
            raise EudicWriteError(f"添加单词 [{word}] 到欧路生词本失败") from error

    def _parse_api_time(self, timestamp_str: str) -> datetime:
        """解析API返回的时间字符串，与Word._fix_timezone保持一致"""
        return parse_eudic_api_time(timestamp_str)

    def _fetch_page(self, vocab_book_id: str, page: int, page_size: int) -> list[dict]:
        """获取单页单词数据"""
        url = GET_WORD_URL
        params = {
            "language": "en",
            "category_id": vocab_book_id,
            "page": page,
            "page_size": page_size,
        }
        res = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=EUDIC_REQUEST_TIMEOUT,
        )
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
