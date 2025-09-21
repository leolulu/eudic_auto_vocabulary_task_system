import re

import requests
from playwright.sync_api import expect, sync_playwright

"""
此脚本复制于anki-api的同名脚本
需要定期检查两者是否有差异
"""


def get_phonetic_by_youdao(word):
    url = "https://www.youdao.com/result?word={word}&lang=en"
    res = requests.get(url.format(word=word))
    for i in re.findall("per-phone.*?点击发音", res.text):
        if "美" in i:
            for i in re.findall(r"phonetic.*?span", i):
                for i in re.findall(r"/(.*?)/", i):
                    return i.strip()
    return None


def get_phonetic_by_bing(word):
    url = "https://cn.bing.com/dict/search?q={word}"
    res = requests.get(url.format(word=word))
    for i in re.findall(r"美.*?\[(.*?)\]", res.text):
        return i.strip()
    return None


def get_phonetic_by_baidu(word):
    url = f"https://fanyi.baidu.com/mtpe-individual/transText?lang=en2zh&query={word}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
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
        except Exception as e:
            print(f"使用playwright进行百度音标获取失败:\n {e}")
            return None
        finally:
            browser.close()
    return phonetic


def get_phonetic_by_ciba(word):
    url = "https://dict-co.iciba.com/api/dictionary.php?key=AA6C7429C3884C9E766C51187BD1D86F&type=json&w={word}"
    res = requests.get(url.format(word=word))
    data = res.json()
    try:
        p = data["symbols"][0]["ph_am"]
        if p.strip():
            return p.strip()
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


if __name__ == "__main__":
    print(get_all_phonetic("Assumi"))
