import io
from enum import Enum, unique

import requests
from utils.logging_util import logged_request


DICTVOICE_REQUEST_TIMEOUT = (5, 30)


@unique
class VoiceType(Enum):
    # UK = 1
    US = 0


def request_dictvoice(type, word):
    url = "http://dict.youdao.com/dictvoice?type={type}&audio={word}"
    r = logged_request("media", requests.get, "GET", url.format(type=type, word=word), log_response_body=False,
        timeout=DICTVOICE_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.content


def get_dictvoice_bytes(word):
    result = []
    for type_name, type_value in VoiceType.__members__.items():
        type_value = type_value.value
        r_content = request_dictvoice(type_value, word)
        # 添加.mp3扩展名
        result.append((type_name.lower() + ".mp3", io.BytesIO(r_content)))
    return result
