"""手动执行一条带图片笔记的欧路 -> 滴答真实写入验收。

该脚本会真实创建数据，仅用于选定的全新测试词。未提供 ``--execute`` 时只显示
将要执行的对象，不发出写请求。滴答登录处于冷却时，欧路发布完成后会以退出码
75 停止；稍后用完全相同的命令重试即可复用已经验证的欧路笔记。
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.eudic import Eudic
from constants.eudic import EUDIC_NOTE_IMAGES_PLACEHOLDER
from constants.yaml import ANKI_PUSH_ENDPOINT, EUDIC_API_KEY
from dida365_project.api.dida365 import (
    DidaLoginCooldownError,
    DidaSessionValidationError,
    DidaSignInError,
)
from main import Bearer, compose_word_task_content, publish_single_word
from utils.phonetic_util import get_all_phonetic
from utils.word_his_db import add_word_to_his_set
from utils.yaml_config_manager import YamlConfigManager


def build_parser():
    parser = argparse.ArgumentParser(description="真实验收欧路图片笔记到滴答任务的完整链路")
    parser.add_argument("--word", required=True, help="专门用于测试、此前不存在的英文单词")
    parser.add_argument("--note", default="", help="图片笔记文字，可留空")
    parser.add_argument("--image", required=True, action="append", help="本地图片；可重复指定")
    parser.add_argument("--execute", action="store_true", help="确认执行真实欧路和滴答写入")
    return parser


def main():
    args = build_parser().parse_args()
    word = args.word.strip().lower()
    image_paths = [Path(path).resolve() for path in args.image]
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "word": word,
                    "note": args.note,
                    "images": [str(path) for path in image_paths],
                    "next": "确认测试词和图片无误后追加 --execute",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    config = YamlConfigManager()
    eudic = Eudic(config.get_config(EUDIC_API_KEY))
    publish_single_word(
        eudic,
        word,
        args.note or None,
        note_image_paths=image_paths,
    )

    try:
        bearer = Bearer()
    except (DidaLoginCooldownError, DidaSessionValidationError, DidaSignInError) as error:
        print(error, file=sys.stderr)
        print("欧路记录已经就绪；滴答登录恢复后用相同命令重试。", file=sys.stderr)
        return 75

    note_data = bearer.agent.eudic.get_note_data(word)
    if note_data is None or not note_data.images:
        raise RuntimeError("OpenAPI 回读未发现欧路笔记图片")
    note_image_files = bearer.agent.eudic.download_note_images(note_data.images)
    explanation = bearer.get_doubao_explanation_by_doubao(word)
    explanation += (
        "\n\n[通过web添加anki生词]("
        + f"{config.get_config(ANKI_PUSH_ENDPOINT)}?word={quote(word)}"
        + ")"
    )
    content = compose_word_task_content(
        get_all_phonetic(word),
        note_data.text or None,
        explanation,
        note_image_count=len(note_image_files),
    )
    bearer.agent.dida.add_task(
        word,
        content,
        note_image_files=note_image_files,
    )

    task = bearer.agent.dida.find_task(word, if_reload_data=True)
    attachments_by_name = {
        attachment.file_name.lower(): attachment
        for attachment in task.attachments
    }
    expected_names = [filename.lower() for filename, _ in note_image_files]
    missing_attachments = [name for name in expected_names if name not in attachments_by_name]
    missing_references = [
        name
        for name in expected_names
        if name in attachments_by_name
        and attachments_by_name[name].content_file_string not in (task.content or "")
    ]
    if missing_attachments or missing_references:
        raise RuntimeError(
            f"滴答读回校验失败：缺少附件={missing_attachments}，缺少正文引用={missing_references}"
        )
    if EUDIC_NOTE_IMAGES_PLACEHOLDER in (task.content or ""):
        raise RuntimeError("滴答任务仍残留图片同步占位符")
    if "&nbsp;" in (task.content or ""):
        raise RuntimeError("滴答任务仍残留 &nbsp; 实体")

    add_word_to_his_set(word)
    print(
        json.dumps(
            {
                "success": True,
                "word": word,
                "eudic_image_count": len(note_data.images),
                "dida_image_filenames": expected_names,
                "placeholder_removed": True,
                "nbsp_removed": True,
                "history_recorded": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
