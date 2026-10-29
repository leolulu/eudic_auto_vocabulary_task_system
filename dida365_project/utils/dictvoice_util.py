import io
import logging
from enum import Enum, unique
from time import sleep

import requests
from utils.logging_util import log_event, logged_request


DICTVOICE_REQUEST_TIMEOUT = (5, 30)
VOICE_REQUEST_ATTEMPTS = 3
MAX_VOICE_BYTES = 2 * 1024 * 1024


class PronunciationError(requests.RequestException):
    pass


def _is_mp3(data):
    offset = 0
    if data.startswith(b"ID3"):
        if len(data) < 10 or any(value & 0x80 for value in data[6:10]):
            return False
        offset = 10 + sum(value << shift for value, shift in zip(data[6:10], (21, 14, 7, 0)))
        if data[3] == 4 and data[5] & 0x10:
            offset += 10
    header = data[offset:offset + 4]
    if len(header) != 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return False
    version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
    bitrate_index, rate_index = header[2] >> 4, (header[2] >> 2) & 3
    if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
        return False
    rates = (44100, 48000, 32000)
    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    sample_rate = rates[rate_index] // (1 if version == 3 else 2 if version == 2 else 4)
    frame_size = (144000 if version == 3 else 72000) * bitrates[bitrate_index] // sample_rate + ((header[2] >> 1) & 1)
    return len(data) >= offset + frame_size


def _request_voice(provider, url, params, word):
    last_error = None
    for attempt in range(1, VOICE_REQUEST_ATTEMPTS + 1):
        response = None
        try:
            response = logged_request(
                "media", requests.get, "GET", url, params=params,
                log_response_body=False, timeout=DICTVOICE_REQUEST_TIMEOUT,
            )
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            data = response.content
            unavailable = False
            if provider == "youdao" and response.status_code == 500 and content_type == "application/json":
                try:
                    error_data = response.json()
                except ValueError:
                    error_data = None
                # 有道实测的缺音业务响应；不能把所有 HTTP 500 都视为无资源。
                unavailable = isinstance(error_data, dict) and error_data.get("msg") == "returned null audio" and error_data.get("path") == "/dictvoice"
            if unavailable:
                log_event("media", "voice_source_unavailable", word=word, provider=provider,
                          status=response.status_code)
                return None
            response.raise_for_status()
            if not data or content_type not in {"audio/mpeg", "audio/mp3"} or len(data) > MAX_VOICE_BYTES or not _is_mp3(data):
                raise PronunciationError("发音接口未返回完整有效的 MP3", response=response)
            log_event("media", "voice_download_completed", word=word, provider=provider,
                      attempt=attempt, bytes=len(data))
            return data
        except requests.RequestException as error:
            last_error = error
            status = error.response.status_code if error.response is not None else None
            log_event("media", "voice_download_failed", level=logging.WARNING, error=error,
                      word=word, provider=provider, attempt=attempt,
                      max_attempts=VOICE_REQUEST_ATTEMPTS, status=status)
            if isinstance(error, requests.HTTPError) and status is not None and status < 500 and status not in (408, 429):
                break
            if attempt < VOICE_REQUEST_ATTEMPTS:
                sleep(2 ** (attempt - 1))
        finally:
            if response is not None:
                response.close()
    raise PronunciationError(f"{provider} 发音获取失败") from last_error


@unique
class VoiceType(Enum):
    # UK = 1
    US = 0


def request_dictvoice(type, word):
    return _request_voice("youdao", "https://dict.youdao.com/dictvoice", {"type": type, "audio": word}, word)


def request_baidu_voice(type, word):
    return _request_voice("baidu", "https://fanyi.baidu.com/gettts",
                          {"lan": "uk" if type == 1 else "en", "text": word, "spd": 3}, word)


def get_dictvoice_bytes(word):
    if not isinstance(word, str) or not word.strip():
        raise ValueError("发音单词不能为空")
    word = word.strip()
    result = []
    for type_name, type_value in VoiceType.__members__.items():
        type_value = type_value.value
        baidu_error = None
        try:
            r_content = request_baidu_voice(type_value, word)
        except PronunciationError as error:
            baidu_error = error
            r_content = None
        if r_content is None:
            log_event("media", "voice_provider_fallback", word=word, provider="baidu", next_provider="youdao",
                      reason="error" if baidu_error is not None else "unavailable")
            try:
                r_content = request_dictvoice(type_value, word)
            except PronunciationError as error:
                raise PronunciationError(f"单词 [{word}] 的发音仍有来源异常，本词未完成") from error
        if r_content is None:
            if baidu_error is not None:
                raise PronunciationError(f"单词 [{word}] 的百度发音异常，有道无资源，本词未完成") from baidu_error
            log_event("media", "voice_unavailable", word=word, voice_type=type_name,
                      reason="both_sources_confirmed_unavailable")
            print(f"单词 [{word}] 的百度和有道均明确无可用发音，按无音频结果继续生成。")
            continue
        # 添加.mp3扩展名
        result.append((type_name.lower() + ".mp3", io.BytesIO(r_content)))
    return result
