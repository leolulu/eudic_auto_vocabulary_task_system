import json
import os
import tempfile
import unittest
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from dida365_project.api.dida365 import (
    Dida365,
    DidaLoginCooldownError,
    DidaSessionValidationError,
    DidaSignInError,
)


def make_response(status_code, json_data=None, headers=None):
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://api.dida365.com/test"
    response.headers.update(headers or {})
    if json_data is not None:
        response._content = json.dumps(json_data).encode("utf-8")
        response.headers["content-type"] = "application/json"
    return response


def save_cookie(path, value="cached-session"):
    cookie_jar = MozillaCookieJar(str(path))
    cookie_jar.set_cookie(
        requests.cookies.create_cookie(name="t", value=value, domain=".dida365.com", path="/")
    )
    cookie_jar.save(ignore_discard=True, ignore_expires=True)


class Dida365AuthenticationTest(unittest.TestCase):
    def test_authentication_errors_are_in_chinese(self):
        cooldown_error = DidaLoginCooldownError(1_000)
        self.assertIn("滴答清单登录仍处于冷却期", str(cooldown_error))
        self.assertIn("--set-dida-t", str(cooldown_error))

        http_error = DidaSignInError(429)
        self.assertIn("滴答清单登录失败（HTTP 429）", str(http_error))
        self.assertIn("已记录登录冷却状态", str(http_error))

        network_error = DidaSignInError()
        self.assertIn("网络错误", str(network_error))

        validation_error = DidaSessionValidationError()
        self.assertIn("会话验证失败（网络或 DNS 错误）", str(validation_error))
        self.assertIn("已保存的 t 保持不变", str(validation_error))

    def create_client(self, session, session_file):
        with (
            patch("dida365_project.api.dida365.requests.Session", return_value=session),
            patch.object(Dida365, "get_latest_data"),
        ):
            return Dida365("user@example.com", "password", session_file=session_file)

    def test_reuses_valid_persisted_session_without_login(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file)
            session = requests.Session()
            session.get = Mock(return_value=make_response(200))
            session.request = Mock()

            client = self.create_client(session, session_file)

            self.assertTrue(client.has_session_cookie())
            session.get.assert_called_once_with(
                Dida365.AUTH_CHECK_URL,
                headers=client.headers,
                timeout=Dida365.AUTH_REQUEST_TIMEOUT_SECONDS,
            )
            session.request.assert_not_called()

    def test_logs_in_and_persists_session_when_cookie_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock()

            def login_request(*args, **kwargs):
                session.cookies.set("t", "fresh-session", domain=".dida365.com", path="/")
                return make_response(200)

            session.request = Mock(side_effect=login_request)

            self.create_client(session, session_file)

            session.get.assert_not_called()
            session.request.assert_called_once()
            self.assertTrue(session_file.exists())
            persisted_cookies = MozillaCookieJar(str(session_file))
            persisted_cookies.load(ignore_discard=True, ignore_expires=True)
            self.assertTrue(any(cookie.name == "t" for cookie in persisted_cookies))

    def test_replaces_session_only_after_explicit_unauthorized_response(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file)
            session = requests.Session()
            session.get = Mock(return_value=make_response(401))

            def login_request(*args, **kwargs):
                session.cookies.set("t", "replacement-session", domain=".dida365.com", path="/")
                return make_response(200)

            session.request = Mock(side_effect=login_request)

            self.create_client(session, session_file)

            session.request.assert_called_once()
            persisted_cookies = MozillaCookieJar(str(session_file))
            persisted_cookies.load(ignore_discard=True, ignore_expires=True)
            values = [cookie.value for cookie in persisted_cookies if cookie.name == "t"]
            self.assertEqual(values, ["replacement-session"])

    def test_rejects_login_response_without_session_cookie(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock()
            session.request = Mock(return_value=make_response(200))

            with self.assertRaises(DidaSignInError):
                self.create_client(session, session_file)

            self.assertFalse(session_file.exists())
            self.assertTrue((Path(temporary_directory) / "dida365.auth-state.json").exists())

    def test_does_not_login_when_session_check_has_transient_failure(self):
        for status_code in (429, 500):
            with self.subTest(status_code=status_code), tempfile.TemporaryDirectory() as temporary_directory:
                session_file = Path(temporary_directory) / "dida365.session"
                save_cookie(session_file)
                session = requests.Session()
                session.get = Mock(return_value=make_response(status_code))
                session.request = Mock()

                with self.assertRaises(DidaSessionValidationError) as raised:
                    self.create_client(session, session_file)

                self.assertIn(f"HTTP {status_code}", str(raised.exception))
                session.request.assert_not_called()
                self.assertTrue(session_file.exists())

    def test_does_not_login_or_delete_session_when_dns_resolution_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file)
            session = requests.Session()
            session.get = Mock(side_effect=requests.ConnectionError("name resolution failed"))
            session.request = Mock()

            with self.assertRaises(DidaSessionValidationError) as raised:
                self.create_client(session, session_file)

            self.assertIn("网络或 DNS 错误", str(raised.exception))
            session.request.assert_not_called()
            self.assertTrue(session_file.exists())
            self.assertFalse((Path(temporary_directory) / "dida365.auth-state.json").exists())

    def test_ten_process_starts_reuse_one_session_without_login(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file)
            for _ in range(10):
                session = requests.Session()
                session.get = Mock(return_value=make_response(200))
                session.request = Mock()
                self.create_client(session, session_file)
                session.request.assert_not_called()

    def test_login_429_creates_persisted_cooldown_and_restart_does_not_login(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            auth_state_file = Path(temporary_directory) / "dida365.auth-state.json"
            first_session = requests.Session()
            first_session.get = Mock()
            first_session.request = Mock(
                return_value=make_response(429, {"errorCode": "too_many_requests"})
            )

            with patch("dida365_project.api.dida365.time.time", return_value=1_000):
                with self.assertRaises(DidaSignInError):
                    self.create_client(first_session, session_file)

            first_session.request.assert_called_once()
            state = json.loads(auth_state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["consecutive_failures"], 1)
            self.assertEqual(state["next_login_at"], 4_600)
            self.assertEqual(state["last_status"], 429)

            restarted_session = requests.Session()
            restarted_session.get = Mock()
            restarted_session.request = Mock()
            with patch("dida365_project.api.dida365.time.time", return_value=1_001):
                with self.assertRaises(DidaLoginCooldownError):
                    self.create_client(restarted_session, session_file)

            restarted_session.get.assert_not_called()
            restarted_session.request.assert_not_called()

    def test_login_retry_after_is_respected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock()
            session.request = Mock(
                return_value=make_response(
                    429,
                    {"errorCode": "too_many_requests"},
                    headers={"Retry-After": "7200"},
                )
            )

            with patch("dida365_project.api.dida365.time.time", return_value=1_000):
                with self.assertRaises(DidaSignInError):
                    self.create_client(session, session_file)

            state = json.loads(
                (Path(temporary_directory) / "dida365.auth-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["next_login_at"], 8_200)

    def test_transient_login_failure_starts_with_five_minute_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock()
            session.request = Mock(return_value=make_response(503))

            with patch("dida365_project.api.dida365.time.time", return_value=1_000):
                with self.assertRaises(DidaSignInError):
                    self.create_client(session, session_file)

            state = json.loads(
                (Path(temporary_directory) / "dida365.auth-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["next_login_at"], 1_300)
            self.assertEqual(state["last_status"], 503)

    def test_login_network_timeout_starts_with_five_minute_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock()
            session.request = Mock(side_effect=requests.Timeout("timed out"))

            with patch("dida365_project.api.dida365.time.time", return_value=1_000):
                with self.assertRaises(DidaSignInError):
                    self.create_client(session, session_file)

            state = json.loads(
                (Path(temporary_directory) / "dida365.auth-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["next_login_at"], 1_300)
            self.assertIsNone(state["last_status"])

    def test_successful_login_clears_existing_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            auth_state_file = Path(temporary_directory) / "dida365.auth-state.json"
            auth_state_file.write_text(
                json.dumps({"consecutive_failures": 2, "next_login_at": 999}),
                encoding="utf-8",
            )
            session = requests.Session()
            session.get = Mock()

            def login_request(*args, **kwargs):
                session.cookies.set("t", "fresh-session", domain=".dida365.com", path="/")
                return make_response(200)

            session.request = Mock(side_effect=login_request)
            with patch("dida365_project.api.dida365.time.time", return_value=1_000):
                self.create_client(session, session_file)

            self.assertFalse(auth_state_file.exists())

    def test_import_session_cookie_validates_saves_and_clears_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            auth_state_file = Path(temporary_directory) / "dida365.auth-state.json"
            auth_state_file.write_text(
                json.dumps({"consecutive_failures": 1, "next_login_at": 9_999}),
                encoding="utf-8",
            )
            session = requests.Session()
            session.get = Mock(return_value=make_response(200))

            with patch("dida365_project.api.dida365.requests.Session", return_value=session):
                Dida365.import_session_cookie(" imported-session ", session_file=session_file)

            session.get.assert_called_once()
            self.assertTrue(session_file.exists())
            self.assertFalse(auth_state_file.exists())
            persisted_cookies = MozillaCookieJar(str(session_file))
            persisted_cookies.load(ignore_discard=True, ignore_expires=True)
            values = [cookie.value for cookie in persisted_cookies if cookie.name == "t"]
            self.assertEqual(values, ["imported-session"])

    def test_import_invalid_session_cookie_does_not_overwrite_existing_session(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file, value="existing-session")
            session = requests.Session()
            session.get = Mock(return_value=make_response(401))

            with patch("dida365_project.api.dida365.requests.Session", return_value=session):
                with self.assertRaisesRegex(ValueError, "已失效或未获授权"):
                    Dida365.import_session_cookie("invalid-session", session_file=session_file)

            persisted_cookies = MozillaCookieJar(str(session_file))
            persisted_cookies.load(ignore_discard=True, ignore_expires=True)
            values = [cookie.value for cookie in persisted_cookies if cookie.name == "t"]
            self.assertEqual(values, ["existing-session"])

    def test_import_transient_failure_does_not_overwrite_existing_session(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            save_cookie(session_file, value="existing-session")
            session = requests.Session()
            session.get = Mock(return_value=make_response(503))

            with patch("dida365_project.api.dida365.requests.Session", return_value=session):
                with self.assertRaises(requests.HTTPError):
                    Dida365.import_session_cookie("unverified-session", session_file=session_file)

            persisted_cookies = MozillaCookieJar(str(session_file))
            persisted_cookies.load(ignore_discard=True, ignore_expires=True)
            values = [cookie.value for cookie in persisted_cookies if cookie.name == "t"]
            self.assertEqual(values, ["existing-session"])

    def test_import_empty_session_cookie_is_rejected_without_network_request(self):
        session = requests.Session()
        session.get = Mock()
        with patch("dida365_project.api.dida365.requests.Session", return_value=session):
            with self.assertRaisesRegex(ValueError, "不能为空"):
                Dida365.import_session_cookie("   ")
        session.get.assert_not_called()

    def test_corrupted_session_file_falls_back_to_single_login(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session_file.write_text("not a cookie jar", encoding="utf-8")
            session = requests.Session()
            session.get = Mock()

            def login_request(*args, **kwargs):
                session.cookies.set("t", "fresh-session", domain=".dida365.com", path="/")
                return make_response(200)

            session.request = Mock(side_effect=login_request)
            self.create_client(session, session_file)
            session.request.assert_called_once()

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_persisted_session_file_is_owner_only_on_posix(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_file = Path(temporary_directory) / "dida365.session"
            session = requests.Session()
            session.get = Mock(return_value=make_response(200))
            with patch("dida365_project.api.dida365.requests.Session", return_value=session):
                Dida365.import_session_cookie("session", session_file=session_file)
            self.assertEqual(session_file.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
