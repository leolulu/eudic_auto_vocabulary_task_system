import unittest
from unittest.mock import patch

from agent.eudic import Eudic


class EudicWordListingTest(unittest.TestCase):
    def test_get_words_in_book_calculates_beijing_cutoff(self):
        eudic = Eudic("NIS test")
        page_data = [
            {
                "word": "hello",
                "exp": "greeting",
                "add_time": "2099-01-01T00:00:00Z",
            }
        ]

        with (
            patch.object(eudic, "_find_last_page", return_value=0),
            patch.object(eudic, "_fetch_page", return_value=page_data),
        ):
            words = eudic.get_words_in_book(
                vocab_book_id="book-id",
                days=7,
            )

        self.assertEqual([word.word for word in words], ["hello"])


if __name__ == "__main__":
    unittest.main()
