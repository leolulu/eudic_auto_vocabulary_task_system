import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

from agent.eudic import EUDIC_REQUEST_TIMEOUT, Eudic, EudicNoteFetchError, EudicWordFetchError, EudicWriteError
from constants.eudic import GET_NOTE_URL, WORD_URL
from main import (
    EudicNoteConflictError,
    EudicPublishError,
    build_argument_parser,
    format_eudic_add_time,
    publish_single_word,
    resolve_note_argument,
)


class EudicWriteClientTest(unittest.TestCase):
    def test_get_word_accepts_the_documented_direct_payload(self):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {"word": "hello", "exp": ""}

        with patch("agent.eudic.requests.get", return_value=response) as request_get:
            result = Eudic("NIS test").get_word("hello")

        self.assertEqual(result, {"word": "hello", "exp": ""})
        self.assertEqual(request_get.call_args.args[0], WORD_URL)
        self.assertEqual(request_get.call_args.kwargs["params"], {"language": "en", "word": "hello"})
        self.assertEqual(request_get.call_args.kwargs["timeout"], EUDIC_REQUEST_TIMEOUT)

    def test_get_word_returns_none_for_an_empty_data_collection_or_404(self):
        empty_response = Mock(status_code=200)
        empty_response.raise_for_status.return_value = None
        empty_response.json.return_value = {"data": []}
        missing_response = Mock(status_code=404)

        with patch("agent.eudic.requests.get", side_effect=[empty_response, missing_response]):
            eudic = Eudic("NIS test")
            self.assertIsNone(eudic.get_word("missing"))
            self.assertIsNone(eudic.get_word("missing"))

    def test_get_word_wraps_request_failures(self):
        with patch("agent.eudic.requests.get", side_effect=requests.Timeout("timed out")):
            with self.assertRaises(EudicWordFetchError):
                Eudic("NIS test").get_word("hello")

    def test_save_note_posts_the_exact_multiline_text(self):
        response = Mock()
        response.raise_for_status.return_value = None
        note = "first line\nsecond line"

        with patch("agent.eudic.requests.post", return_value=response) as request_post:
            Eudic("NIS test").save_note("hello", note)

        self.assertEqual(request_post.call_args.args[0], GET_NOTE_URL)
        self.assertEqual(
            request_post.call_args.kwargs["json"],
            {"language": "en", "word": "hello", "note": note},
        )
        self.assertEqual(request_post.call_args.kwargs["timeout"], EUDIC_REQUEST_TIMEOUT)

    def test_add_word_posts_to_the_single_word_endpoint(self):
        response = Mock()
        response.raise_for_status.return_value = None

        with patch("agent.eudic.requests.post", return_value=response) as request_post:
            Eudic("NIS test").add_word("hello")

        self.assertEqual(request_post.call_args.args[0], WORD_URL)
        self.assertEqual(request_post.call_args.kwargs["json"], {"language": "en", "word": "hello"})

    def test_write_failures_are_not_retried_inside_the_client(self):
        with patch("agent.eudic.requests.post", side_effect=requests.ReadTimeout("uncertain")) as request_post:
            with self.assertRaises(EudicWriteError):
                Eudic("NIS test").add_word("hello")

        request_post.assert_called_once()


class ManualEudicPublishTest(unittest.TestCase):
    def test_new_note_is_verified_before_the_word_is_added(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}]
        eudic.get_note.side_effect = [None, "first line\nsecond line"]

        result = publish_single_word(eudic, " Hello ", " first line\r\nsecond line ")

        self.assertEqual(result, "created")
        self.assertEqual(
            eudic.method_calls,
            [
                call.get_word("hello"),
                call.get_note("hello"),
                call.save_note("hello", "first line\nsecond line"),
                call.get_note("hello"),
                call.add_word("hello"),
                call.get_word("hello"),
            ],
        )

    def test_retry_reuses_an_identical_orphan_note(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}]
        eudic.get_note.return_value = "context"

        result = publish_single_word(eudic, "hello", "context")

        self.assertEqual(result, "created")
        eudic.save_note.assert_not_called()
        eudic.add_word.assert_called_once_with("hello")

    def test_note_write_error_is_reconciled_before_publishing_the_word(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}]
        eudic.get_note.side_effect = [None, "context", "context"]
        eudic.save_note.side_effect = EudicWriteError("uncertain")

        result = publish_single_word(eudic, "hello", "context")

        self.assertEqual(result, "created")
        eudic.add_word.assert_called_once_with("hello")

    def test_different_orphan_note_stops_before_adding_the_word(self):
        eudic = Mock()
        eudic.get_word.return_value = None
        eudic.get_note.return_value = "old context"

        with self.assertRaises(EudicNoteConflictError):
            publish_single_word(eudic, "hello", "new context")

        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_missing_note_after_a_failed_save_stops_before_adding_the_word(self):
        eudic = Mock()
        eudic.get_word.return_value = None
        eudic.get_note.side_effect = [None, None]
        eudic.save_note.side_effect = EudicWriteError("failed")

        with self.assertRaises(EudicPublishError):
            publish_single_word(eudic, "hello", "context")

        eudic.add_word.assert_not_called()

    def test_note_read_failure_stops_before_adding_the_word(self):
        eudic = Mock()
        eudic.get_word.return_value = None
        eudic.get_note.side_effect = EudicNoteFetchError("temporary failure")

        with self.assertRaises(EudicPublishError):
            publish_single_word(eudic, "hello", "context")

        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_word_write_error_is_reconciled_by_reading_the_word(self):
        eudic = Mock()
        eudic.get_word.side_effect = [None, {"word": "hello"}, {"word": "hello"}]
        eudic.add_word.side_effect = EudicWriteError("uncertain")

        result = publish_single_word(eudic, "hello")

        self.assertEqual(result, "created")
        eudic.add_word.assert_called_once_with("hello")

    def test_existing_complete_record_is_an_idempotent_success(self):
        eudic = Mock()
        eudic.get_word.return_value = {
            "word": "hello",
            "add_time": "2023-10-31T19:33:49Z",
        }
        eudic.get_note.return_value = "context"

        with patch("builtins.print") as print_mock:
            result = publish_single_word(eudic, "hello", "context")

        self.assertEqual(result, "existing")
        print_mock.assert_called_once_with(
            "单词 [hello] 及笔记已完整存在于欧路词典"
            "（单词添加于：2023-11-01 11:33:49 北京时间），无需重复添加。",
        )
        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_existing_word_with_a_different_note_is_a_conflict(self):
        eudic = Mock()
        eudic.get_word.return_value = {
            "word": "hello",
            "add_time": "2023-10-31T19:33:49Z",
        }
        eudic.get_note.return_value = "old context"

        with self.assertRaisesRegex(
            EudicNoteConflictError,
            "添加于：2023-11-01 11:33:49 北京时间.*笔记与本次输入不同",
        ):
            publish_single_word(eudic, "hello", "new context")

        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_existing_historical_word_without_note_is_not_backfilled(self):
        eudic = Mock()
        eudic.get_word.return_value = {
            "word": "hello",
            "add_time": "2023-10-31T19:33:49Z",
        }
        eudic.get_note.return_value = None

        with self.assertRaisesRegex(
            EudicNoteConflictError,
            "添加于：2023-11-01 11:33:49 北京时间.*不会给历史生词补写笔记",
        ):
            publish_single_word(eudic, "hello", "new context")

        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_existing_word_without_a_note_argument_reports_add_time_without_claiming_completeness(self):
        eudic = Mock()
        eudic.get_word.return_value = {
            "word": "hello",
            "add_time": "2023-10-31T19:33:49Z",
        }

        with patch("builtins.print") as print_mock:
            result = publish_single_word(eudic, "hello")

        self.assertEqual(result, "existing")
        print_mock.assert_called_once_with(
            "单词 [hello] 已存在于欧路生词本"
            "（添加于：2023-11-01 11:33:49 北京时间），无需重复添加。",
        )
        eudic.get_note.assert_not_called()
        eudic.save_note.assert_not_called()
        eudic.add_word.assert_not_called()

    def test_existing_word_with_missing_or_invalid_add_time_uses_a_clear_fallback(self):
        self.assertEqual(format_eudic_add_time({}), "添加时间未知")
        self.assertEqual(format_eudic_add_time({"add_time": "invalid"}, subject="单词"), "单词添加时间未知")


class ManualNoteArgumentTest(unittest.TestCase):
    def test_note_file_preserves_internal_markdown_and_normalizes_newlines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note_path = Path(temp_dir, "note.md")
            note_path.write_bytes(b"\xef\xbb\xbf  **source**\r\n> context\r\n")
            parser = build_argument_parser()
            args = parser.parse_args(["--add-word", "hello", "--note-file", str(note_path)])

            note = resolve_note_argument(parser, args)

        self.assertEqual(note, "**source**\n> context")

    def test_note_requires_add_word(self):
        parser = build_argument_parser()
        args = parser.parse_args(["--note", "context"])

        with self.assertRaises(SystemExit):
            resolve_note_argument(parser, args)

    def test_note_and_note_file_are_mutually_exclusive(self):
        parser = build_argument_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--add-word", "hello", "--note", "context", "--note-file", "note.md"])

    def test_explicit_blank_note_is_rejected(self):
        parser = build_argument_parser()
        args = parser.parse_args(["--add-word", "hello", "--note", "  "])

        with self.assertRaises(SystemExit):
            resolve_note_argument(parser, args)


if __name__ == "__main__":
    unittest.main()
