import hashlib
import re
import time

import requests
from playwright.sync_api import expect, sync_playwright

"""
此脚本复制于anki-api的同名脚本
需要定期检查两者是否有差异
"""


def get_phonetic_by_youdao(word):
    try:
        url = "https://www.youdao.com/result?word={word}&lang=en"
        res = requests.get(url.format(word=word), timeout=10)
        for i in re.findall("per-phone.*?点击发音", res.text):
            if "美" in i:
                for i in re.findall(r"phonetic.*?span", i):
                    for i in re.findall(r"/(.*?)/", i):
                        return i.strip()
    except Exception as e:
        print(f"通过有道词典获取音标失败: {e}")
    return None


def get_phonetic_by_bing(word):
    try:
        url = "https://cn.bing.com/dict/search?q={word}"
        res = requests.get(url.format(word=word), timeout=10)
        for i in re.findall(r"美.*?\[(.*?)\]", res.text):
            return i.strip()
    except Exception as e:
        print(f"通过必应词典获取音标失败: {e}")
    return None


def get_phonetic_by_baidu(word):
    url = f"https://fanyi.baidu.com/mtpe-individual/transText?lang=en2zh&query={word}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="load", timeout=120000)
                phonetic_locator = page.get_by_text("美/")
                expect(phonetic_locator).to_be_visible(timeout=5000)
                phonetic_content = phonetic_locator.text_content()
                if phonetic_content is None:
                    return None
                else:
                    phonetic_match = re.findall(r"美/(.*?)/", phonetic_content)
                    if phonetic_match:
                        phonetic = phonetic_match[0]
                    else:
                        phonetic = None
            finally:
                browser.close()
    except Exception as e:
        print(f"使用playwright进行百度音标获取失败:\n {e}")
        return None
    return phonetic


def get_phonetic_by_ciba(word):
    query = word.strip()
    if not query:
        return None

    api_path = "/dictionary/word/query/web"
    client = "6"
    web_key = "1000006"
    web_secret = "7ece94d9f9c202b0d2ec557dg4r9bc"
    timestamp = str(int(time.time() * 1000))
    # 词霸网页客户端使用的签名参数，无需个人开发者 Key。
    signature_source = f"{api_path}{client}{web_key}{timestamp}{query}{web_secret}"
    signature = hashlib.md5(signature_source.encode("utf-8")).hexdigest()

    try:
        res = requests.get(
            f"https://dict.iciba.com{api_path}",
            params={
                "client": client,
                "key": web_key,
                "timestamp": timestamp,
                "word": query,
                "signature": signature,
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        message = data.get("message")
        if not isinstance(message, dict):
            raise ValueError(f"接口返回异常: {message}")

        base_info = message.get("baesInfo")
        symbols = base_info.get("symbols") if isinstance(base_info, dict) else None
        if isinstance(symbols, list):
            symbol = symbols[0] if symbols else None
        else:
            symbol = symbols
        if isinstance(symbol, dict) and (phonetic := str(symbol.get("ph_am") or "").strip()):
            return phonetic
    except Exception as e:
        print(f"通过金山词霸获取音标失败: {e}")
    return None


def get_phonetic(word):
    def _format(result):
        return f"美[{result}]"

    if result := get_phonetic_by_bing(word):
        print("通过必应词典获取音标成功...")
        return _format(result)
    if result := get_phonetic_by_youdao(word):
        print("通过有道词典获取音标成功...")
        return _format(result)
    if result := get_phonetic_by_baidu(word):
        print("通过百度翻译获取音标成功...")
        return _format(result)
    # 不推荐使用词霸，因为美式发音使用KK音标
    if result := get_phonetic_by_ciba(word):
        print("通过金山词霸获取音标成功...")
        return _format(result)

    return "空"


def get_all_phonetic(word: str) -> str:
    data = {
        "百度词典": get_phonetic_by_baidu(word),
        "必应词典": get_phonetic_by_bing(word),
        "有道词典": get_phonetic_by_youdao(word),
        "金山词霸": get_phonetic_by_ciba(word),
    }
    filtered_data = {k: v for k, v in data.items() if v is not None}
    result = "\n".join([f"{k}: /{v}/" for k, v in filtered_data.items()])
    if result == "":
        return "通过Api获取音标皆返回为None..."
    else:
        return result


def query_word_explanation_video(word: str) -> list[str] | None:
    url = f"https://fanyi.baidu.com/mtpe-individual/transText?query={word}&lang=en2zh"
    video_urls = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=120000)

        video_elements = page.query_selector_all("video")
        for video_element in video_elements:
            src = video_element.get_attribute("src")
            if src:
                video_urls.add(src)
            else:
                sources = video_element.query_selector_all("source")
                for source in sources:
                    src = source.get_attribute("src")
                    if src:
                        video_urls.add(src)
        browser.close()
    return list(video_urls)


if __name__ == "__main__":
    word = "all"
    print(get_all_phonetic(word))
    print(query_word_explanation_video(word))
