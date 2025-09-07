import requests

from constants.eudic import DEFAULT_VOCAB_BOOK_NAME, GET_WORD_URL, VOCAB_BOOK_BASE_URL, VOCAB_BOOK_ID, VOCAB_BOOK_NAME
from constants.header import HEADER_AUTHORIZATION, HEADER_USER_AGENT
from models.eudic_word import Word


class Eudic:
    def __init__(self, api_key) -> None:
        self.api_key = api_key
        self.headers = {
            HEADER_AUTHORIZATION: self.api_key,
            HEADER_USER_AGENT: "",  # 不带这一项会报错，值随便填，这里留空
        }

    def get_vocab_book(self):
        url = VOCAB_BOOK_BASE_URL
        params = {
            "language": "en",
        }
        res = requests.get(url, headers=self.headers, params=params)
        try:
            res.raise_for_status()
        except:
            print(res.text)
            raise
        return res.json()

    def get_default_vocab_book_id(self):
        data = self.get_vocab_book()["data"]
        for book_info in data:
            if book_info[VOCAB_BOOK_NAME] == DEFAULT_VOCAB_BOOK_NAME:
                return book_info[VOCAB_BOOK_ID]
        raise UserWarning(f"未找到默认生词本，请检查原始数据：{data}")

    def get_words_in_book(self, vocab_book_id=None, days=1):
        url = GET_WORD_URL
        params = {
            "language": "en",
            # "page":1, "page_size":2   #先不加，等以后万一超出返回限制的时候，再考虑循环拉取分页数据
        }
        res = requests.get(url, headers=self.headers, params=params)

        try:
            res.raise_for_status()
        except:
            print(res.content)
            raise

        return [Word(w) for w in res.json()["data"]]
