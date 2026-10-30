import io
import json
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent.dida365 import Dida365Agent
from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from constants.eudic import EUDIC_NOTE_IMAGES_PLACEHOLDER
from dida365_project.models.upload_attachment import uploadAttachment
from dida365_project.utils.dictvoice_util import PronunciationError, get_dictvoice_bytes
from main import Bearer


AUDIO = (Path(__file__).parent / "fixtures/pronunciation.mp3").read_bytes()
VIDEO = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def response(content=AUDIO, status=200, content_type="audio/mpeg"):
    result = requests.Response()
    result.status_code = status
    result.url = "https://audio.example.test/voice"
    result.headers["Content-Type"] = content_type
    result._content = content
    result._content_consumed = True
    return result


def media_files():
    return [("us.mp3", io.BytesIO(AUDIO))]


def no_youdao_audio():
    return response(json.dumps({"status": 500, "msg": "returned null audio", "path": "/dictvoice"}).encode(),
                    status=500, content_type="application/json")


class PronunciationProviderTest(unittest.TestCase):
    def setUp(self):
        self.sleep = self.enterContext(patch("dida365_project.utils.dictvoice_util.sleep"))

    def test_baidu_is_first_and_preserves_the_existing_attachment_contract(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", return_value=response()) as send:
            files = get_dictvoice_bytes("rock & roll")
        self.assertEqual(files[0][0], "us.mp3")
        self.assertEqual(files[0][1].getvalue(), AUDIO)
        send.assert_called_once_with(
            "https://fanyi.baidu.com/gettts", params={"lan": "en", "text": "rock & roll", "spd": 3},
            timeout=(5, 30),
        )

    def test_transient_baidu_failure_is_retried_before_fallback(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[requests.ReadTimeout(), response()]) as send:
            self.assertEqual(get_dictvoice_bytes("hello")[0][1].getvalue(), AUDIO)
        self.assertEqual(send.call_count, 2)
        self.assertTrue(all("baidu.com" in call.args[0] for call in send.call_args_list))
        self.sleep.assert_called_once_with(1)

    def test_youdao_is_used_after_baidu_retries_are_exhausted(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[requests.Timeout()] * 3 + [response()]) as send:
            self.assertEqual(get_dictvoice_bytes("hello")[0][1].getvalue(), AUDIO)
        self.assertEqual(send.call_count, 4)
        self.assertEqual(send.call_args.args[0], "https://dict.youdao.com/dictvoice")
        self.assertEqual(send.call_args.kwargs["params"], {"type": 0, "audio": "hello"})

    def test_permanent_http_failure_switches_provider_without_repeating_it(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[response(status=403), response()]) as send:
            get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 2)
        self.sleep.assert_not_called()

    def test_invalid_audio_is_never_accepted_as_success(self):
        for content, content_type in [(b"", "audio/mpeg"), (b"<html>error</html>", "text/html"), (b"ID3", "audio/mpeg"), (AUDIO[:20], "audio/mpeg")]:
            with self.subTest(content=content[:20], content_type=content_type):
                def send(url, **kwargs):
                    return response(content, content_type=content_type) if "baidu.com" in url else response()
                with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=send) as request_get:
                    self.assertEqual(get_dictvoice_bytes("hello")[0][1].getvalue(), AUDIO)
                self.assertEqual(request_get.call_count, 4)

    def test_mp3_with_id3_metadata_is_accepted(self):
        audio = b"ID3\x04\x00\x00\x00\x00\x00\x00" + AUDIO
        with patch("dida365_project.utils.dictvoice_util.requests.get", return_value=response(audio)):
            self.assertEqual(get_dictvoice_bytes("hello")[0][1].getvalue(), audio)

    def test_both_providers_failing_raises_instead_of_returning_no_audio(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", return_value=response(status=503)) as send:
            with self.assertRaisesRegex(PronunciationError, "本词未完成"):
                get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 6)

    def test_both_sources_explicitly_unavailable_return_no_attachment(self):
        # 聚合层契约测试：模拟明确缺音，不假定百度 HTTP 204 具有缺音语义。
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None) as baidu, patch("dida365_project.utils.dictvoice_util.request_dictvoice", return_value=None) as youdao:
            self.assertEqual(get_dictvoice_bytes("missing-word"), [])
        baidu.assert_called_once_with(0, "missing-word")
        youdao.assert_called_once_with(0, "missing-word")
        self.sleep.assert_not_called()

    def test_empty_audio_is_retried_before_using_youdao(self):
        for status in (200, 204):
            with self.subTest(status=status):
                empty = response(b"", status=status)
                empty.headers["Content-Length"] = "0"
                with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[empty] * 3 + [response()]) as send:
                    files = get_dictvoice_bytes("hello")
                self.assertEqual(files[0][1].getvalue(), AUDIO)
                self.assertEqual(send.call_count, 4)
                self.assertTrue(all("baidu.com" in call.args[0] for call in send.call_args_list[:3]))
                self.assertEqual(send.call_args.args[0], "https://dict.youdao.com/dictvoice")

    def test_empty_baidu_audio_and_unavailable_youdao_remain_a_failure(self):
        for status in (200, 204):
            with self.subTest(status=status):
                empty = response(b"", status=status)
                empty.headers["Content-Length"] = "0"
                with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[empty] * 3 + [no_youdao_audio()]) as send:
                    with self.assertRaisesRegex(PronunciationError, "本词未完成"):
                        get_dictvoice_bytes("hello")
                self.assertEqual(send.call_count, 4)

    def test_both_sources_returning_empty_audio_remain_a_failure(self):
        empty = response(b"")
        empty.headers["Content-Length"] = "0"
        with patch("dida365_project.utils.dictvoice_util.requests.get", return_value=empty) as send:
            with self.assertRaisesRegex(PronunciationError, "本词未完成"):
                get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 6)

    def test_unavailable_baidu_still_uses_youdao_audio(self):
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None), patch("dida365_project.utils.dictvoice_util.requests.get", return_value=response()):
            self.assertEqual(get_dictvoice_bytes("hello")[0][1].getvalue(), AUDIO)

    def test_baidu_error_and_youdao_unavailable_remain_a_failure(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[requests.Timeout()] * 3 + [no_youdao_audio()]) as send:
            with self.assertRaisesRegex(PronunciationError, "本词未完成"):
                get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 4)

    def test_baidu_unavailable_and_youdao_error_remain_a_failure(self):
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None), patch("dida365_project.utils.dictvoice_util.requests.get", return_value=response(status=503)) as send:
            with self.assertRaisesRegex(PronunciationError, "本词未完成"):
                get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 3)

    def test_unknown_youdao_server_error_is_not_treated_as_no_audio(self):
        unknown = response(b'{"msg":"synthesis server failed","path":"/dictvoice"}', status=500, content_type="application/json")
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None), patch("dida365_project.utils.dictvoice_util.requests.get", return_value=unknown) as send:
            with self.assertRaises(PronunciationError):
                get_dictvoice_bytes("hello")
        self.assertEqual(send.call_count, 3)

    def test_empty_input_stops_before_a_request(self):
        with patch("dida365_project.utils.dictvoice_util.requests.get") as send:
            with self.assertRaises(ValueError):
                get_dictvoice_bytes("  ")
        send.assert_not_called()


class TaskSnapshot:
    def __init__(self, data):
        self.task_dict = deepcopy(data)
        self.id = data["id"]
        self.project_id = data["projectId"]
        self.title = data["title"]
        self.content = data["content"]
        self.attachments = [SimpleNamespace(**item) for item in data.get("attachments", [])]
        self.attachments_to_upload = set()

    def change_start_date_to_today(self):
        pass

    def update_content(self, content):
        self.content = content
        self.task_dict["content"] = content

    def add_upload_attachment_post_payload_by_bytes(self, *files):
        self.attachments_to_upload.update(uploadAttachment(self, file_bytes_obj=file) for file in files)


class RemoteTasks:
    """Remote writes and reads are separate snapshots, including ambiguous timeouts."""
    def __init__(self):
        self.tasks = {"模板任务一": {"id": "template", "projectId": VOCAB_BOOK_PROJECT_ID, "title": "模板任务一", "content": "template", "attachments": []}}
        self.creates = 0
        self.uploaded = []
        self.fail_after_create = False
        self.fail_after_upload = False
        self.fail_after_update = False
        self.fail_uploads = False
        self.ignore_updates = False
        self.ignore_uploads = False
        self.get_latest_data()

    def get_latest_data(self):
        self.active_tasks = [TaskSnapshot(data) for data in self.tasks.values()]

    def post_task(self, payload):
        for data in payload["add"]:
            if data["title"] in self.tasks:
                raise AssertionError("Duplicate task creation")
            self.tasks[data["title"]] = deepcopy(data)
            self.tasks[data["title"]]["id"] = "task-" + data["title"]
            self.creates += 1
            if self.fail_after_create:
                self.fail_after_create = False
                raise requests.ReadTimeout("created but response lost")
        for data in payload["update"]:
            if self.ignore_updates:
                continue
            self.tasks[data["title"]]["content"] = data["content"]
            if self.fail_after_update:
                self.fail_after_update = False
                raise requests.ReadTimeout("updated but response lost")

    def upload_attachment(self, *attachments):
        if self.fail_uploads:
            raise requests.ReadTimeout("upload unavailable")
        if self.ignore_uploads:
            return
        for attachment in attachments:
            data = next(data for data in self.tasks.values() if data["id"] == attachment.task_id)
            self.uploaded.append(attachment.file_name)
            data.setdefault("attachments", []).append({"file_name": attachment.file_name, "content_file_string": f"![file](attachment/{attachment.file_name})"})
            if self.fail_after_upload:
                self.fail_after_upload = False
                raise requests.ReadTimeout("uploaded but response lost")


class AtomicAttachmentTest(unittest.TestCase):
    def setUp(self):
        self.remote = RemoteTasks()
        self.agent = Dida365Agent(self.remote)
        self.enterContext(patch("agent.dida365.sleep"))

    def test_voice_failure_prevents_remote_task_creation(self):
        with patch("agent.dida365.get_dictvoice_bytes", side_effect=PronunciationError("no audio")):
            with self.assertRaises(PronunciationError):
                self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.creates, 0)

    def test_video_query_failure_also_prevents_remote_task_creation(self):
        with patch("agent.dida365.get_dictvoice_bytes", side_effect=lambda word: media_files()), patch("agent.dida365.query_word_explanation_video", side_effect=requests.Timeout()):
            with self.assertRaises(requests.Timeout):
                self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.creates, 0)

    def test_video_compression_failure_prevents_creation_without_redownloading(self):
        with (
            patch("agent.dida365.get_dictvoice_bytes", side_effect=lambda word: media_files()),
            patch("agent.dida365.query_word_explanation_video", return_value=["https://video.example/movie.mp4"]),
            patch("agent.dida365.requests.get", return_value=response(VIDEO, content_type="video/mp4")) as download,
            patch("agent.dida365.compress_video", side_effect=RuntimeError("视频转码失败")),
        ):
            with self.assertRaisesRegex(RuntimeError, "视频转码失败"):
                self.agent.add_task("hello", "body", resume_existing=True)
        download.assert_called_once()
        self.assertEqual(self.remote.creates, 0)
        self.assertEqual(self.remote.uploaded, [])

    def test_failed_or_invalid_video_download_is_not_silently_skipped(self):
        for result in (response(status=503), response(b"<html>error</html>", content_type="text/html")):
            with self.subTest(status=result.status_code):
                with patch("agent.dida365.get_dictvoice_bytes", side_effect=lambda word: media_files()), patch("agent.dida365.query_word_explanation_video", return_value=["https://video.example/movie.mp4"]), patch("agent.dida365.requests.get", return_value=result) as send:
                    with self.assertRaises((requests.HTTPError, ValueError)):
                        self.agent.add_task("hello", "body", resume_existing=True)
                self.assertEqual(send.call_count, 3)
        self.assertEqual(self.remote.creates, 0)

    def test_explicit_no_video_is_a_successful_word_with_audio(self):
        with patch("agent.dida365.get_dictvoice_bytes", side_effect=lambda word: media_files()), patch("agent.dida365.query_word_explanation_video", return_value=[]):
            task = self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.creates, 1)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])
        self.assertIn("![file](attachment/us.mp3)", task.content)

    def test_create_timeout_is_reconciled_without_creating_twice(self):
        self.remote.fail_after_create = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            task = self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.creates, 1)
        self.assertIn("us.mp3", task.content)

    def test_partial_upload_retry_skips_the_already_uploaded_file(self):
        self.remote.fail_after_upload = True
        with patch.object(self.agent, "_get_task_attachments_bytes", return_value=media_files() + [("movie.mp4", io.BytesIO(VIDEO))]) as prepare:
            task = self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.creates, 1)
        self.assertEqual(Counter(self.remote.uploaded), {"us.mp3": 1, "movie.mp4": 1})
        self.assertIn("movie.mp4", task.content)
        prepare.assert_called_once()

    def test_body_write_timeout_is_reconciled_by_reading_the_body(self):
        self.remote.fail_after_update = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            task = self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])
        self.assertIn("us.mp3", task.content)

    def test_http_success_without_body_update_is_not_task_success(self):
        self.remote.ignore_updates = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            with self.assertRaisesRegex(RuntimeError, "尚未确认"):
                self.agent.add_task("hello", "body", resume_existing=True)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])

    def test_retry_after_a_restart_reuses_the_existing_task_and_images(self):
        self.remote.tasks["hello"] = {"id": "word-task", "projectId": VOCAB_BOOK_PROJECT_ID, "title": "hello", "content": "context\n![file](attachment/image.jpg)\nbody", "attachments": [{"file_name": "image.jpg", "content_file_string": "![file](attachment/image.jpg)"}]}
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            task = self.agent.add_task("hello", "regenerated body", note_image_files=[("image.jpg", io.BytesIO(b"image"))], resume_existing=True)
        self.assertEqual(self.remote.creates, 0)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])
        self.assertEqual(task.content.count("attachment/image.jpg"), 1)
        self.assertIn("context", task.content)
        self.assertNotIn(EUDIC_NOTE_IMAGES_PLACEHOLDER, task.content)


class AtomicWordHistoryTest(unittest.TestCase):
    def setUp(self):
        self.remote = RemoteTasks()
        self.agent = Dida365Agent(self.remote)
        self.history = set()
        word = SimpleNamespace(word="hello", note=None, note_images=())
        eudic = Mock()
        eudic.download_note_images.return_value = []
        self.bearer = Bearer.__new__(Bearer)
        self.bearer.agent = SimpleNamespace(eudic=eudic, dida=self.agent)
        self.bearer.acquire_words = Mock(side_effect=lambda *args, **kwargs: [] if "hello" in self.history else [word])
        self.bearer.get_doubao_explanation_by_doubao = Mock(return_value="body")
        self.enterContext(patch("agent.dida365.sleep"))
        self.enterContext(patch("main.get_all_phonetic", return_value="ipa"))
        config = self.enterContext(patch("main.YamlConfigManager"))
        config.return_value.get_config.return_value = "https://example.test/anki"
        self.enterContext(patch("main.add_word_to_his_set", side_effect=self.history.add))
        self.enterContext(patch("main.traceback.print_exc"))

    def test_both_audio_sources_failing_leave_history_untouched(self):
        with patch("agent.dida365.get_dictvoice_bytes", side_effect=PronunciationError("both failed")):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, set())
        self.assertEqual(self.remote.creates, 0)

    def test_confirmed_no_audio_and_no_video_complete_once_without_retrying_forever(self):
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None) as baidu, patch("dida365_project.utils.dictvoice_util.requests.get", return_value=no_youdao_audio()) as send, patch("agent.dida365.query_word_explanation_video", return_value=[]):
            self.bearer.bear_eudic_to_dida365()
            self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, {"hello"})
        self.assertEqual(self.remote.creates, 1)
        self.assertEqual(self.remote.uploaded, [])
        baidu.assert_called_once_with(0, "hello")
        self.assertEqual(send.call_count, 1)
        self.assertIn("body", self.remote.tasks["hello"]["content"])

    def test_confirmed_no_audio_still_processes_the_video_before_success(self):
        with patch("dida365_project.utils.dictvoice_util.request_baidu_voice", return_value=None), patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[no_youdao_audio(), response(VIDEO, content_type="video/mp4")]), patch("agent.dida365.query_word_explanation_video", return_value=["https://video.example/movie.mp4"]):
            self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, {"hello"})
        self.assertEqual(self.remote.uploaded, ["movie.mp4"])
        self.assertIn("movie.mp4", self.remote.tasks["hello"]["content"])

    def test_empty_audio_cannot_create_a_task_or_update_history(self):
        for status in (200, 204):
            with self.subTest(status=status):
                empty = response(b"", status=status)
                empty.headers["Content-Length"] = "0"
                with patch("dida365_project.utils.dictvoice_util.requests.get", side_effect=[empty] * 3 + [no_youdao_audio()]), patch("agent.dida365.query_word_explanation_video") as video:
                    with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                        self.bearer.bear_eudic_to_dida365()
                self.assertEqual(self.history, set())
                self.assertEqual(self.remote.creates, 0)
                self.assertEqual(self.remote.uploaded, [])
                video.assert_not_called()

    def test_upload_failure_without_images_can_retry_and_finish_once(self):
        self.remote.fail_uploads = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
            self.assertEqual(self.history, set())
            self.remote.fail_uploads = False
            self.bearer.bear_eudic_to_dida365()
            self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, {"hello"})
        self.assertEqual(self.remote.creates, 1)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])

    def test_body_not_confirmed_cannot_update_history(self):
        self.remote.ignore_updates = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, set())

    def test_http_success_without_an_uploaded_audio_cannot_update_history(self):
        self.remote.ignore_uploads = True
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, set())

    def test_history_write_failure_retries_without_duplicate_attachments(self):
        attempts = []
        def save(word):
            attempts.append(word)
            if len(attempts) == 1:
                raise OSError("disk unavailable")
            self.history.add(word)
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=lambda word: media_files()), patch("main.add_word_to_his_set", side_effect=save):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
            self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, {"hello"})
        self.assertEqual(self.remote.creates, 1)
        self.assertEqual(self.remote.uploaded, ["us.mp3"])

    def test_one_failed_word_does_not_mark_it_successful_or_block_the_next_word(self):
        words = [SimpleNamespace(word=word, note=None, note_images=()) for word in ("bad", "good")]
        self.bearer.acquire_words = Mock(return_value=words)
        def prepare(word):
            if word == "bad":
                raise PronunciationError("both providers failed")
            return media_files()
        with patch.object(self.agent, "_get_task_attachments_bytes", side_effect=prepare):
            with self.assertRaisesRegex(RuntimeError, "未完整同步"):
                self.bearer.bear_eudic_to_dida365()
        self.assertEqual(self.history, {"good"})
        self.assertNotIn("bad", self.remote.tasks)


if __name__ == "__main__":
    unittest.main()
