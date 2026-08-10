import io
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent.dida365 import Dida365Agent, MEDIA_DOWNLOAD_TIMEOUT
from agent.doubao_online import DOUBAO_REQUEST_TIMEOUT, DoubaoOnline
from agent.eudic import EUDIC_REQUEST_TIMEOUT, Eudic
from dida365_project.api.dida365 import Dida365
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import DICTVOICE_REQUEST_TIMEOUT, request_dictvoice
from main import SCHEDULED_JOB_LAST_SUCCESS, log_scheduler_heartbeat, run_scheduled_job


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
        response.content = b"audio"

        with patch(
            "dida365_project.utils.dictvoice_util.requests.get",
            return_value=response,
        ) as request_get:
            self.assertEqual(request_dictvoice(0, "hello"), b"audio")

        self.assertEqual(request_get.call_args.kwargs["timeout"], DICTVOICE_REQUEST_TIMEOUT)

    def test_video_download_uses_the_media_timeout(self):
        response = Mock()
        response.content = b"video"
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

    def test_voice_failure_does_not_block_other_attachments(self):
        agent = Dida365Agent(Mock())

        with (
            patch(
                "agent.dida365.get_dictvoice_bytes",
                side_effect=requests.HTTPError("voice unavailable"),
            ),
            patch("agent.dida365.query_word_explanation_video", return_value=[]),
        ):
            self.assertEqual(agent._get_task_attachments_bytes("rare-word"), [])


class ScheduledJobLoggingTest(unittest.TestCase):
    def setUp(self):
        SCHEDULED_JOB_LAST_SUCCESS.clear()

    def test_successful_job_updates_heartbeat_state(self):
        with patch("builtins.print") as output:
            result = run_scheduled_job("测试任务", lambda: "done", log_success=True)

        self.assertEqual(result, "done")
        self.assertIn("测试任务", SCHEDULED_JOB_LAST_SUCCESS)
        self.assertEqual(output.call_count, 2)

    def test_failed_job_is_logged_and_reraised(self):
        def fail():
            raise RuntimeError("boom")

        with patch("builtins.print") as output, self.assertRaises(RuntimeError):
            run_scheduled_job("失败任务", fail)

        self.assertNotIn("失败任务", SCHEDULED_JOB_LAST_SUCCESS)
        self.assertIn("[调度任务失败] 失败任务", output.call_args.args[0])
        self.assertIs(output.call_args.kwargs["file"], sys.stderr)

    def test_heartbeat_lists_last_successful_jobs(self):
        SCHEDULED_JOB_LAST_SUCCESS["测试任务"] = "2026-08-01T12:00:00+08:00"

        with patch("builtins.print") as output:
            log_scheduler_heartbeat()

        self.assertIn("测试任务=2026-08-01T12:00:00+08:00", output.call_args.args[0])
        self.assertTrue(output.call_args.kwargs["flush"])


if __name__ == "__main__":
    unittest.main()
