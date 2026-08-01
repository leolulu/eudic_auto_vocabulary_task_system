import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from agent.eudic import Eudic, EudicNoteFetchError
from main import Bearer, compose_word_task_content


class EudicNoteIntegrationTest(unittest.TestCase):
    def test_get_note_returns_note_text(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "word": "hello",
                "note": "  **来源：**《Demo》\n\n> **hello** world  ",
            }
        }

        with patch("agent.eudic.requests.get", return_value=response) as request_get:
            note = Eudic("NIS test").get_note("hello")

        self.assertEqual(note, "**来源：**《Demo》\n\n> **hello** world")
        request_get.assert_called_once()
        self.assertEqual(request_get.call_args.kwargs["params"], {"language": "en", "word": "hello"})

    def test_get_note_allows_a_valid_null_note(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "word": "legacy",
                "note": None,
            }
        }

        with patch("agent.eudic.requests.get", return_value=response):
            self.assertIsNone(Eudic("NIS test").get_note("legacy"))

    def test_get_note_wraps_request_failures(self):
        with patch("agent.eudic.requests.get", side_effect=requests.Timeout("timed out")):
            with self.assertRaises(EudicNoteFetchError):
                Eudic("NIS test").get_note("hello")

    def test_get_note_rejects_an_invalid_payload_shape(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []

        with patch("agent.eudic.requests.get", return_value=response):
            with self.assertRaises(EudicNoteFetchError):
                Eudic("NIS test").get_note("hello")

    def test_acquire_words_skips_note_failures_without_marking_the_word(self):
        word = SimpleNamespace(
            word="hello",
            note=None,
            is_in_last_days_range=lambda days: True,
        )
        eudic = Mock()
        eudic.get_words_in_book.return_value = [word]
        eudic.get_note.side_effect = EudicNoteFetchError("temporary failure")
        bearer = Bearer.__new__(Bearer)
        bearer.agent = SimpleNamespace(eudic=eudic)

        with patch("main.if_exists_in_his_set", return_value=False):
            result = bearer.acquire_words(7, include_notes=True)

        self.assertEqual(result, [])
        eudic.get_note.assert_called_once_with("hello")

    def test_compose_task_content_places_note_between_phonetic_and_explanation(self):
        content = compose_word_task_content(
            "必应词典: /həˈləʊ/",
            "**来源：**《Demo》\n\n> **hello** world",
            "释义\n\n[通过web添加anki生词](http://example.test)",
        )

        self.assertEqual(
            content,
            "必应词典: /həˈləʊ/\n\n"
            "**来源：**《Demo》\n\n> **hello** world\n\n"
            "释义\n\n[通过web添加anki生词](http://example.test)",
        )

    def test_compose_task_content_keeps_legacy_words_without_notes(self):
        self.assertEqual(
            compose_word_task_content("音标", None, "释义"),
            "音标\n\n释义",
        )


if __name__ == "__main__":
    unittest.main()
