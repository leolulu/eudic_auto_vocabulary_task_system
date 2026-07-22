import copy
import json
import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookiejar import LoadError, MozillaCookieJar
from io import BufferedReader
from pathlib import Path

import requests
from retrying import retry

from ..models.project import Project
from ..models.task import Task
from ..models.upload_attachment import uploadAttachment


class DidaLoginCooldownError(RuntimeError):
    def __init__(self, next_login_at):
        self.next_login_at = next_login_at
        retry_at = _format_local_timestamp(next_login_at)
        super().__init__(
            f"滴答清单登录仍处于冷却期，请在 {retry_at} 之后重试；"
            "也可以使用 --set-dida-t 导入浏览器中的有效会话。"
        )


class DidaSignInError(RuntimeError):
    def __init__(self, status_code=None):
        self.status_code = status_code
        status_text = f"HTTP {status_code}" if status_code is not None else "网络错误"
        super().__init__(
            f"滴答清单登录失败（{status_text}），已记录登录冷却状态。"
            "请使用 --set-dida-t 导入浏览器中的有效会话，或等待冷却结束后重试。"
        )


class DidaSessionValidationError(RuntimeError):
    def __init__(self, status_code=None, timed_out=False):
        self.status_code = status_code
        if status_code is not None:
            reason = f"HTTP {status_code}"
        elif timed_out:
            reason = "网络超时"
        else:
            reason = "网络或 DNS 错误"
        super().__init__(
            f"滴答清单会话验证失败（{reason}）。已保存的 t 保持不变，程序不会尝试重新登录。"
            "请检查网络连接、DNS 或代理后重试。"
        )


def _format_local_timestamp(timestamp):
    local_timezone = datetime.now().astimezone().tzinfo
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .astimezone(local_timezone)
        .isoformat(timespec="seconds")
    )


class Dida365:
    AUTH_CHECK_URL = "https://api.dida365.com/api/v1/attachment/isUnderQuota"
    DEFAULT_SESSION_FILE = Path(__file__).resolve().parents[2] / "dida365.session"
    LOGIN_429_BASE_DELAY_SECONDS = 60 * 60
    LOGIN_429_MAX_DELAY_SECONDS = 24 * 60 * 60
    LOGIN_TRANSIENT_BASE_DELAY_SECONDS = 5 * 60
    LOGIN_TRANSIENT_MAX_DELAY_SECONDS = 60 * 60
    AUTH_REQUEST_TIMEOUT_SECONDS = 30

    def __init__(self, username, password, session_file=None, auth_state_file=None) -> None:
        self._initialize_http(session_file=session_file, auth_state_file=auth_state_file)
        self.ensure_authenticated(username, password)
        self.get_latest_data()

    def _initialize_http(self, session_file=None, auth_state_file=None):
        self.session = requests.Session()
        self.session_file = Path(session_file) if session_file else self.DEFAULT_SESSION_FILE
        self.auth_state_file = (
            Path(auth_state_file)
            if auth_state_file
            else self.session_file.with_name("dida365.auth-state.json")
        )
        self.base_url = "https://api.dida365.com/api/v2"
        self.headers = {
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
            "x-device": '{"platform":"web","os":"Windows 10","device":"Chrome 109.0.0.0","name":"","version":4411,"id":"63b0fb54363a786fba71cc80","channel":"website","campaign":"","websocket":""}',
        }

    def ensure_authenticated(self, username, password):
        if self.load_session_cookies():
            if self.is_session_valid():
                self.clear_login_state()
                print("滴答清单：正在复用已保存的登录会话。")
                return
            self.clear_session_cookies()
            print("滴答清单：已保存的登录会话已失效，将重新登录一次。")
        self.login(username, password)

    def has_session_cookie(self):
        return any(cookie.name == "t" and cookie.value for cookie in self.session.cookies)

    def load_session_cookies(self):
        if not self.session_file.exists():
            return False

        cookie_jar = MozillaCookieJar(str(self.session_file))
        try:
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
        except (LoadError, OSError):
            return False

        self.session.cookies.update(cookie_jar)
        return self.has_session_cookie()

    def save_session_cookies(self):
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self.session_file.with_name(
            f"{self.session_file.stem}.{os.getpid()}.tmp.session"
        )
        cookie_jar = MozillaCookieJar(str(temporary_file))
        for cookie in self.session.cookies:
            cookie_jar.set_cookie(copy.copy(cookie))
        cookie_jar.save(ignore_discard=True, ignore_expires=True)
        self._restrict_file_permissions(temporary_file)
        os.replace(temporary_file, self.session_file)
        self._restrict_file_permissions(self.session_file)

    def clear_session_cookies(self):
        self.session.cookies.clear()
        self.session_file.unlink(missing_ok=True)

    def is_session_valid(self):
        try:
            response = self.session.get(
                self.AUTH_CHECK_URL,
                headers=self.headers,
                timeout=self.AUTH_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise DidaSessionValidationError(timed_out=isinstance(error, requests.Timeout)) from error
        if response.status_code == 401:
            return False
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            raise DidaSessionValidationError(response.status_code) from error
        return True

    def login(self, username, password):
        self.raise_if_login_is_cooling_down()
        url = self.base_url + "/user/signon?wc=true&remember=true"
        data = json.dumps({"username": username, "password": password})
        response = None
        try:
            response = self.session.request(
                "POST",
                url,
                headers=self.headers,
                data=data,
                timeout=self.AUTH_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            failed_response = error.response if error.response is not None else response
            self.record_login_failure(failed_response)
            status_code = failed_response.status_code if failed_response is not None else None
            raise DidaSignInError(status_code) from error
        if not self.has_session_cookie():
            self.record_login_failure(response)
            raise DidaSignInError(response.status_code)
        self.save_session_cookies()
        self.clear_login_state()
        print("滴答清单：登录成功，登录会话已保存。")

    @classmethod
    def import_session_cookie(cls, t_value, session_file=None, auth_state_file=None):
        t_value = t_value.strip()
        if not t_value:
            raise ValueError("滴答清单登录会话 t 不能为空")

        client = cls.__new__(cls)
        client._initialize_http(session_file=session_file, auth_state_file=auth_state_file)
        client.session.cookies.set("t", t_value, domain=".dida365.com", path="/")
        response = client.session.get(
            client.AUTH_CHECK_URL,
            headers=client.headers,
            timeout=client.AUTH_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 401:
            raise ValueError("滴答清单登录会话 t 已失效或未获授权")
        response.raise_for_status()
        client.save_session_cookies()
        client.clear_login_state()

    def raise_if_login_is_cooling_down(self):
        state = self.load_login_state()
        next_login_at = state.get("next_login_at", 0)
        if not isinstance(next_login_at, (int, float)):
            next_login_at = 0
        if next_login_at > time.time():
            raise DidaLoginCooldownError(next_login_at)

    def load_login_state(self):
        if not self.auth_state_file.exists():
            return {}
        try:
            with open(self.auth_state_file, "r", encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, ValueError, TypeError):
            return {}
        return state if isinstance(state, dict) else {}

    def record_login_failure(self, response=None):
        previous_state = self.load_login_state()
        try:
            previous_failures = int(previous_state.get("consecutive_failures", 0))
        except (TypeError, ValueError):
            previous_failures = 0
        consecutive_failures = min(previous_failures + 1, 30)
        status_code = response.status_code if response is not None else None
        error_code = self._get_response_error_code(response)

        if status_code == 429 or status_code in (400, 401, 403) or error_code in {
            "access_forbidden",
            "need_captcha",
            "username_password_not_match",
        }:
            base_delay = self.LOGIN_429_BASE_DELAY_SECONDS
            max_delay = self.LOGIN_429_MAX_DELAY_SECONDS
        else:
            base_delay = self.LOGIN_TRANSIENT_BASE_DELAY_SECONDS
            max_delay = self.LOGIN_TRANSIENT_MAX_DELAY_SECONDS

        delay = min(max_delay, base_delay * (2 ** (consecutive_failures - 1)))
        retry_after = self._get_retry_after_seconds(response)
        if retry_after is not None:
            delay = max(delay, retry_after)

        next_login_at = time.time() + delay
        self.save_login_state(
            {
                "consecutive_failures": consecutive_failures,
                "next_login_at": next_login_at,
                "next_login_at_iso": datetime.fromtimestamp(next_login_at, timezone.utc).isoformat(),
                "last_status": status_code,
                "last_error_code": error_code,
            }
        )
        print(
            "滴答清单：登录失败，已暂停后续登录请求，冷却至 "
            f"{_format_local_timestamp(next_login_at)}。"
        )

    def save_login_state(self, state):
        self.auth_state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self.auth_state_file.with_name(
            f"dida365.{os.getpid()}.tmp.auth-state.json"
        )
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        self._restrict_file_permissions(temporary_file)
        os.replace(temporary_file, self.auth_state_file)
        self._restrict_file_permissions(self.auth_state_file)

    def clear_login_state(self):
        self.auth_state_file.unlink(missing_ok=True)

    @staticmethod
    def _get_response_error_code(response):
        if response is None:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data.get("errorCode") if isinstance(data, dict) else None

    @staticmethod
    def _get_retry_after_seconds(response):
        if response is None:
            return None
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return max(0, int(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0, int(retry_at.timestamp() - time.time()))

    @staticmethod
    def _restrict_file_permissions(path):
        if os.name != "nt":
            os.chmod(path, 0o600)

    def get_latest_data(self):
        self.get_data()
        self.enrich_info()

    def get_data(self):
        url = self.base_url + "/batch/check/0"
        r = self.session.get(url, headers=self.headers)
        r.raise_for_status()
        self.data = json.loads(r.content)
        self._get_projects()
        self._get_task()

    def search(self, keyword: str):
        url = self.base_url + "/search/all"
        params = {"keywords": keyword}
        r = self.session.get(url, headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def enrich_info(self):
        self._enrich_task_info()

    def _enrich_task_info(self):
        project_id_name_mapping = {p.id: p.name for p in self.projects}
        for task in self.active_tasks:
            task.project_name = project_id_name_mapping.get(task.project_id)

    def _get_task(self):
        tasks = self.data["syncTaskBean"]["update"]
        self.active_tasks = [Task(i) for i in tasks]

    def _get_projects(self):
        projects = self.data["projectProfiles"]
        self.projects = [Project(i) for i in projects]

    @retry(wait_fixed=4000, stop_max_attempt_number=5)
    def post_task(self, payload):
        url = self.base_url + "/batch/task"
        data = json.dumps(payload)
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()

    @retry(wait_fixed=4000, stop_max_attempt_number=5)
    def adjust_task_parent(self, payload):
        url = self.base_url + "/batch/taskParent"
        data = json.dumps(payload)
        r = self.session.request("POST", url, headers=self.headers, data=data)
        r.raise_for_status()

    @retry(wait_fixed=4000, stop_max_attempt_number=5)
    def upload_attachment(self, *attachments: uploadAttachment):
        for attachment in attachments:
            url = "https://api.dida365.com/api/v1/attachment/upload/{project_id}/{task_id}/{uuid}".format(
                project_id=attachment.project_id, task_id=attachment.task_id, uuid=uuid.uuid1().hex
            )
            if attachment.file_bytes is not None:
                f = attachment.file_bytes
            elif attachment.file_path is not None:
                f = open(attachment.file_path, "rb")
            else:
                raise UserWarning(f"Attachment without neither file bytes nor file path!")
            files = [("file", (attachment.file_name, f, "application/octet-stream"))]
            headers = copy.copy(self.headers)
            headers.pop("content-type")
            try:
                r = self.session.request("POST", url, headers=headers, data={}, files=files)
                r.raise_for_status()
            except:
                raise
            finally:
                if isinstance(f, BufferedReader):
                    f.close()
