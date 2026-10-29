import io
import json
import sys
import unittest
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
import schedule

from agent.dida365 import Dida365Agent, MEDIA_DOWNLOAD_TIMEOUT
from agent.doubao_online import DOUBAO_REQUEST_TIMEOUT, DoubaoOnline
from agent.eudic import EUDIC_REQUEST_TIMEOUT, Eudic
from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from dida365_project.api.dida365 import Dida365
from dida365_project.models.task import Task
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import DICTVOICE_REQUEST_TIMEOUT, request_dictvoice
from main import Bearer, SCHEDULED_JOB_LAST_SUCCESS, log_scheduler_heartbeat, run_scheduled_job


def make_dida_client(session=None):
    client = Dida365.__new__(Dida365)
    client.session = session or Mock()
    client.base_url = "https://api.dida365.test/api/v2"
    client.headers = {"content-type": "application/json"}
    return client


class DidaNetworkResilienceTest(unittest.TestCase):
    def test_read_requests_use_the_read_timeout(self):
        session = Mock()
        sync_response = Mock()
        sync_response.content = json.dumps(
            {"projectProfiles": [], "syncTaskBean": {"update": []}}
        ).encode("utf-8")
        search_response = Mock()
        search_response.json.return_value = {"tasks": []}
        session.get.side_effect = [sync_response, search_response]
        client = make_dida_client(session)

        client.get_data()
        client.search("hello")

        self.assertEqual(
            session.get.call_args_list[0].kwargs["timeout"],
            Dida365.READ_REQUEST_TIMEOUT,
        )
        self.assertEqual(
            session.get.call_args_list[1].kwargs["timeout"],
            Dida365.READ_REQUEST_TIMEOUT,
        )

    def test_task_write_retries_connect_timeout_with_backoff(self):
        session = Mock()
        response = Mock()
        session.request.side_effect = [requests.ConnectTimeout("connect"), response]
        client = make_dida_client(session)

        with patch("dida365_project.api.dida365.time.sleep") as sleep:
            client.post_task({"add": []})

        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(
            session.request.call_args.kwargs["timeout"],
            Dida365.WRITE_REQUEST_TIMEOUT,
        )
        sleep.assert_called_once_with(1)

    def test_task_write_does_not_retry_ambiguous_read_timeout(self):
        session = Mock()
        session.request.side_effect = requests.ReadTimeout("read")
        client = make_dida_client(session)

        with (
            patch("dida365_project.api.dida365.time.sleep") as sleep,
            self.assertRaises(requests.ReadTimeout),
        ):
            client.post_task({"add": []})

        self.assertEqual(session.request.call_count, 1)
        sleep.assert_not_called()

    def test_task_write_stops_after_three_connect_attempts(self):
        session = Mock()
        session.request.side_effect = requests.ConnectTimeout("connect")
        client = make_dida_client(session)

        with (
            patch("dida365_project.api.dida365.time.sleep") as sleep,
            self.assertRaises(requests.ConnectTimeout),
        ):
            client.post_task({"add": []})

        self.assertEqual(session.request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_attachment_retry_reuses_upload_id_and_complete_bytes(self):
        session = Mock()
        response = Mock()
        session.request.side_effect = [requests.ConnectTimeout("connect"), response]
        client = make_dida_client(session)
        task = SimpleNamespace(id="task-id", project_id="project-id")
        source = io.BytesIO(b"complete audio bytes")
        attachment = uploadAttachment(task, file_bytes_obj=("us.mp3", source))

        with (
            patch(
                "dida365_project.api.dida365.uuid.uuid1",
                return_value=SimpleNamespace(hex="stable-upload-id"),
            ),
            patch("dida365_project.api.dida365.time.sleep"),
        ):
            client.upload_attachment(attachment)

        self.assertEqual(session.request.call_count, 2)
        request_urls = [call.args[1] for call in session.request.call_args_list]
        self.assertEqual(request_urls[0], request_urls[1])
        self.assertTrue(request_urls[0].endswith("/stable-upload-id"))
        for call in session.request.call_args_list:
            self.assertEqual(call.kwargs["timeout"], Dida365.UPLOAD_REQUEST_TIMEOUT)
            self.assertEqual(
                call.kwargs["files"][0][1][1],
                b"complete audio bytes",
            )


class ExternalRequestTimeoutTest(unittest.TestCase):
    def test_eudic_list_and_page_requests_use_the_eudic_timeout(self):
        response = Mock()
        response.json.side_effect = [
            {"data": []},
            {"data": []},
        ]

        with patch("agent.eudic.requests.get", return_value=response) as request_get:
            eudic = Eudic("NIS test")
            eudic.get_vocab_book()
            eudic._fetch_page("book-id", 0, 100)

        self.assertEqual(request_get.call_count, 2)
        for call in request_get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], EUDIC_REQUEST_TIMEOUT)

    def test_doubao_request_uses_the_long_generation_timeout(self):
        response = Mock()
        response.text = "answer"

        with patch("agent.doubao_online.requests.post", return_value=response) as request_post:
            self.assertEqual(DoubaoOnline("http://example.test").chat("question"), "answer")

        self.assertEqual(request_post.call_args.kwargs["timeout"], DOUBAO_REQUEST_TIMEOUT)

    def test_dictvoice_request_uses_the_audio_timeout(self):
        response = Mock()
        response.content = (Path(__file__).parent / "fixtures/pronunciation.mp3").read_bytes()
        response.headers = {"Content-Type": "audio/mpeg"}

        with patch(
            "dida365_project.utils.dictvoice_util.requests.get",
            return_value=response,
        ) as request_get:
            self.assertEqual(request_dictvoice(0, "hello"), response.content)

        self.assertEqual(request_get.call_args.kwargs["timeout"], DICTVOICE_REQUEST_TIMEOUT)

    def test_video_download_uses_the_media_timeout(self):
        response = Mock()
        response.content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        agent = Dida365Agent(Mock())

        with (
            patch("agent.dida365.get_dictvoice_bytes", return_value=[]),
            patch(
                "agent.dida365.query_word_explanation_video",
                return_value=["https://example.test/video.mp4"],
            ),
            patch("agent.dida365.requests.get", return_value=response) as request_get,
        ):
            files = agent._get_task_attachments_bytes("hello")

        self.assertEqual(files[0][0], "video.mp4")
        self.assertEqual(request_get.call_args.kwargs["timeout"], MEDIA_DOWNLOAD_TIMEOUT)

    def test_voice_failure_keeps_the_word_incomplete(self):
        agent = Dida365Agent(Mock())

        with (
            patch(
                "agent.dida365.get_dictvoice_bytes",
                side_effect=requests.HTTPError("voice unavailable"),
            ),
            patch("agent.dida365.query_word_explanation_video", return_value=[]) as video,
        ):
            with self.assertRaises(requests.HTTPError):
                agent._get_task_attachments_bytes("rare-word")
        video.assert_not_called()


class ScheduledJobLoggingTest(unittest.TestCase):
    def setUp(self):
        SCHEDULED_JOB_LAST_SUCCESS.clear()

    def test_successful_job_updates_heartbeat_state(self):
        with patch("builtins.print") as output:
            result = run_scheduled_job("测试任务", lambda: "done", log_success=True)

        self.assertEqual(result, "done")
        self.assertIn("测试任务", SCHEDULED_JOB_LAST_SUCCESS)
        self.assertEqual(output.call_count, 2)

    def test_failed_job_is_logged_without_updating_last_success(self):
        def fail():
            raise RuntimeError("boom")

        SCHEDULED_JOB_LAST_SUCCESS["失败任务"] = "previous-success"
        with patch("builtins.print") as output:
            self.assertIsNone(run_scheduled_job("失败任务", fail))

        self.assertEqual(SCHEDULED_JOB_LAST_SUCCESS["失败任务"], "previous-success")
        self.assertIn("[调度任务失败] 失败任务", output.call_args.args[0])
        self.assertIs(output.call_args.kwargs["file"], sys.stderr)

    def test_ai_failure_is_rescheduled_and_does_not_block_other_jobs(self):
        failures = [
            requests.ConnectTimeout("connect"),
            requests.ReadTimeout("read"),
            requests.ConnectionError("connection"),
            requests.exceptions.SSLError("tls"),
            requests.exceptions.ChunkedEncodingError("truncated"),
            requests.exceptions.ContentDecodingError("decode"),
            requests.TooManyRedirects("redirects"),
        ]
        for status in (400, 401, 403, 429, 500, 502, 503, 504):
            response = requests.Response()
            response.status_code = status
            response._content = b"upstream error"
            failures.append(response)
        for body in (b"", b" \r\n\t"):
            response = requests.Response()
            response.status_code = 200
            response._content = body
            failures.append(response)
        good_response = requests.Response()
        good_response.status_code = 200
        good_response._content = b" answer \n"

        for failure in failures:
            with self.subTest(failure=failure), patch(
                "agent.doubao_online.requests.post", side_effect=[failure, good_response]
            ) as send, patch("builtins.print"):
                SCHEDULED_JOB_LAST_SUCCESS.clear()
                scheduler = schedule.Scheduler()
                ai = DoubaoOnline("https://example.test/chat")
                ai_job = scheduler.every(10).seconds.do(
                    run_scheduled_job, "AI任务", lambda: ai.chat("question")
                )
                healthy = Mock()
                healthy_job = scheduler.every(10).seconds.do(run_scheduled_job, "其他任务", healthy)
                ai_job.next_run = healthy_job.next_run = datetime.now() - timedelta(seconds=1)

                scheduler.run_pending()

                healthy.assert_called_once()
                self.assertNotIn("AI任务", SCHEDULED_JOB_LAST_SUCCESS)
                self.assertGreater(ai_job.next_run, datetime.now())
                scheduler.run_pending()
                self.assertEqual(send.call_count, 1)

                ai_job.next_run = datetime.now() - timedelta(seconds=1)
                scheduler.run_pending()
                self.assertEqual(send.call_count, 2)
                self.assertIn("AI任务", SCHEDULED_JOB_LAST_SUCCESS)

    def test_shutdown_signals_are_not_swallowed(self):
        for signal in (KeyboardInterrupt(), SystemExit(1)):
            with self.subTest(signal=type(signal).__name__), self.assertRaises(type(signal)):
                run_scheduled_job("停止任务", Mock(side_effect=signal))

    def test_heartbeat_lists_last_successful_jobs(self):
        SCHEDULED_JOB_LAST_SUCCESS["测试任务"] = "2026-08-01T12:00:00+08:00"

        with patch("builtins.print") as output:
            log_scheduler_heartbeat()

        self.assertIn("测试任务=2026-08-01T12:00:00+08:00", output.call_args.args[0])
        self.assertTrue(output.call_args.kwargs["flush"])


class AiFailureRetryFlowTest(unittest.TestCase):
    def setUp(self):
        SCHEDULED_JOB_LAST_SUCCESS.clear()

    def test_word_failure_leaves_history_untouched_and_retry_adds_only_once(self):
        word = SimpleNamespace(
            word="hello", note=None, note_images=(), is_in_last_days_range=lambda days: True
        )
        eudic = Mock()
        eudic.get_words_in_book.return_value = [word]
        eudic.get_note_data.return_value = None
        eudic.download_note_images.return_value = []
        dida = Mock()
        bearer = Bearer.__new__(Bearer)
        bearer.agent = SimpleNamespace(
            eudic=eudic, dida=dida, doubao=DoubaoOnline("https://example.test/chat")
        )
        history = set()
        response = requests.Response()
        response.status_code = 200
        response._content = b"explanation"

        with (
            patch("agent.doubao_online.requests.post", side_effect=[requests.ReadTimeout("slow"), response]) as send,
            patch("main.if_exists_in_his_set", side_effect=lambda word: word in history),
            patch("main.add_word_to_his_set", side_effect=history.add) as add_history,
            patch("main.get_all_phonetic", return_value="phonetic"),
            patch("main.YamlConfigManager") as config,
            patch("builtins.print"),
        ):
            config.return_value.get_config.return_value = "https://example.test/anki"
            run_scheduled_job("单词任务", bearer.bear_eudic_to_dida365)
            dida.add_task.assert_not_called()
            add_history.assert_not_called()
            self.assertNotIn("单词任务", SCHEDULED_JOB_LAST_SUCCESS)

            run_scheduled_job("单词任务", bearer.bear_eudic_to_dida365)
            run_scheduled_job("单词任务", bearer.bear_eudic_to_dida365)

        dida.add_task.assert_called_once()
        add_history.assert_called_once_with("hello")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(history, {"hello"})

    def test_question_failure_discards_local_answers_and_retry_writes_only_once(self):
        remote = {
            "id": "task-id", "title": "hello", "projectId": VOCAB_BOOK_PROJECT_ID,
            "content": "context\n::first question::\n::second question::",
        }
        original_content = remote["content"]
        dida = Mock()

        def refresh():
            dida.active_tasks = [Task(deepcopy(remote))]

        dida.get_latest_data.side_effect = refresh
        dida_agent = Mock(dida=dida)
        dida_agent.update_task.side_effect = lambda task: remote.update(deepcopy(task))
        ai = DoubaoOnline("https://example.test/chat")
        bearer = Bearer.__new__(Bearer)
        bearer.agent = SimpleNamespace(
            dida=dida_agent, doubao=ai, substitute_new_doubao_agent=Mock()
        )
        responses = []
        for content in (b"discarded answer", b"first answer", b"second answer"):
            response = requests.Response()
            response.status_code = 200
            response._content = content
            responses.append(response)

        with patch(
            "agent.doubao_online.requests.post",
            side_effect=[responses[0], requests.ReadTimeout("slow"), *responses[1:]],
        ) as send, patch("builtins.print"):
            run_scheduled_job("问答任务", bearer.answer_question_from_dida365)
            dida_agent.update_task.assert_not_called()
            self.assertEqual(remote["content"], original_content)
            self.assertNotIn("问答任务", SCHEDULED_JOB_LAST_SUCCESS)

            run_scheduled_job("问答任务", bearer.answer_question_from_dida365)
            run_scheduled_job("问答任务", bearer.answer_question_from_dida365)

        dida_agent.update_task.assert_called_once()
        self.assertEqual(send.call_count, 4)
        self.assertNotIn("discarded answer", remote["content"])
        self.assertEqual(remote["content"].count("A:first answer"), 1)
        self.assertEqual(remote["content"].count("A:second answer"), 1)
        self.assertNotIn("::", remote["content"])


if __name__ == "__main__":
    unittest.main()
