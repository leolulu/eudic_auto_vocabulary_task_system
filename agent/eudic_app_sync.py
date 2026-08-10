"""欧路桌面 App 私有同步协议的最小图片笔记客户端。

该协议通过实际客户端抓包确认，不属于欧路 OpenAPI。它只供一次性命令上传
图片笔记使用。同步凭据优先读取当前 Windows 用户的欧路桌面 App 配置；若不可用，
则回退读取项目 config.yaml 中的一对同步凭据。常驻后端读取图片仍使用官方
OpenAPI 密钥，不依赖这里的私有同步凭据。
"""

import base64
import configparser
import hashlib
import hmac
import io
import json
import mimetypes
import time
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from constants.yaml import CONFIG_FILE_NAME, EUDIC_SYNC_TOKEN, EUDIC_SYNC_USER_ID


EUDIC_APP_CONFIG_PATH = Path.home() / "AppData/Roaming/Francochinois/eudic/config.ini"
EUDIC_APP_BASE_URL = "https://api.frdic.com"
EUDIC_APP_UPLOAD_PATH = "/api/v2/appsupport/storeAttachments"
EUDIC_APP_SYNC_PATH = "/api/v2/customize/sync"
EUDIC_APP_USER_AGENT = "/eusoft_eudic_en_win32/13.6.8/E1482340/"
EUDIC_APP_URLSIGN_KEY = b"%5Gajiw3Wcf23j"
EUDIC_APP_IMAGE_MAX_BYTES = 50 * 1024 * 1024


class EudicAppSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedEudicImage:
    filename: str
    content: bytes


@dataclass(frozen=True)
class EudicSyncCredentials:
    token: str
    user_id: str
    source: str


class EudicAppSyncClient:
    def __init__(
        self,
        config_path: Path = EUDIC_APP_CONFIG_PATH,
        session=None,
        yaml_config_path: Path = Path(CONFIG_FILE_NAME),
    ) -> None:
        self.config_path = Path(config_path)
        self.yaml_config_path = Path(yaml_config_path)
        self.credentials = self._load_credentials()
        self.session = session or requests.Session()
        # 已验证的桌面 App 链路不读取系统代理；避免代理改写私有同步请求。
        self.session.trust_env = False

    @staticmethod
    def _credential_value(config, key: str) -> str:
        value = config.get(key, "")
        if value is None:
            return ""
        return str(value).strip().strip('"')

    def _load_app_credentials(self) -> EudicSyncCredentials | None:
        if not self.config_path.is_file():
            return None
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(self.config_path, encoding="utf-8-sig")
            common = parser["COMMON"]
        except (OSError, KeyError, configparser.Error):
            return None
        token = self._credential_value(common, "SyncToken")
        user_id = self._credential_value(common, "SyncUserId")
        if not token or not user_id:
            return None
        return EudicSyncCredentials(token, user_id, "eudic_app")

    def _load_yaml_credentials(self) -> EudicSyncCredentials | None:
        if not self.yaml_config_path.is_file():
            return None
        try:
            with self.yaml_config_path.open("r", encoding="utf-8") as config_file:
                config = yaml.safe_load(config_file)
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(config, dict):
            return None
        token = self._credential_value(config, EUDIC_SYNC_TOKEN)
        user_id = self._credential_value(config, EUDIC_SYNC_USER_ID)
        if not token or not user_id:
            return None
        return EudicSyncCredentials(token, user_id, "config_yaml")

    def _load_credentials(self) -> EudicSyncCredentials:
        # 两项凭据必须来自同一个来源。跨来源拼接可能组合出从未实际登录过的身份，
        # 也会让故障排查无法判断当前请求究竟使用了哪套配置。
        credentials = self._load_app_credentials() or self._load_yaml_credentials()
        if credentials is not None:
            return credentials
        raise EudicAppSyncError(
            "未找到可用的欧路图片同步凭据。请先安装并登录欧路桌面 App"
            f"（配置位置：{self.config_path}），或在 {self.yaml_config_path} 中填写 "
            f"{EUDIC_SYNC_TOKEN} 和 {EUDIC_SYNC_USER_ID}。"
        )

    def _authorization(self, path: str) -> str:
        auth_data = {
            "t": "ABI"
            + base64.b64encode(str(int(time.time()) + 0x12E70A7).encode()).decode(),
            "token": self.credentials.token,
            "urlsign": base64.b64encode(
                hmac.new(EUDIC_APP_URLSIGN_KEY, path.encode(), hashlib.sha1).digest()
            ).decode(),
            "userid": self.credentials.user_id,
            "v_dict": True,
        }
        encoded = base64.b64encode(
            json.dumps(auth_data, separators=(",", ":"), sort_keys=True).encode()
        ).decode()
        return f"QYN {encoded}"

    def _headers(self, path: str) -> dict[str, str]:
        return {
            "Authorization": self._authorization(path),
            "User-Agent": EUDIC_APP_USER_AGENT,
            "EudicUserAgent": EUDIC_APP_USER_AGENT,
            "Accept-Language": "zh-CN,en,*",
        }

    @staticmethod
    def _encode_sync(xml: str) -> str:
        compressor = zlib.compressobj(level=8, wbits=-15)
        compressed = compressor.compress(xml.encode()) + compressor.flush()
        return base64.b64encode(b"QY" + compressed).decode()

    @staticmethod
    def _empty_sync_xml(last_sync_timestamp: str) -> str:
        root = ET.Element(
            "EudicSync",
            {"version": "1.0", "lastSyncTimestamp": last_sync_timestamp},
        )
        for tag in (
            "StudyCategory",
            "StudyLists",
            "Annotations",
            "WordCards",
            "Sentences",
            "UserMemory",
            "Histories",
        ):
            ET.SubElement(root, tag)
        return ET.tostring(root, encoding="unicode")

    def _sync(self, xml: str) -> ET.Element:
        try:
            response = self.session.post(
                EUDIC_APP_BASE_URL + EUDIC_APP_SYNC_PATH,
                headers=self._headers(EUDIC_APP_SYNC_PATH),
                data={"productid": "23", "langid": "3", "msgz": self._encode_sync(xml)},
                timeout=(20, 40),
            )
            response.raise_for_status()
            return ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as error:
            raise EudicAppSyncError("欧路桌面 App 私有同步请求失败") from error

    def _server_timestamp(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        response_root = self._sync(self._empty_sync_xml(now))
        timestamp = response_root.attrib.get("serverTimestamp")
        if not timestamp:
            raise EudicAppSyncError("欧路桌面 App 同步响应缺少服务器时间戳")
        return timestamp

    @staticmethod
    def prepare_images(paths: list[Path]) -> list[PreparedEudicImage]:
        prepared = []
        seen_filenames = set()
        for path in paths:
            path = Path(path)
            try:
                content = path.read_bytes()
            except OSError as error:
                raise EudicAppSyncError(f"无法读取笔记图片 [{path}]：{error}") from error
            if not content:
                raise EudicAppSyncError(f"笔记图片 [{path}] 内容为空")
            if len(content) > EUDIC_APP_IMAGE_MAX_BYTES:
                raise EudicAppSyncError(
                    f"笔记图片 [{path}] 超过 {EUDIC_APP_IMAGE_MAX_BYTES // 1024 // 1024} MB 限制"
                )
            mime_type, _ = mimetypes.guess_type(path.name)
            if not mime_type or not mime_type.startswith("image/"):
                raise EudicAppSyncError(f"文件 [{path}] 不是受支持的图片类型")
            suffix = path.suffix.lower()
            digest = hashlib.sha256(content).hexdigest()[:16]
            filename = f"eudic-cli-{digest}{suffix}"
            if filename in seen_filenames:
                continue
            seen_filenames.add(filename)
            prepared.append(PreparedEudicImage(filename=filename, content=content))
        if not prepared:
            raise EudicAppSyncError("没有可上传的笔记图片")
        return prepared

    def _upload_images(self, images: list[PreparedEudicImage]) -> list[dict]:
        body = json.dumps(
            {"attachment": [{"id": image.filename, "type": "image"} for image in images]},
            separators=(",", ":"),
        )
        files = [
            (image.filename, (image.filename, io.BytesIO(image.content), "application/octet-stream"))
            for image in images
        ]
        files.append(("body", (None, body)))
        try:
            response = self.session.post(
                EUDIC_APP_BASE_URL + EUDIC_APP_UPLOAD_PATH,
                headers=self._headers(EUDIC_APP_UPLOAD_PATH),
                files=files,
                timeout=(20, 60),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise EudicAppSyncError("上传欧路笔记图片失败") from error

        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            data = data.get("attachment", data)
        if isinstance(data, dict):
            attachments = [data]
        elif isinstance(data, list):
            attachments = data
        else:
            attachments = []
        if len(attachments) != len(images) or any(
            not isinstance(item, dict) or not item.get("id") or not item.get("url")
            for item in attachments
        ):
            raise EudicAppSyncError("欧路笔记图片上传响应格式异常")
        attachments_by_name = {
            str(item.get("orgfilename") or "").lower(): item
            for item in attachments
        }
        try:
            return [attachments_by_name[image.filename.lower()] for image in images]
        except KeyError as error:
            raise EudicAppSyncError("欧路笔记图片上传响应与本地文件不匹配") from error

    def save_note_with_images(
        self,
        word: str,
        note: str,
        images: list[PreparedEudicImage],
    ) -> None:
        server_timestamp = self._server_timestamp()
        attachments = self._upload_images(images)
        metadata = {
            "font_style": "normal",
            "image_list": attachments,
            "public_status": 0,
        }
        serialized_note = (
            "<!--meta files "
            + json.dumps(metadata, separators=(",", ":"), ensure_ascii=False)
            + " -->"
            + note.replace(" ", "&nbsp;")
        )
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        root = ET.Element(
            "EudicSync",
            {"version": "1.0", "lastSyncTimestamp": server_timestamp},
        )
        for tag in ("StudyCategory", "StudyLists"):
            ET.SubElement(root, tag)
        annotations = ET.SubElement(root, "Annotations")
        ET.SubElement(
            annotations,
            "CustomizeListItem",
            {
                "word": word,
                "itemType": "-9999",
                "note": serialized_note,
                "hl": "",
                "addTimeP": now,
                "deleted": "0",
                "serverTimestamp": server_timestamp,
                "localTimestamp": now,
                "meta": "",
            },
        )
        for tag in ("WordCards", "Sentences", "UserMemory", "Histories"):
            ET.SubElement(root, tag)
        self._sync(ET.tostring(root, encoding="unicode"))
