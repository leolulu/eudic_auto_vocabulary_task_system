import base64
import json
import tempfile
import unittest
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from agent.eudic import EudicNoteData, EudicNoteImage
from agent.eudic_app_sync import (
    EUDIC_APP_SYNC_PATH,
    EUDIC_APP_UPLOAD_PATH,
    EudicAppSyncClient,
    EudicAppSyncError,
    PreparedEudicImage,
)
from main import EudicNoteConflictError, publish_single_word
from utils.yaml_config_manager import YamlConfigManager


def make_response(*, content=b"", payload=None):
    response = Mock()
    response.content = content
    response.raise_for_status.return_value = None
    if payload is not None:
        response.json.return_value = payload
    return response


def decode_sync_payload(msgz: str) -> ET.Element:
    compressed = base64.b64decode(msgz)
    assert compressed.startswith(b"QY")
    return ET.fromstring(zlib.decompress(compressed[2:], wbits=-15))


class EudicAppSyncClientTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "config.ini"
        self.yaml_config_path = self.root / "config.yaml"
        self.config_path.write_text(
            "[COMMON]\n"
            "SyncToken=test-token\n"
            "SyncUserId=test-user-id\n",
            encoding="utf-8",
        )

    @staticmethod
    def decode_authorization(client, path=EUDIC_APP_SYNC_PATH):
        authorization = client._authorization(path)
        return json.loads(base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8"))

    def test_prepare_images_uses_content_hash_and_deduplicates(self):
        first = self.root / "first.PNG"
        second = self.root / "copy.png"
        first.write_bytes(b"same image")
        second.write_bytes(b"same image")

        prepared = EudicAppSyncClient.prepare_images([first, second])

        self.assertEqual(len(prepared), 1)
        self.assertRegex(prepared[0].filename, r"^eudic-cli-[0-9a-f]{16}\.png$")
        self.assertEqual(prepared[0].content, b"same image")

    def test_prepare_images_rejects_non_images(self):
        text_file = self.root / "note.txt"
        text_file.write_text("not an image", encoding="utf-8")

        with self.assertRaisesRegex(EudicAppSyncError, "不是受支持的图片"):
            EudicAppSyncClient.prepare_images([text_file])

    def test_missing_app_config_has_an_actionable_error(self):
        with self.assertRaisesRegex(
            EudicAppSyncError,
            "请先安装并登录.*eudic_sync_token.*eudic_sync_user_id",
        ):
            EudicAppSyncClient(
                self.root / "missing.ini",
                yaml_config_path=self.root / "missing.yaml",
            )

    def test_app_config_is_preferred_over_yaml_credentials(self):
        self.yaml_config_path.write_text(
            "eudic_sync_token: yaml-token\n"
            "eudic_sync_user_id: yaml-user-id\n",
            encoding="utf-8",
        )

        client = EudicAppSyncClient(
            self.config_path,
            yaml_config_path=self.yaml_config_path,
        )

        self.assertEqual(client.credentials.source, "eudic_app")
        auth = self.decode_authorization(client)
        self.assertEqual(auth["token"], "test-token")
        self.assertEqual(auth["userid"], "test-user-id")

    def test_missing_app_config_falls_back_to_yaml_credentials(self):
        self.yaml_config_path.write_text(
            "eudic_sync_token: yaml-token\n"
            "eudic_sync_user_id: yaml-user-id\n",
            encoding="utf-8",
        )

        client = EudicAppSyncClient(
            self.root / "missing.ini",
            yaml_config_path=self.yaml_config_path,
        )

        self.assertEqual(client.credentials.source, "config_yaml")
        auth = self.decode_authorization(client)
        self.assertEqual(auth["token"], "yaml-token")
        self.assertEqual(auth["userid"], "yaml-user-id")

    def test_incomplete_app_config_falls_back_as_a_complete_pair(self):
        self.config_path.write_text(
            "[COMMON]\nSyncToken=app-token-only\n",
            encoding="utf-8",
        )
        self.yaml_config_path.write_text(
            "eudic_sync_token: yaml-token\n"
            "eudic_sync_user_id: yaml-user-id\n",
            encoding="utf-8",
        )

        client = EudicAppSyncClient(
            self.config_path,
            yaml_config_path=self.yaml_config_path,
        )

        self.assertEqual(client.credentials.source, "config_yaml")
        self.assertEqual(client.credentials.token, "yaml-token")
        self.assertEqual(client.credentials.user_id, "yaml-user-id")

    def test_credentials_are_not_mixed_across_sources(self):
        self.config_path.write_text(
            "[COMMON]\nSyncToken=app-token-only\n",
            encoding="utf-8",
        )
        self.yaml_config_path.write_text(
            "eudic_sync_user_id: yaml-user-only\n",
            encoding="utf-8",
        )

        with self.assertRaises(EudicAppSyncError):
            EudicAppSyncClient(
                self.config_path,
                yaml_config_path=self.yaml_config_path,
            )

    def test_authorization_uses_only_persisted_token_and_user_id(self):
        client = EudicAppSyncClient(
            self.config_path,
            yaml_config_path=self.yaml_config_path,
        )

        auth = self.decode_authorization(client)

        self.assertNotIn("fl", auth)
        self.assertNotIn("lc", auth)
        self.assertEqual(auth["token"], "test-token")
        self.assertEqual(auth["userid"], "test-user-id")
        self.assertTrue(auth["t"].startswith("ABI"))
        self.assertTrue(auth["urlsign"])

    def test_save_note_uploads_then_syncs_annotation(self):
        session = Mock()
        session.post.side_effect = [
            make_response(
                content=b'<EudicSync serverTimestamp="20260809T080000"></EudicSync>'
            ),
            make_response(
                payload={
                    "data": {
                        "attachment": {
                            "id": "server-image-id",
                            "type": "image",
                            "url": "https://fs-gateway.frdic.com/image.jpg",
                            "thumb": "https://fs-gateway.frdic.com/thumb.jpg",
                            "orgfilename": "eudic-cli-abc.png",
                        }
                    }
                }
            ),
            make_response(content=b"<EudicSync></EudicSync>"),
        ]
        client = EudicAppSyncClient(
            self.config_path,
            session=session,
            yaml_config_path=self.yaml_config_path,
        )

        client.save_note_with_images(
            "hello",
            "two words",
            [PreparedEudicImage("eudic-cli-abc.png", b"image bytes")],
        )

        self.assertEqual(session.post.call_count, 3)
        self.assertTrue(session.post.call_args_list[0].args[0].endswith(EUDIC_APP_SYNC_PATH))
        self.assertTrue(session.post.call_args_list[1].args[0].endswith(EUDIC_APP_UPLOAD_PATH))
        upload_files = session.post.call_args_list[1].kwargs["files"]
        self.assertEqual(upload_files[0][0], "eudic-cli-abc.png")
        self.assertEqual(upload_files[0][1][1].getvalue(), b"image bytes")

        final_sync = session.post.call_args_list[2]
        root = decode_sync_payload(final_sync.kwargs["data"]["msgz"])
        annotation = root.find("./Annotations/CustomizeListItem")
        self.assertIsNotNone(annotation)
        self.assertEqual(annotation.attrib["word"], "hello")
        self.assertIn("server-image-id", annotation.attrib["note"])
        self.assertTrue(annotation.attrib["note"].endswith("two&nbsp;words"))
        for call in session.post.call_args_list:
            self.assertTrue(call.kwargs["headers"]["Authorization"].startswith("QYN "))


class YamlConfigTemplateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.yaml"

    def test_first_run_template_contains_optional_sync_credentials(self):
        with self.assertRaises(UserWarning):
            YamlConfigManager(self.config_path)

        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("eudic_sync_token", config)
        self.assertIn("eudic_sync_user_id", config)
        self.assertEqual(config["eudic_sync_token"], "")
        self.assertEqual(config["eudic_sync_user_id"], "")

    def test_existing_old_config_is_not_forced_to_add_optional_keys(self):
        self.config_path.write_text(
            yaml.safe_dump(YamlConfigManager.ESSENTIAL_CONFIG, allow_unicode=True),
            encoding="utf-8",
        )

        manager = YamlConfigManager(self.config_path)

        self.assertNotIn("eudic_sync_token", manager.get_all_config())
        self.assertNotIn("eudic_sync_user_id", manager.get_all_config())
        persisted = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("eudic_sync_token", persisted)
        self.assertNotIn("eudic_sync_user_id", persisted)


class PublishSingleWordWithImagesTest(unittest.TestCase):
    def setUp(self):
        self.path = Path("image.png")
        self.prepared = PreparedEudicImage("eudic-cli-hash.png", b"image")
        self.saved_note = EudicNoteData(
            text="context",
            images=(
                EudicNoteImage(
                    "server-id",
                    "https://fs-gateway.frdic.com/image.jpg",
                    "eudic-cli-hash.png",
                ),
            ),
        )

    def test_new_image_note_is_verified_before_word_is_added(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}]
        eudic.get_note_data.side_effect = [None, self.saved_note]

        with patch("main.EudicAppSyncClient") as app_client:
            app_client.prepare_images.return_value = [self.prepared]
            result = publish_single_word(
                eudic,
                "hello",
                "context",
                note_image_paths=[self.path],
            )

        self.assertEqual(result, "created")
        app_client.return_value.save_note_with_images.assert_called_once_with(
            "hello",
            "context",
            [self.prepared],
        )
        eudic.add_word.assert_called_once_with("hello")

    def test_retry_reuses_an_already_verified_image_note(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}]
        eudic.get_note_data.return_value = self.saved_note

        with patch("main.EudicAppSyncClient") as app_client:
            app_client.prepare_images.return_value = [self.prepared]
            result = publish_single_word(
                eudic,
                "hello",
                "context",
                note_image_paths=[self.path],
            )

        self.assertEqual(result, "created")
        app_client.return_value.save_note_with_images.assert_not_called()
        eudic.add_word.assert_called_once_with("hello")

    def test_existing_different_image_note_is_not_overwritten(self):
        eudic = Mock()
        eudic.get_word.return_value = {
            "word": "hello",
            "add_time": "2023-10-31T19:33:49Z",
        }
        eudic.get_note_data.return_value = EudicNoteData(
            text="context",
            images=(
                EudicNoteImage(
                    "other-id",
                    "https://fs-gateway.frdic.com/other.jpg",
                    "other.png",
                ),
            ),
        )

        with patch("main.EudicAppSyncClient") as app_client:
            app_client.prepare_images.return_value = [self.prepared]
            with self.assertRaises(EudicNoteConflictError):
                publish_single_word(
                    eudic,
                    "hello",
                    "context",
                    note_image_paths=[self.path],
                )

        app_client.return_value.save_note_with_images.assert_not_called()
        eudic.add_word.assert_not_called()


if __name__ == "__main__":
    unittest.main()
