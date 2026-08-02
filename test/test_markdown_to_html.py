import unittest

from utils.markdown_to_html_util import markdown_to_html


class MarkdownToHtmlEscapeTest(unittest.TestCase):
    def test_filename_markdown_escapes_render_without_visible_backslashes(self):
        markdown = (
            r"**来源：**《\[9volt\] Sousou no Frieren - 29 (S02E01) "
            r"(Dual Audio) (WEB 1080p HEVC EAC-3) \[E15A4F27\]\_x264.mp4》"
        )

        result = markdown_to_html(markdown)

        self.assertEqual(
            result,
            "<p><b>来源：</b>《&#91;9volt&#93; Sousou no Frieren - 29 (S02E01) "
            "(Dual Audio) (WEB 1080p HEVC EAC-3) "
            "&#91;E15A4F27&#93;&#95;x264.mp4》</p>",
        )
        self.assertNotIn("\\", result)

    def test_escaped_markdown_stays_literal_while_regular_markdown_renders(self):
        markdown = r"\*literal\* and **bold** and \[label\]\(target\)"

        result = markdown_to_html(markdown)

        self.assertEqual(
            result,
            "<p>&#42;literal&#42; and <b>bold</b> and "
            "&#91;label&#93;&#40;target&#41;</p>",
        )
        self.assertNotIn("<a ", result)

    def test_non_escapable_backslashes_are_preserved(self):
        self.assertEqual(
            markdown_to_html(r"C:\Media\Video"),
            r"<p>C:\Media\Video</p>",
        )

    def test_escaped_backslash_renders_as_one_literal_backslash(self):
        self.assertEqual(
            markdown_to_html(r"path\\name"),
            "<p>path&#92;name</p>",
        )


if __name__ == "__main__":
    unittest.main()
