import io
import importlib.util
import json
import logging
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from agent.doubao_online import DoubaoOnline
from main import run_scheduled_job
from sentence_practice import SentencePracticeService
from utils.logging_util import (
    BODY_PREVIEW_LIMIT,
    ERROR_BODY_PREVIEW_LIMIT,
    LOG_CATEGORIES,
    LOG_DIRECTORY,
    body_summary,
    configure_logging,
    install_exception_logging,
    log_context,
    log_event,
    logged_request,
)


def response(status=200, content=b"answer", content_type="text/plain"):
    result = requests.Response()
    result.status_code = status
    result._content = content
    result.headers["Content-Type"] = content_type
    result.headers["X-Request-Id"] = "upstream-id"
    result.url = "https://example.test/chat"
    return result


class LoggingTest(unittest.TestCase):
    def setUp(self):
        # Keep all test artifacts inside the project, including on Windows.
        temporary_root = LOG_DIRECTORY.parent / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.directory = Path(self.temp.name)
        configure_logging(self.directory)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(configure_logging)

    def events(self, category):
        return [json.loads(line) for line in (self.directory / f"{category}.log").read_text(encoding="utf-8").splitlines()]

    def test_classification_error_summary_and_idempotent_append(self):
        log_event("runtime", "first")
        configure_logging(self.directory)
        log_event("ai", "failed", level=logging.ERROR, error=ValueError("bad response"))
        configure_logging()
        configure_logging(self.directory)
        log_event("runtime", "second")
        self.assertEqual([item["event"] for item in self.events("runtime")], ["first", "second"])
        self.assertEqual(self.events("ai"), self.events("errors"))
        self.assertEqual(len(list(self.directory.glob("*.log"))), len(LOG_CATEGORIES) + 1)
        self.assertIn("+00:00", self.events("ai")[0]["timestamp"])

    def test_ai_logs_bounded_preview_without_changing_request_or_answer(self):
        upstream = response(content=("回答" * 3000).encode())
        question = "问题" * 5000
        with log_context(job="answer", run_id="run-1", task_id="task-1"), patch("agent.doubao_online.requests.post", return_value=upstream) as send:
            answer = DoubaoOnline("https://example.test/chat").chat(question)
        self.assertEqual(answer, upstream.text)
        self.assertEqual(send.call_args.kwargs["json"]["user_message"], question)
        events = self.events("ai")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["request_id"], events[1]["request_id"])
        self.assertEqual(events[1]["run_id"], "run-1")
        self.assertEqual(events[1]["status"], 200)
        self.assertEqual(events[1]["response_headers"]["x-request-id"], "upstream-id")
        self.assertTrue(events[0]["request_body"]["truncated"])
        self.assertTrue(events[1]["response_body"]["truncated"])
        self.assertLessEqual(len(events[1]["response_body"]["preview"]), BODY_PREVIEW_LIMIT)
        self.assertNotIn("task_id", events[0]["request_body"])

    def test_error_body_status_retry_after_and_business_error_survive_truncation(self):
        content = json.dumps({"content": "x" * 10000, "errorCode": "quota_exceeded"}).encode()
        upstream = response(503, content, "application/json")
        upstream.headers["Retry-After"] = "60"
        logged_request("ai", Mock(return_value=upstream), "POST", upstream.url)
        event = self.events("errors")[0]
        self.assertEqual(event["status"], 503)
        self.assertEqual(event["upstream"]["errorCode"], "quota_exceeded")
        self.assertEqual(event["response_headers"]["retry-after"], "60")
        self.assertLessEqual(len(event["response_body"]["preview"]), ERROR_BODY_PREVIEW_LIMIT)
        logged_request("dida", Mock(return_value=response(200, b'{"id2error":{"task-1":"rejected"}}', "application/json")), "POST", upstream.url)
        self.assertEqual(self.events("errors")[-1]["status"], 200)
        self.assertTrue(self.events("errors")[-1]["business_error"])

    def test_network_error_is_reraised_and_has_traceback_and_context(self):
        error = requests.ReadTimeout("upstream slow")
        with self.assertRaises(requests.ReadTimeout) as raised, log_context(run_id="run-2"):
            logged_request("ai", Mock(side_effect=error), "POST", "https://example.test/chat", timeout=(5, 180))
        self.assertIs(raised.exception, error)
        event = self.events("errors")[0]
        self.assertEqual(event["error_type"], "ReadTimeout")
        self.assertIsNone(event["status"])
        self.assertIn("Traceback", event["traceback"])
        self.assertEqual(event["run_id"], "run-2")
        self.assertEqual(self.events("ai")[0]["timeout"], [5, 180])

    def test_secrets_headers_url_userinfo_and_reflected_body_are_redacted(self):
        secrets = ["test-key-9384", "test-token-5738", "test-password-7362", "test-cookie-5394"]
        upstream = response(403, ("upstream echoed " + " ".join(secrets)).encode())
        upstream.headers["Set-Cookie"] = "t=" + secrets[-1]
        logged_request("eudic", Mock(return_value=upstream), "POST",
                       "https://someone:secret@example.test/chat?token=" + secrets[1],
                       headers={"Authorization": "Bearer " + secrets[0], "Cookie": "t=" + secrets[-1]},
                       json={"nested": {"password": secrets[2]}})
        text = "".join(path.read_text(encoding="utf-8") for path in self.directory.glob("*.log"))
        for secret in secrets:
            self.assertNotIn(secret, text)
        self.assertNotIn("someone:secret", text)
        self.assertNotIn("set-cookie", text.lower())
        self.assertIn("REDACTED", text)

    def test_auth_payload_is_omitted_and_binary_files_are_never_read_for_logging(self):
        auth = response(200, b'{"token":"new-secret-1234"}', "application/json")
        logged_request("dida", Mock(return_value=auth), "POST", "https://example.test/user/signon", data='{"password":"login-secret-1234"}')
        event = self.events("dida")[-1]
        self.assertTrue(event["response_body"]["omitted"])
        self.assertTrue(self.events("dida")[0]["request_body"]["omitted"])
        source = io.BytesIO(b"image-private-bytes")
        source.seek(3)
        logged_request("media", Mock(return_value=response(content=b"binary-private-bytes", content_type="image/png")), "POST", "https://example.test/upload", files=[("image", ("image.png", source, "image/png"))])
        self.assertEqual(source.tell(), 3)
        event = self.events("media")[0]
        self.assertEqual(event["files"]["items"][0]["bytes"], 19)
        self.assertTrue(self.events("media")[-1]["response_body"]["omitted"])
        self.assertNotIn("private-bytes", (self.directory / "media.log").read_text())

    def test_parse_failure_keeps_preview_and_associates_interaction(self):
        service = SentencePracticeService.__new__(SentencePracticeService)
        service.state = Mock()
        service.state.get_task_by_id.return_value = {"groups_json": '[{"group_id":1,"words":["word"]}]'}
        service.state.get_interaction_comments.return_value = []
        service.dida = Mock()
        service.dida.get_task.return_value = {"content": "body"}
        service.doubao = Mock()
        service.doubao.chat.return_value = "invalid AI output"
        with self.assertRaises(RuntimeError), log_context(interaction_id=5, task_id="task-5"):
            service._ask_ai({"id": 5, "task_id": "task-5"})
        event = self.events("errors")[0]
        self.assertEqual(event["event"], "review_parse_failed")
        self.assertEqual(event["interaction_id"], 5)
        self.assertEqual(event["response_body"]["preview"], "invalid AI output")

    def test_scheduler_logs_success_and_preserves_failed_job_behavior(self):
        run_scheduled_job("success", lambda: None)
        with self.assertRaises(ValueError):
            run_scheduled_job("failure", Mock(side_effect=ValueError("boom")))
        self.assertEqual([event["event"] for event in self.events("runtime")],
                         ["job_started", "job_completed", "job_started", "job_failed"])
        self.assertEqual(self.events("runtime")[0]["run_id"], self.events("runtime")[1]["run_id"])
        self.assertEqual(self.events("errors")[0]["job"], "failure")

    def test_uncaught_exception_and_unwritable_log_directory(self):
        original = sys.excepthook
        fallback = Mock()
        self.addCleanup(setattr, sys, "excepthook", original)
        sys.excepthook = fallback
        install_exception_logging()
        error = RuntimeError("startup error")
        sys.excepthook(type(error), error, None)
        fallback.assert_called_once()
        self.assertEqual(self.events("errors")[0]["event"], "uncaught_exception")
        with patch("utils.logging_util.configure_logging", side_effect=OSError("disk full")), patch("sys.stderr", new_callable=io.StringIO) as output:
            log_event("ai", "fallback")
        self.assertIn("logging failure", output.getvalue())

    def test_preview_redacts_before_truncating_credentials(self):
        secret = "abcdefgh" * 500
        summary = body_summary('{"token":"' + secret + '","answer":"hello"}', limit=20)
        self.assertNotIn("abcdefgh", summary["preview"])

    def test_content_is_not_mistaken_for_the_t_cookie_key(self):
        summary = body_summary('{"content":"useful response","status":"ok"}')
        self.assertIn("useful response", summary["preview"])

    def test_media_failure_keeps_text_error_without_logging_successful_binary(self):
        upstream = response(502, b'{"errorCode":"gateway_failure"}', "application/json")
        logged_request("media", Mock(return_value=upstream), "GET", upstream.url, log_response_body=False)
        event = self.events("errors")[0]
        self.assertIn("gateway_failure", event["response_body"]["preview"])
        self.assertEqual(event["upstream"]["errorCode"], "gateway_failure")

    def test_real_local_http_request_sends_full_body_and_logs_error_response(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "120")
                self.end_headers()
                self.wfile.write(b'{"errorCode":"rate_limited"}')

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            with self.assertRaises(requests.HTTPError), log_context(run_id="local-http"):
                DoubaoOnline(f"http://127.0.0.1:{server.server_port}/chat").chat("x" * 10000)
        finally:
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual(json.loads(received[0])["user_message"], "x" * 10000)
        event = self.events("errors")[0]
        self.assertEqual(event["status"], 429)
        self.assertEqual(event["response_headers"]["retry-after"], "120")
        self.assertEqual(event["run_id"], "local-http")

    def test_request_context_is_restored_after_completion_and_failure(self):
        logged_request("ai", Mock(return_value=response()), "POST", "https://example.test/chat")
        log_event("runtime", "outside_request")
        with self.assertRaises(requests.ConnectTimeout):
            logged_request("ai", Mock(side_effect=requests.ConnectTimeout("failed")), "POST", "https://example.test/chat")
        log_event("runtime", "outside_failed_request")
        for event in self.events("runtime"):
            self.assertNotIn("request_id", event)

    def test_sdk_client_logs_response_and_preserves_sdk_exception(self):
        # This optional SDK is not used by the scheduler; test our adapter without its dependencies.
        with patch.dict(sys.modules, {"volcenginesdkarkruntime": Mock(Ark=Mock())}):
            from agent.doubao import Doubao

        client = Doubao.__new__(Doubao)
        client.access_point = "test-model"
        client.system_message = "system"
        client.preserve_messages = []
        client.client = Mock()
        choice = Mock()
        choice.message.content = "SDK answer"
        choice.finish_reason = "stop"
        completion = Mock(id="completion-id", choices=[choice])
        client.client.chat.completions.create.return_value = completion
        self.assertEqual(client.chat("question"), "SDK answer")
        self.assertEqual(self.events("ai")[-1]["completion_id"], "completion-id")
        error = requests.ReadTimeout("SDK slow")
        client.client.chat.completions.create.side_effect = error
        with self.assertRaises(requests.ReadTimeout) as raised:
            client.chat("second question")
        self.assertIs(raised.exception, error)
        self.assertEqual(self.events("errors")[-1]["event"], "sdk_request_failed")

    def test_generic_client_preserves_session_saving(self):
        spec = importlib.util.spec_from_file_location("session_requester", LOG_DIRECTORY.parent / "client/session_requester.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        Client = module.Client
        client = Client.__new__(Client)
        client.session = Mock()
        upstream = response()
        client.session.get.return_value = upstream
        client.session.post.return_value = upstream
        client._save_session = Mock()
        self.assertIs(client.get(upstream.url), upstream)
        self.assertIs(client.post(upstream.url, {"hello": "world"}), upstream)
        self.assertEqual(client._save_session.call_count, 2)
        self.assertEqual(len(self.events("http")), 4)


if __name__ == "__main__":
    unittest.main()
