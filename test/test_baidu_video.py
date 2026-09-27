import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from utils.phonetic_util import query_word_explanation_video


class BaiduVideoQueryTest(unittest.TestCase):
    def test_node_video_result_skips_playwright(self):
        output = [{"ok": True, "hasVideo": True, "video": {"videoUrl": "https://example.test/video.mp4"}}]
        with (
            patch(
                "utils.phonetic_util.subprocess.run",
                return_value=SimpleNamespace(stdout=json.dumps(output)),
            ) as run,
            patch("utils.phonetic_util.sync_playwright") as playwright,
        ):
            self.assertEqual(
                query_word_explanation_video("evidence"),
                ["https://example.test/video.mp4"],
            )

        self.assertEqual(run.call_args.args[0][0], "node")
        self.assertEqual(run.call_args.args[0][-2:], ["--json", "evidence"])
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        playwright.assert_not_called()

    def test_confirmed_no_video_skips_playwright(self):
        output = [{"ok": True, "hasVideo": False, "video": None}]
        with (
            patch(
                "utils.phonetic_util.subprocess.run",
                return_value=SimpleNamespace(stdout=json.dumps(output)),
            ),
            patch("utils.phonetic_util.sync_playwright") as playwright,
        ):
            self.assertEqual(query_word_explanation_video("unknown"), [])

        playwright.assert_not_called()

    def test_node_failures_use_playwright(self):
        cases = [
            ("node missing", None, FileNotFoundError("node")),
            ("node failed", None, subprocess.CalledProcessError(1, ["node"])),
            ("node timed out", None, subprocess.TimeoutExpired(["node"], 60)),
            ("invalid json", "invalid json", None),
            ("upstream failed", json.dumps([{"ok": False, "failure": "ACS_REJECTED"}]), None),
        ]
        for name, stdout, error in cases:
            with self.subTest(name=name):
                video = Mock()
                video.get_attribute.return_value = "https://example.test/fallback.mp4"
                with (
                    patch(
                        "utils.phonetic_util.subprocess.run",
                        side_effect=error,
                        return_value=SimpleNamespace(stdout=stdout),
                    ),
                    patch("utils.phonetic_util.sync_playwright") as playwright,
                    patch("builtins.print"),
                ):
                    browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
                    page = browser.new_page.return_value
                    page.query_selector_all.return_value = [video]
                    self.assertEqual(
                        query_word_explanation_video("evidence"),
                        ["https://example.test/fallback.mp4"],
                    )
                    playwright.assert_called_once()


if __name__ == "__main__":
    unittest.main()
