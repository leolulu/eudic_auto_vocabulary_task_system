"""只读联调：Python 客户端 -> 独立 HTTP 服务 -> 百度 -> 视频 CDN。

通过 uv 执行。--local 临时启动项目内服务，不改真实配置，不写入欧路或滴答。
"""
import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.baidu_video_client import _service_endpoint, query_word_explanation_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="启动临时的无鉴权本地服务")
    parser.add_argument("--endpoint", help="覆盖本次联调的服务地址")
    parser.add_argument("--download", action="store_true", help="额外走原附件流程完整下载一个视频，仅保留内存字节")
    parser.add_argument("--download-word", choices=("evidence", "feature"), default="evidence")
    parser.add_argument("--output", default=str(ROOT / ".cache" / "baidu-video-integration.json"))
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT):
        parser.error("联调报告必须保存在项目目录中")
    process = None
    previous = {"BAIDU_VIDEO_SERVICE_URL": os.environ.get("BAIDU_VIDEO_SERVICE_URL")}
    report = {"startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "local": args.local, "authentication": "none", "checks": []}
    try:
        if args.local:
            child_env = {**os.environ, "PORT": "0"}
            process = subprocess.Popen(
                ["node", "scripts/dev.mjs"], cwd=ROOT / "baidu-video-service", env=child_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            lines = queue.Queue()
            def read_output():
                for line in process.stdout:
                    lines.put(line)
            threading.Thread(target=read_output, daemon=True).start()
            deadline = time.monotonic() + 10
            ready = False
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                match = re.search(r"Local API: (http://127\.0\.0\.1:\d+)", line)
                if match:
                    os.environ["BAIDU_VIDEO_SERVICE_URL"] = match[1] + "/v1/video"
                    ready = True
                    break
            else:
                raise RuntimeError("本地服务未能启动")
            if not ready:
                raise RuntimeError("本地服务未能启动")
        elif args.endpoint:
            os.environ["BAIDU_VIDEO_SERVICE_URL"] = args.endpoint
        endpoint = _service_endpoint()
        parts = urlsplit(endpoint)
        origin = f"{parts.scheme}://{parts.netloc}"
        report["endpoint"] = endpoint
        health = requests.get(origin + "/healthz", headers={"Origin": "https://public-client.example"}, timeout=(5, 35), allow_redirects=False)
        assert health.status_code == 200 and health.json().get("ok") is True, "服务健康检查失败"
        assert health.headers.get("Access-Control-Allow-Origin") == "*", "公开跨域响应头缺失"
        report["checks"].append({"name": "health", "status": health.status_code, "cors": health.headers.get("Access-Control-Allow-Origin"), "version": health.json().get("version")})
        urls = {}
        for word in ("evidence", "feature", "apricot"):
            started = time.monotonic()
            urls[word] = query_word_explanation_video(word)
            report["checks"].append({"name": word, "urls": urls[word], "elapsedMs": round((time.monotonic() - started) * 1000)})
        assert urls["evidence"] and urls["feature"], "参考单词没有视频，请核对上游覆盖变化"
        # A range request checks the real CDN without downloading the entire reference video.
        with requests.get(urls["feature"][0], headers={"Range": "bytes=0-1023"}, stream=True, timeout=(5, 20)) as video:
            video.raise_for_status()
            prefix = next(video.iter_content(1024))
            assert prefix[4:8] == b"ftyp", "CDN 返回内容不是预期 MP4"
            report["checks"].append({"name": "video-cdn-range", "status": video.status_code, "bytesRead": len(prefix), "mp4Header": True})
        if args.download:
            from agent.dida365 import Dida365Agent
            started = time.monotonic()
            with patch("agent.dida365.get_dictvoice_bytes", return_value=[]):
                files = Dida365Agent(None)._get_task_attachments_bytes(args.download_word)
            assert len(files) == 1, "原附件流程没有获取视频"
            filename, stream = files[0]
            data = stream.getbuffer()
            assert data[4:8] == b"ftyp", "完整附件不是预期 MP4"
            report["checks"].append({"name": "full-attachment-download", "word": args.download_word, "filename": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "elapsedMs": round((time.monotonic() - started) * 1000)})
        report["ok"] = True
    except Exception as error:
        report["ok"] = False
        report["errorType"] = type(error).__name__
        # Do not persist arbitrary exception messages or authentication data.
        raise
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report["localServerStopped"] = True
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
