import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

import requests

from constants.yaml import BAIDU_VIDEO_SERVICE_URL
from utils.logging_util import log_event, logged_request
from utils.yaml_config_manager import YamlConfigManager


BAIDU_VIDEO_REQUEST_TIMEOUT = (5, 35)


class BaiduVideoServiceError(RuntimeError):
    def __init__(self, message, *, failure=None, status=None, request_id=None):
        super().__init__(message)
        self.failure = failure
        self.status = status
        self.request_id = request_id


def _service_endpoint():
    endpoint = os.environ.get("BAIDU_VIDEO_SERVICE_URL")
    if endpoint is None:
        endpoint = YamlConfigManager().get_all_config().get(BAIDU_VIDEO_SERVICE_URL, "")

    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BaiduVideoServiceError("请配置 baidu_video_service_url 或 BAIDU_VIDEO_SERVICE_URL")

    try:
        parts = urlsplit(endpoint.strip())
        valid_scheme = parts.scheme == "https" or (
            parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if not valid_scheme or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError()
        parts.port  # Validate the port without including the original URL in errors.
    except ValueError:
        raise BaiduVideoServiceError("百度视频服务地址须为 HTTPS 接口地址；本地联调可使用回环 HTTP 地址") from None
    path = parts.path.rstrip("/") or "/v1/video"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _result_urls(result, word):
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise BaiduVideoServiceError("百度视频服务返回格式异常", failure="FORMAT_ERROR")
    if result["ok"] is False:
        failure = result.get("failure")
        if not isinstance(failure, str) or not re.fullmatch(r"[A-Z_]{1,64}", failure):
            failure = "SERVICE_ERROR"
        raise BaiduVideoServiceError(f"百度视频服务查询失败: {failure}", failure=failure)
    if result.get("word") != word:
        raise BaiduVideoServiceError("百度视频服务返回的单词不匹配", failure="FORMAT_ERROR")
    if result.get("hasVideo") is False and result.get("video") is None:
        return []
    video = result.get("video")
    video_url = video.get("videoUrl") if isinstance(video, dict) else None
    if result.get("hasVideo") is not True or not isinstance(video_url, str) or not video_url:
        raise BaiduVideoServiceError("百度视频服务返回结果缺少视频地址", failure="FORMAT_ERROR")
    try:
        parts = urlsplit(video_url)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        parts.port
    except ValueError:
        raise BaiduVideoServiceError("百度视频服务返回的视频地址无效", failure="FORMAT_ERROR") from None
    return [video_url]


def query_word_explanation_video(word: str) -> list[str]:
    """通过独立 HTTP 服务查询讲解视频；正常无视频返回空列表，失败抛出异常。"""
    if not isinstance(word, str) or not word.strip():
        raise ValueError("查询单词不能为空")
    word = " ".join(word.strip().lower().split())
    try:
        endpoint = _service_endpoint()
        response = logged_request(
            "media", requests.get, "GET", endpoint,
            params={"word": word},
            headers={"Accept": "application/json"},
            timeout=BAIDU_VIDEO_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        request_id = response.headers.get("X-Request-Id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            request_id = None
        if not 200 <= response.status_code < 300:
            failure = "HTTP_ERROR"
            try:
                payload = response.json()
            except ValueError:
                pass
            else:
                code = payload.get("failure") if isinstance(payload, dict) else None
                if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,64}", code):
                    failure = code
            raise BaiduVideoServiceError(
                f"百度视频服务请求失败: HTTP {response.status_code} ({failure})",
                failure=failure, status=response.status_code, request_id=request_id,
            )
        if not response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip() == "application/json":
            raise BaiduVideoServiceError("百度视频服务未返回 JSON，请检查服务地址和访问保护", failure="FORMAT_ERROR", request_id=request_id)
        try:
            result = response.json()
        except ValueError:
            raise BaiduVideoServiceError("百度视频服务返回了无效 JSON", failure="FORMAT_ERROR", request_id=request_id) from None
        urls = _result_urls(result, word)
        log_event("media", "video_query_completed", word=word, has_video=bool(urls), service_request_id=request_id)
        return urls
    except requests.RequestException as error:
        log_event("media", "video_query_failed", level=logging.ERROR, error=error, word=word)
        raise BaiduVideoServiceError("百度视频服务网络请求失败", failure="NETWORK_ERROR") from error
    except BaiduVideoServiceError as error:
        log_event("media", "video_query_failed", level=logging.ERROR, error=error,
                  word=word, failure=error.failure, status=error.status, service_request_id=error.request_id)
        raise
