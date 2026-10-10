import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from itertools import islice
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


LOG_DIRECTORY = Path(__file__).resolve().parents[1] / "logs"
BODY_PREVIEW_LIMIT = 1024
ERROR_BODY_PREVIEW_LIMIT = 4096
TRACEBACK_LIMIT = 8192
LOG_CATEGORIES = ("runtime", "ai", "dida", "eudic", "anki", "media", "http")
_CONTEXT = ContextVar("log_context", default={})
_LOCK = threading.RLock()
_CONFIGURED_DIRECTORY = None
_SECRETS = set()
_SENSITIVE_KEYS = {
    "authorization", "proxyauthorization", "cookie", "setcookie", "password",
    "passwd", "pwd", "username", "email", "token", "accesstoken",
    "refreshtoken", "apikey", "key", "secret", "clientsecret", "t",
    "signature", "sign", "urlsign", "synctoken", "userid", "syncuserid",
    "eudicsynctoken", "eudicsyncuserid",
}
_RESPONSE_HEADERS = {
    "content-type", "content-length", "retry-after", "x-request-id",
    "request-id", "x-tt-logid", "traceparent",
}
_REQUEST_HEADERS = {"content-type", "accept", "user-agent"}


def _sensitive(key):
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEYS or normalized.endswith(("token", "password", "apikey", "secret"))


def _remember_secrets(value):
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _sensitive(key) and isinstance(item, str) and len(item) >= 4:
                with _LOCK:
                    _SECRETS.add(item)
                    parts = re.split(r"[\s=;,]+", item)
                    _SECRETS.update(part for part in parts if len(part) >= 4)
            elif isinstance(item, (Mapping, list, tuple)):
                _remember_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _remember_secrets(item)


def register_secrets(**values):
    _remember_secrets(values)


def safe_url(url):
    try:
        parts = urlsplit(str(url))
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = [(key, "[REDACTED]" if _sensitive(key) else value)
                 for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))
    except ValueError:
        return "[INVALID URL]"


def redact_text(text):
    text = str(text)
    with _LOCK:
        secrets = tuple(_SECRETS)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"https?://[^\s<>\"']+", lambda match: safe_url(match.group()), text)
    text = re.sub(r"\b(?:Bearer|NIS|QYN)\s+[^\s\"',;]+", "[REDACTED AUTH]", text, flags=re.IGNORECASE)
    text = re.sub(
        r'(?<![a-zA-Z0-9_])([\"\']?(?:password|passwd|pwd|token|access_token|refresh_token|api_key|apikey|authorization|cookie|set-cookie|username|email|secret|signature|t)[\"\']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,;&}]+)',
        r'\1"[REDACTED]"', text, flags=re.IGNORECASE,
    )
    return text


def _safe_value(value, depth=0):
    if depth > 6:
        return "[MAX DEPTH]"
    if isinstance(value, Mapping):
        items = list(islice(value.items(), 30))
        result = {}
        for key, item in items:
            if _sensitive(key):
                safe_item = "[REDACTED]"
            elif key == "preview" and isinstance(item, str):
                safe_item = redact_text(item)[:ERROR_BODY_PREVIEW_LIMIT]
            else:
                safe_item = _safe_value(item, depth + 1)
            result[str(key)[:100]] = safe_item
        if len(value) > len(items):
            result["_omitted_keys"] = len(value) - len(items)
        return result
    if isinstance(value, (list, tuple)):
        result = [_safe_value(item, depth + 1) for item in value[:20]]
        if len(value) > 20:
            result.append({"_omitted_items": len(value) - 20})
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = redact_text(value)
    return text if len(text) <= 512 else text[:512] + "...[truncated]"


def body_summary(body, limit=BODY_PREVIEW_LIMIT, *, omit=False):
    if body is None:
        return {"length": 0, "truncated": False, "preview": ""}
    if isinstance(body, bytes):
        length = len(body)
        length_unit = "bytes"
        if omit:
            return {"length": length, "length_unit": length_unit, "omitted": True}
        text = body.decode("utf-8", errors="replace")
    else:
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
        length = len(text)
        length_unit = "characters"
    # Sanitize before slicing, so truncation cannot expose half of a credential.
    try:
        structured = json.loads(text)
    except (ValueError, TypeError):
        preview = redact_text(text)
    else:
        _remember_secrets(structured)
        preview = json.dumps(_safe_value(structured), ensure_ascii=False)
    if omit:
        return {"length": length, "length_unit": length_unit, "omitted": True}
    return {
        "length": length, "length_unit": length_unit,
        "truncated": len(preview) > limit or len(text) > limit or "[truncated]" in preview or "_omitted_" in preview,
        "preview": preview[:limit],
    }


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "category": record.name.rsplit(".", 1)[-1],
            "pid": os.getpid(), **getattr(record, "event_data", {}),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _FileHandler(logging.FileHandler):
    def handleError(self, record):
        sys.stderr.write("[logging failure] " + self.format(record) + "\n")


def configure_logging(log_directory=LOG_DIRECTORY):
    global _CONFIGURED_DIRECTORY
    directory = Path(log_directory).resolve()
    with _LOCK:
        if _CONFIGURED_DIRECTORY == directory:
            return
        directory.mkdir(parents=True, exist_ok=True)
        formatter = _JsonFormatter()
        handlers = {}
        try:
            for category in LOG_CATEGORIES:
                handler = _FileHandler(directory / f"{category}.log", encoding="utf-8")
                handler.setFormatter(formatter)
                handlers[category] = handler
            error_handler = _FileHandler(directory / "errors.log", encoding="utf-8")
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
        except OSError:
            for handler in handlers.values():
                handler.close()
            raise
        previous = set()
        for category in LOG_CATEGORIES:
            logger = logging.getLogger(f"vocabulary.{category}")
            previous.update(logger.handlers)
            logger.handlers = [handlers[category], error_handler]
            logger.setLevel(logging.INFO)
            logger.propagate = False
        for handler in previous:
            handler.close()
        _CONFIGURED_DIRECTORY = directory


@contextmanager
def log_context(**fields):
    token = _CONTEXT.set({**_CONTEXT.get(), **fields})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def log_event(category, event, *, level=logging.INFO, error=None, **fields):
    data = {"event": event, **_safe_value(_CONTEXT.get()), **_safe_value(fields)}
    if error is not None:
        data["error_type"] = type(error).__name__
        data["error"] = redact_text(error)[:2048]
        data["traceback"] = redact_text("".join(traceback.format_exception(type(error), error, error.__traceback__)))[:TRACEBACK_LIMIT]
    try:
        with _LOCK:
            configure_logging(_CONFIGURED_DIRECTORY or LOG_DIRECTORY)
            logging.getLogger(f"vocabulary.{category}").log(level, event, extra={"event_data": data})
    except OSError as log_error:
        sys.stderr.write(f"[logging failure: {type(log_error).__name__}] " + json.dumps(data, ensure_ascii=False) + "\n")


def install_exception_logging():
    previous_hook = sys.excepthook

    def log_uncaught(error_type, error, tb):
        log_event("runtime", "uncaught_exception", level=logging.ERROR, error=error)
        previous_hook(error_type, error, tb)

    sys.excepthook = log_uncaught


def _file_summary(files):
    if not files:
        return None
    items = list(files.items()) if isinstance(files, Mapping) else list(files)
    summaries = []
    for name, item in items[:20]:
        filename, content = item[:2] if isinstance(item, tuple) else (None, item)
        size = len(content) if isinstance(content, (bytes, str)) else None
        if size is None and hasattr(content, "getbuffer"):
            size = content.getbuffer().nbytes
        summaries.append({"field": str(name), "filename": filename, "bytes": size})
    return {"count": len(items), "items": summaries}


def logged_request(category, send, method, url, *, log_response_body=True, **kwargs):
    """Observe an existing request without changing its timeout, retries or HTTP handling."""
    request_id = uuid.uuid4().hex
    started_at = time.monotonic()
    _remember_secrets(kwargs.get("headers", {}))
    _remember_secrets(kwargs.get("params", {}))
    url_parts = urlsplit(url)
    register_secrets(username=url_parts.username, password=url_parts.password)
    _remember_secrets(dict(parse_qsl(url_parts.query)))
    session = getattr(getattr(send, "func", send), "__self__", None)
    if session is not None and hasattr(session, "cookies"):
        cookies = session.cookies
        if hasattr(cookies, "get_dict"):
            _remember_secrets(cookies.get_dict())
    body = kwargs.get("json", kwargs.get("data"))
    auth_request = "/user/signon" in url_parts.path
    with log_context(request_id=request_id):
        request_body = body_summary(body, omit=auth_request)
        request_headers = kwargs.get("headers", {})
        if session is not None and isinstance(getattr(session, "headers", None), Mapping):
            _remember_secrets(session.headers)
            request_headers = {**session.headers, **request_headers}
        log_event(category, "request_started", method=method, url=safe_url(url),
                  timeout=kwargs.get("timeout"), params=kwargs.get("params"),
                  request_headers={key.lower(): value for key, value in request_headers.items() if key.lower() in _REQUEST_HEADERS},
                  request_body=request_body, files=_file_summary(kwargs.get("files")))
        try:
            response = send(url, **kwargs)
        except Exception as error:
            failed_response = getattr(error, "response", None)
            status = getattr(failed_response, "status_code", None)
            failed_content = getattr(failed_response, "content", None)
            if not isinstance(failed_content, (bytes, str)):
                failed_content = None
            log_event(category, "request_failed", level=logging.ERROR, error=error,
                      method=method, url=safe_url(url), status=status,
                      response_body=body_summary(failed_content, ERROR_BODY_PREVIEW_LIMIT, omit=auth_request or not log_response_body),
                      duration_ms=round((time.monotonic() - started_at) * 1000, 1))
            raise
        status = response.status_code if isinstance(response.status_code, int) else None
        failed = status is not None and status >= 400
        headers = response.headers if isinstance(response.headers, Mapping) else {}
        _remember_secrets(headers)
        content_type = str(headers.get("content-type", "")).lower()
        binary = content_type.startswith(("image/", "audio/", "video/")) or "octet-stream" in content_type
        content = response.content if isinstance(response.content, bytes) else response.text
        if not isinstance(content, (bytes, str)):
            content = None
        summary = body_summary(content, ERROR_BODY_PREVIEW_LIMIT if failed else BODY_PREVIEW_LIMIT,
                               omit=auth_request or binary or (not log_response_body and not failed))
        upstream = {}
        if not auth_request and not binary and (log_response_body or failed) and isinstance(content, (bytes, str)):
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeError):
                pass
            else:
                if isinstance(payload, dict):
                    upstream = {key: payload[key] for key in ("code", "status", "errorCode", "error", "message", "id2error") if key in payload}
        business_error = bool(upstream.get("errorCode") or upstream.get("error") or upstream.get("id2error"))
        log_event(category, "response_received", level=logging.ERROR if failed or business_error else logging.INFO,
                  method=method, url=safe_url(url), status=status,
                  response_url=safe_url(response.url) if isinstance(response.url, str) else safe_url(url),
                  business_error=business_error,
                  duration_ms=round((time.monotonic() - started_at) * 1000, 1),
                  response_headers={key.lower(): value for key, value in headers.items() if key.lower() in _RESPONSE_HEADERS},
                  response_body=summary, upstream=upstream)
        return response
