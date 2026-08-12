import io
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent.dida365 import Dida365Agent
from agent.eudic import (
    EUDIC_IMAGE_DOWNLOAD_TIMEOUT,
    Eudic,
    EudicNoteImage,
    EudicNoteImageDownloadError,
    parse_eudic_note,
)
from constants.eudic import EUDIC_NOTE_IMAGES_PLACEHOLDER
from dida365_project.models.attachment import Attachment
from dida365_project.models.upload_attachment import uploadAttachment
from main import Bearer, compose_word_task_content


def build_raw_note(text="Image context", images=None):
    metadata = {
        "font_style": "normal",
        "image_list": images or [],
        "public_status": 0,
    }
    return f"<!--meta files {json.dumps(metadata)} -->{text}"


class FakeTask:
    def __init__(self, title, content, attachments=None):
        self.id = "task-id"
        self.project_id = "project-id"
        self.title = title
        self.content = content
        self.attachments = attachments or []
        self.attachments_to_upload = set()
        self.task_dict = {
            "id": self.id,
            "projectId": self.project_id,
            "title": title,
            "content": content,
        }

    def update_content(self, content):
        self.content = content
        self.task_dict["content"] = content

    def add_upload_attachment_post_payload_by_bytes(self, *file_bytes_objs):
        self.attachments_to_upload.update(file_bytes_objs)


class DidaAttachmentMarkdownTest(unittest.TestCase):
    def test_image_attachment_uses_inline_image_markdown(self):
        attachment = Attachment(
            {
                "id": "image-id",
                "fileName": "context.jpg",
                "fileType": "IMAGE",
            }
        )

        self.assertEqual(
            attachment.content_file_string,
            "![image](image-id/context.jpg)",
        )

    def test_audio_attachment_keeps_file_markdown(self):
        attachment = Attachment(
            {
                "id": "audio-id",
                "fileName": "us.mp3",
                "fileType": "AUDIO",
            }
        )

        self.assertEqual(
            attachment.content_file_string,
            "![file](audio-id/us.mp3)",
        )

    def test_attachment_pattern_matches_file_and_image_references(self):
        content = "![file](audio/us.mp3)\n![image](image/context.jpg)"

        self.assertEqual(
            re.findall(uploadAttachment.FILE_PATTERN, content),
            ["![file](audio/us.mp3)", "![image](image/context.jpg)"],
        )


class EudicNoteImageParsingTest(unittest.TestCase):
    def test_parse_note_keeps_text_and_multiple_images(self):
        data = parse_eudic_note(
            build_raw_note(
                "First&nbsp;second",
                [
                    {
                        "id": "image-1",
                        "type": "image",
                        "url": "https://fs-gateway.frdic.com/1.jpg",
                        "orgfilename": "one.png",
                    },
                    {
                        "id": "image-2",
                        "type": "image",
                        "url": "https://fs-gateway.frdic.com/2.jpg",
                        "orgfilename": "two.jpg",
                    },
                ],
            )
        )

        self.assertEqual(data.text, "First second")
        self.assertEqual([image.image_id for image in data.images], ["image-1", "image-2"])
        self.assertEqual(data.images[0].original_filename, "one.png")

    def test_parse_note_supports_an_image_only_note(self):
        data = parse_eudic_note(
            build_raw_note(
                "",
                [{"id": "image-1", "type": "image", "url": "https://fs-gateway.frdic.com/1.jpg"}],
            )
        )

        self.assertEqual(data.text, "")
        self.assertEqual(len(data.images), 1)

    def test_parse_note_rejects_malformed_image_metadata(self):
        with self.assertRaises(ValueError):
            parse_eudic_note(build_raw_note(images=[{"id": "image-1", "type": "image"}]))

    def test_get_note_data_wraps_malformed_image_metadata(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {"note": build_raw_note(images=[{"id": "image-1", "type": "image"}])}
        }

        with patch("agent.eudic.requests.get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "图片元数据异常"):
                Eudic("NIS test").get_note_data("hello")


class EudicNoteImageDownloadTest(unittest.TestCase):
    def test_download_uses_eudic_auth_and_actual_content_type(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {"content-type": "image/jpeg; charset=binary"}
        response.content = b"\xff\xd8\xffimage"
        image = EudicNoteImage(
            image_id="ABC-123",
            url="https://fs-gateway.frdic.com/image.jpg",
            original_filename="original.png",
        )
        eudic = Eudic("NIS test")

        with patch("agent.eudic.requests.get", return_value=response) as request_get:
            downloaded = eudic.download_note_images((image,))

        self.assertEqual(downloaded[0][0], "eudic-note-01-abc-123.jpg")
        self.assertEqual(downloaded[0][1].getvalue(), response.content)
        self.assertEqual(request_get.call_args.kwargs["headers"], eudic.headers)
        self.assertEqual(request_get.call_args.kwargs["timeout"], EUDIC_IMAGE_DOWNLOAD_TIMEOUT)

    def test_download_rejects_a_non_eudic_url_before_request(self):
        image = EudicNoteImage("image-1", "https://attacker.example/image.jpg")

        with patch("agent.eudic.requests.get") as request_get:
            with self.assertRaisesRegex(EudicNoteImageDownloadError, "非欧路"):
                Eudic("NIS test").download_note_images((image,))

        request_get.assert_not_called()

    def test_download_rejects_non_image_or_empty_responses(self):
        image = EudicNoteImage("image-1", "https://fs-gateway.frdic.com/image.jpg")
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {"content-type": "text/html"}
        response.content = b"error"

        with patch("agent.eudic.requests.get", return_value=response):
            with self.assertRaisesRegex(EudicNoteImageDownloadError, "非图片"):
                Eudic("NIS test").download_note_images((image,))

    def test_download_wraps_network_failures(self):
        image = EudicNoteImage("image-1", "https://fs-gateway.frdic.com/image.jpg")

        with patch("agent.eudic.requests.get", side_effect=requests.Timeout("timeout")):
            with self.assertRaisesRegex(EudicNoteImageDownloadError, "下载欧路笔记图片"):
                Eudic("NIS test").download_note_images((image,))


class DidaNoteImagePlacementTest(unittest.TestCase):
    def test_add_task_resumes_a_partial_note_image_task(self):
        task = FakeTask("hello", f"context\n{EUDIC_NOTE_IMAGES_PLACEHOLDER}")
        dida = Mock()
        dida.active_tasks = [task]
        agent = Dida365Agent(dida)
        agent.find_task = Mock(return_value=task)
        agent._gen_dictvoice_and_upload_to_task_and_rearrange_content = Mock()
        image_files = [("eudic-note-01-image.jpg", io.BytesIO(b"image"))]

        result = agent.add_task("hello", "new generated content", note_image_files=image_files)

        self.assertIs(result, task)
        dida.post_task.assert_not_called()
        agent._gen_dictvoice_and_upload_to_task_and_rearrange_content.assert_called_once_with(
            task,
            note_image_files=image_files,
        )

    def test_add_task_accepts_a_fully_verified_previous_attempt(self):
        image = SimpleNamespace(
            file_name="eudic-note-01-image.jpg",
            content_file_string="![image](image/eudic-note-01-image.jpg)",
        )
        task = FakeTask("hello", image.content_file_string, attachments=[image])
        dida = Mock()
        dida.active_tasks = [task]
        agent = Dida365Agent(dida)
        agent._gen_dictvoice_and_upload_to_task_and_rearrange_content = Mock()

        result = agent.add_task(
            "hello",
            "new generated content",
            note_image_files=[("eudic-note-01-image.jpg", io.BytesIO(b"image"))],
        )

        self.assertIs(result, task)
        dida.post_task.assert_not_called()
        agent._gen_dictvoice_and_upload_to_task_and_rearrange_content.assert_not_called()

    def test_rearrange_places_note_images_at_context_and_audio_first(self):
        audio = SimpleNamespace(file_name="us.mp3", content_file_string="![file](audio/us.mp3)")
        image = SimpleNamespace(
            file_name="eudic-note-01-image.jpg",
            content_file_string="![image](image/eudic-note-01-image.jpg)",
        )
        task = FakeTask(
            "hello",
            f"phonetic\n\n**生词语境：**\n> context\n{EUDIC_NOTE_IMAGES_PLACEHOLDER}\n\nexplanation",
            attachments=[audio, image],
        )
        dida = Mock()
        agent = Dida365Agent(dida)
        agent.find_task = Mock(return_value=task)

        agent.rearrange_content_put_dictvoice_ahead(
            "hello",
            note_image_names=["eudic-note-01-image.jpg"],
        )

        self.assertEqual(
            task.content,
            "![file](audio/us.mp3)\n\n"
            "phonetic\n\n**生词语境：**\n> context\n"
            "![image](image/eudic-note-01-image.jpg)\n\nexplanation",
        )
        dida.post_task.assert_called_once()

    def test_rearrange_does_not_finalize_until_every_note_image_exists(self):
        task = FakeTask(
            "hello",
            f"context\n{EUDIC_NOTE_IMAGES_PLACEHOLDER}",
            attachments=[],
        )
        agent = Dida365Agent(Mock())
        agent.find_task = Mock(return_value=task)

        with patch("agent.dida365.sleep"), self.assertRaisesRegex(RuntimeError, "未能确认"):
            agent.rearrange_content_put_dictvoice_ahead(
                "hello",
                note_image_names=["missing.jpg"],
            )

        self.assertIn(EUDIC_NOTE_IMAGES_PLACEHOLDER, task.content)

    def test_attachment_generation_skips_an_already_uploaded_image(self):
        existing = SimpleNamespace(
            file_name="eudic-note-01-image.jpg",
            content_file_string="![image](image/eudic-note-01-image.jpg)",
        )
        task = FakeTask("hello", EUDIC_NOTE_IMAGES_PLACEHOLDER, attachments=[existing])
        dida = Mock()
        agent = Dida365Agent(dida)
        agent._get_task_attachments_bytes = Mock(return_value=[])
        agent.rearrange_content_put_dictvoice_ahead = Mock()

        agent._gen_dictvoice_and_upload_to_task_and_rearrange_content(
            task,
            note_image_files=[("eudic-note-01-image.jpg", io.BytesIO(b"image"))],
        )

        dida.upload_attachment.assert_not_called()
        agent.rearrange_content_put_dictvoice_ahead.assert_called_once()


class NoteImageSyncFlowTest(unittest.TestCase):
    def test_player_note_image_placeholder_follows_context_without_source_filename(self):
        content = compose_word_task_content(
            "phonetic",
            "**来源：**《A Very Long Filename.mp4》\n> **context** line",
            "explanation",
            note_image_count=1,
        )

        self.assertEqual(
            content,
            "phonetic\n\n**生词语境：**\n> **context** line\n"
            f"{EUDIC_NOTE_IMAGES_PLACEHOLDER}\n\nexplanation",
        )
        self.assertNotIn("A Very Long Filename.mp4", content)

    def test_compose_content_adds_placeholder_after_note(self):
        content = compose_word_task_content("phonetic", "context", "explanation", note_image_count=2)

        self.assertEqual(
            content,
            "phonetic\n\n**生词语境：**\n> context\n"
            f"{EUDIC_NOTE_IMAGES_PLACEHOLDER}\n\nexplanation",
        )

    def test_download_failure_stops_before_ai_and_dida(self):
        word = SimpleNamespace(word="hello", note="context", note_images=(Mock(),))
        eudic = Mock()
        eudic.download_note_images.side_effect = EudicNoteImageDownloadError("download failed")
        dida = Mock()
        bearer = Bearer.__new__(Bearer)
        bearer.agent = SimpleNamespace(eudic=eudic, dida=dida)
        bearer.acquire_words = Mock(return_value=[word])
        bearer.get_doubao_explanation_by_doubao = Mock()

        bearer.bear_eudic_to_dida365()

        bearer.get_doubao_explanation_by_doubao.assert_not_called()
        dida.add_task.assert_not_called()

    def test_failed_dida_sync_is_not_written_to_word_history(self):
        word = SimpleNamespace(word="hello", note="context", note_images=(Mock(),))
        eudic = Mock()
        eudic.download_note_images.return_value = [
            ("eudic-note-01-image.jpg", io.BytesIO(b"image"))
        ]
        dida = Mock()
        dida.add_task.side_effect = RuntimeError("upload failed")
        bearer = Bearer.__new__(Bearer)
        bearer.agent = SimpleNamespace(eudic=eudic, dida=dida)
        bearer.acquire_words = Mock(return_value=[word])
        bearer.get_doubao_explanation_by_doubao = Mock(return_value="explanation")

        with (
            patch("main.get_all_phonetic", return_value="phonetic"),
            patch("main.YamlConfigManager") as config_manager,
            patch("main.add_word_to_his_set") as add_to_history,
            patch("main.traceback.print_exc"),
        ):
            config_manager.return_value.get_config.return_value = "http://example.test"
            bearer.bear_eudic_to_dida365()

        add_to_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
