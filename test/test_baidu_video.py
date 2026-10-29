import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from agent.dida365 import Dida365Agent, MEDIA_DOWNLOAD_TIMEOUT
from utils.baidu_video_client import (
    BAIDU_VIDEO_REQUEST_TIMEOUT,
    BaiduVideoServiceError,
    query_word_explanation_video,
)


ENDPOINT = "https://video.example.test/v1/video"
VIDEO_URL = "https://cdn.example.test/video.mp4"


def response(payload, status=200, content_type="application/json"):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    result.headers["Content-Type"] = content_type
    result.headers["X-Request-Id"] = "service-request-id"
    result.url = ENDPOINT
    return result


def video_result(word="evidence"):
    return {"ok": True, "word": word, "hasVideo": True, "video": {"videoUrl": VIDEO_URL}}


class BaiduVideoQueryTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"BAIDU_VIDEO_SERVICE_URL": ENDPOINT})
        environment.start()
        self.addCleanup(environment.stop)

    def test_video_is_queried_through_public_http_without_credentials(self):
        with (
            patch("utils.baidu_video_client.requests.get", return_value=response(video_result())) as request_get,
            patch("utils.baidu_video_client.YamlConfigManager") as config,
            patch("subprocess.run") as subprocess_run,
            patch("playwright.sync_api.sync_playwright") as playwright,
        ):
            self.assertEqual(query_word_explanation_video(" Evidence "), [VIDEO_URL])
        self.assertEqual(request_get.call_args.args, (ENDPOINT,))
        self.assertEqual(request_get.call_args.kwargs["params"], {"word": "evidence"})
        self.assertEqual(request_get.call_args.kwargs["headers"], {"Accept": "application/json"})
        self.assertEqual(request_get.call_args.kwargs["timeout"], BAIDU_VIDEO_REQUEST_TIMEOUT)
        self.assertFalse(request_get.call_args.kwargs["allow_redirects"])
        config.assert_not_called()
        subprocess_run.assert_not_called()
        playwright.assert_not_called()

    def test_confirmed_no_video_returns_empty_list(self):
        with patch("utils.baidu_video_client.requests.get", return_value=response({"ok": True, "word": "apricot", "hasVideo": False, "video": None})):
            self.assertEqual(query_word_explanation_video("apricot"), [])

    def test_legacy_key_setting_is_ignored_and_never_sent(self):
        with patch.dict(os.environ, {"BAIDU_VIDEO_API_KEY": "obsolete-setting"}), patch("utils.baidu_video_client.requests.get", return_value=response(video_result())) as request_get:
            self.assertEqual(query_word_explanation_video("evidence"), [VIDEO_URL])
        self.assertNotIn("Authorization", request_get.call_args.kwargs["headers"])

    def test_service_failures_propagate_without_local_fallback(self):
        for status, code in [(502, "ACS_REJECTED"), (503, "BUSY"), (504, "TIMEOUT")]:
            with self.subTest(status=status), patch(
                "utils.baidu_video_client.requests.get",
                return_value=response({"ok": False, "failure": code, "errmsg": "private upstream detail"}, status),
            ), patch("subprocess.run") as subprocess_run, patch("playwright.sync_api.sync_playwright") as playwright:
                with self.assertRaises(BaiduVideoServiceError) as raised:
                    query_word_explanation_video("evidence")
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(raised.exception.failure, code)
                self.assertEqual(raised.exception.request_id, "service-request-id")
                self.assertNotIn("private upstream detail", str(raised.exception))
                subprocess_run.assert_not_called()
                playwright.assert_not_called()

    def test_redirects_and_login_html_are_errors(self):
        for result in [response(b"redirect", 302, "text/html"), response(b"<html>login</html>", 200, "text/html"), response(b"<html>login</html>", 401, "text/html")]:
            with self.subTest(status=result.status_code), patch("utils.baidu_video_client.requests.get", return_value=result), self.assertRaises(BaiduVideoServiceError):
                query_word_explanation_video("evidence")

    def test_invalid_success_data_is_never_reported_as_no_video(self):
        payloads = [
            [], {}, {"ok": "true"}, video_result("wrong-word"),
            {"ok": True, "word": "evidence", "hasVideo": True, "video": None},
            {"ok": True, "word": "evidence", "hasVideo": False, "video": {"videoUrl": VIDEO_URL}},
            {"ok": True, "word": "evidence", "hasVideo": "false", "video": None},
            {"ok": True, "word": "evidence", "hasVideo": True, "video": {"videoUrl": "http://cdn.example.test/video.mp4"}},
            {"ok": True, "word": "evidence", "hasVideo": True, "video": {"videoUrl": "https://user:pass@cdn.example.test/video.mp4"}},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch("utils.baidu_video_client.requests.get", return_value=response(payload)), self.assertRaises(BaiduVideoServiceError) as raised:
                query_word_explanation_video("evidence")
            self.assertEqual(raised.exception.failure, "FORMAT_ERROR")

    def test_invalid_json_and_business_failure_at_http_200_are_errors(self):
        for result in [response(b"bad json"), response({"ok": False, "failure": "QUOTA_EXCEEDED"})]:
            with patch("utils.baidu_video_client.requests.get", return_value=result), self.assertRaises(BaiduVideoServiceError):
                query_word_explanation_video("evidence")

    def test_transport_failure_is_not_silenced_or_retried_locally(self):
        for error in [requests.ConnectTimeout("connect"), requests.ReadTimeout("read"), requests.ConnectionError("connection")]:
            with self.subTest(error=type(error).__name__), patch("utils.baidu_video_client.requests.get", side_effect=error) as request_get:
                with self.assertRaises(BaiduVideoServiceError) as raised:
                    query_word_explanation_video("evidence")
                self.assertEqual(raised.exception.failure, "NETWORK_ERROR")
                request_get.assert_called_once()

    def test_yaml_settings_work_when_environment_overrides_are_absent(self):
        with patch.dict(os.environ, {}, clear=True), patch("utils.baidu_video_client.YamlConfigManager") as config, patch("utils.baidu_video_client.requests.get", return_value=response(video_result())):
            config.return_value.get_all_config.return_value = {"baidu_video_service_url": ENDPOINT}
            self.assertEqual(query_word_explanation_video("evidence"), [VIDEO_URL])

    def test_origin_url_gets_default_api_path_and_loopback_http_is_allowed(self):
        for origin in ["https://video.example.test/", "http://127.0.0.1:8899", "http://localhost:8899", "http://[::1]:8899"]:
            with self.subTest(origin=origin), patch.dict(os.environ, {"BAIDU_VIDEO_SERVICE_URL": origin}), patch("utils.baidu_video_client.requests.get", return_value=response(video_result())) as request_get:
                query_word_explanation_video("evidence")
                self.assertEqual(request_get.call_args.args[0], origin.rstrip("/") + "/v1/video")

    def test_bad_configuration_stops_before_network_request(self):
        for variable, value in [
            ("BAIDU_VIDEO_SERVICE_URL", ""), ("BAIDU_VIDEO_SERVICE_URL", "http://video.example.test/v1/video"),
            ("BAIDU_VIDEO_SERVICE_URL", "https://user:pass@video.example.test/v1/video"),
            ("BAIDU_VIDEO_SERVICE_URL", "https://video.example.test/v1/video?api_key=private"),
            ("BAIDU_VIDEO_SERVICE_URL", "https://video.example.test:bad/v1/video"),
        ]:
            with self.subTest(variable=variable, value=value), patch.dict(os.environ, {variable: value}), patch("utils.baidu_video_client.requests.get") as request_get, self.assertRaises(BaiduVideoServiceError):
                query_word_explanation_video("evidence")
            request_get.assert_not_called()

    def test_empty_word_stops_before_network_request(self):
        with patch("utils.baidu_video_client.requests.get") as request_get, self.assertRaises(ValueError):
            query_word_explanation_video("  ")
        request_get.assert_not_called()

    def test_attachment_download_uses_public_service_without_authorization(self):
        audio = io.BytesIO(b"audio")
        calls = []
        def send(url, **kwargs):
            calls.append((url, kwargs))
            if url == ENDPOINT:
                return response(video_result())
            self.assertEqual(url, VIDEO_URL)
            return response(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", content_type="video/mp4")
        dida = Mock()
        with patch("utils.baidu_video_client.requests.get", side_effect=send), patch("agent.dida365.get_dictvoice_bytes", return_value=[("us.mp3", audio)]):
            files = Dida365Agent(dida)._get_task_attachments_bytes("evidence")
        self.assertEqual([filename for filename, _ in files], ["us.mp3", "video.mp4"])
        self.assertEqual(files[1][1].getvalue()[4:8], b"ftyp")
        self.assertEqual(calls[1][1]["timeout"], MEDIA_DOWNLOAD_TIMEOUT)
        self.assertNotIn("Authorization", calls[0][1]["headers"])
        self.assertNotIn("Authorization", calls[1][1]["headers"])
        dida.upload_attachment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
