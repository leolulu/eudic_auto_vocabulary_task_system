import hashlib
import io
import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

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


class EudicNoteImageDownloadError(RuntimeError):
    pass


EUDIC_NOTE_META_PREFIX = "<!--meta files"
EUDIC_NOTE_NBSP_PATTERN = re.compile(r"&(?:nbsp|#0*160|#x0*a0);", re.IGNORECASE)
EUDIC_REQUEST_TIMEOUT = (5, 30)
EUDIC_IMAGE_DOWNLOAD_TIMEOUT = (5, 60)
EUDIC_IMAGE_MAX_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class EudicNoteImage:
    image_id: str
    url: str
    original_filename: str | None = None


@dataclass(frozen=True)
class EudicNoteData:
    text: str
    images: tuple[EudicNoteImage, ...] = ()


def split_eudic_note_metadata(note: str) -> tuple[dict | None, str]:
    """拆分欧路 App 写入的 meta files 注释和用户正文。

    图片列表位于这个私有元数据中。调用方若调整解析规则，必须同时检查
    ``Eudic.get_note_data`` 和滴答附件同步链路，避免再次只保留正文而丢图。
    """
    stripped_note = note.strip()
    if not stripped_note.startswith(EUDIC_NOTE_META_PREFIX):
        return None, stripped_note

    metadata_start = len(EUDIC_NOTE_META_PREFIX)
    if metadata_start >= len(stripped_note) or not stripped_note[metadata_start].isspace():
        return None, stripped_note
    while metadata_start < len(stripped_note) and stripped_note[metadata_start].isspace():
        metadata_start += 1

    try:
        metadata, metadata_end = json.JSONDecoder().raw_decode(stripped_note, metadata_start)
    except json.JSONDecodeError:
        return None, stripped_note
    if not isinstance(metadata, dict):
        return None, stripped_note

    comment_end = metadata_end
    while comment_end < len(stripped_note) and stripped_note[comment_end].isspace():
        comment_end += 1
    if not stripped_note.startswith("-->", comment_end):
        return None, stripped_note
    return metadata, stripped_note[comment_end + 3 :].strip()


def strip_eudic_note_metadata(note: str) -> str:
    return split_eudic_note_metadata(note)[1]


def parse_eudic_note(note: str) -> EudicNoteData:
    metadata, text = split_eudic_note_metadata(note)
    text = EUDIC_NOTE_NBSP_PATTERN.sub(" ", text).replace("\u00a0", " ").strip()
    if metadata is None:
        return EudicNoteData(text=text)

    raw_images = metadata.get("image_list") or []
    if not isinstance(raw_images, list):
        raise ValueError("image_list 不是列表")

    images = []
    for index, raw_image in enumerate(raw_images, start=1):
        if not isinstance(raw_image, dict):
            raise ValueError(f"第 {index} 个图片元数据不是对象")
        media_type = raw_image.get("type")
        if media_type not in (None, "image"):
            continue
        url = raw_image.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"第 {index} 个图片缺少下载地址")
        image_id = raw_image.get("id")
        if not isinstance(image_id, str) or not image_id.strip():
            image_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        original_filename = raw_image.get("orgfilename")
        if not isinstance(original_filename, str) or not original_filename.strip():
            original_filename = None
        images.append(
            EudicNoteImage(
                image_id=image_id.strip(),
                url=url.strip(),
                original_filename=original_filename,
            )
        )
    return EudicNoteData(text=text, images=tuple(images))


def normalize_eudic_note(note: str) -> str:
    return parse_eudic_note(note).text


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

    def get_note_data(self, word: str) -> EudicNoteData | None:
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
        try:
            note_data = parse_eudic_note(note)
        except ValueError as error:
            raise EudicNoteFetchError(f"单词 [{word}] 的欧路笔记图片元数据异常") from error
        if not note_data.text and not note_data.images:
            return None
        return note_data

    def get_note(self, word: str) -> str | None:
        note_data = self.get_note_data(word)
        if note_data is None:
            return None
        return note_data.text or None

    @staticmethod
    def _validate_note_image_url(url: str) -> None:
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme != "https" or not (
            hostname == "frdic.com" or hostname.endswith(".frdic.com")
        ):
            raise EudicNoteImageDownloadError(
                f"拒绝从非欧路 HTTPS 地址下载笔记图片：{url}"
            )

    @staticmethod
    def _note_image_filename(image: EudicNoteImage, content_type: str, index: int) -> str:
        mime_type = content_type.partition(";")[0].strip().lower()
        extension = mimetypes.guess_extension(mime_type) or ""
        if extension == ".jpe":
            extension = ".jpg"
        if not extension:
            extension = ".jpg"
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", image.image_id)[:16] or "unknown"
        return f"eudic-note-{index:02d}-{safe_id}{extension}".lower()

    def download_note_images(
        self,
        images: tuple[EudicNoteImage, ...],
    ) -> list[tuple[str, io.BytesIO]]:
        downloaded_images = []
        for index, image in enumerate(images, start=1):
            self._validate_note_image_url(image.url)
            try:
                response = requests.get(
                    image.url,
                    headers=self.headers,
                    timeout=EUDIC_IMAGE_DOWNLOAD_TIMEOUT,
                )
                response.raise_for_status()
            except requests.RequestException as error:
                raise EudicNoteImageDownloadError(
                    f"下载欧路笔记图片 [{image.image_id}] 失败"
                ) from error

            content_type = response.headers.get("content-type", "")
            if not content_type.lower().startswith("image/"):
                raise EudicNoteImageDownloadError(
                    f"欧路笔记图片 [{image.image_id}] 返回了非图片内容：{content_type or '未知类型'}"
                )
            if not response.content:
                raise EudicNoteImageDownloadError(
                    f"欧路笔记图片 [{image.image_id}] 内容为空"
                )
            if len(response.content) > EUDIC_IMAGE_MAX_BYTES:
                raise EudicNoteImageDownloadError(
                    f"欧路笔记图片 [{image.image_id}] 超过 {EUDIC_IMAGE_MAX_BYTES // 1024 // 1024} MB 限制"
                )
            filename = self._note_image_filename(image, content_type, index)
            downloaded_images.append((filename, io.BytesIO(response.content)))
        return downloaded_images

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
