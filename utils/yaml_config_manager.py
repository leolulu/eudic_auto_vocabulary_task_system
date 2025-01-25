import os

import yaml

from constants.yaml import (
    ANKI_PUSH_ENDPOINT,
    CONFIG_FILE_NAME,
    DOUBAO_WEBSERVER_ENDPOINT,
    EUDIC_API_KEY,
    DIDA365_USERNAME,
    DIDA365_PASSWORD,
)


class YamlConfigManager:
    ESSENTIAL_CONFIG = {
        EUDIC_API_KEY: "请输入欧路词典API密钥",
        DOUBAO_WEBSERVER_ENDPOINT: "请输入豆包API地址",
        DIDA365_USERNAME: "请输入Dida365用户名",
        DIDA365_PASSWORD: "请输入Dida365密码",
        ANKI_PUSH_ENDPOINT: "请输入Anki添加新词的API地址",
    }

    def __init__(self, config_file_path=CONFIG_FILE_NAME) -> None:
        self.config_file_path = config_file_path
        self.config = dict()
        if not os.path.exists(self.config_file_path):
            self.save_config(YamlConfigManager.ESSENTIAL_CONFIG)
            raise UserWarning(f"配置文件不存在，已生成默认配置文件：{self.config_file_path}")
        else:
            _config = self.load_config()
            missing_config = False
            for key in YamlConfigManager.ESSENTIAL_CONFIG.keys():
                if key not in _config:
                    _config[key] = YamlConfigManager.ESSENTIAL_CONFIG[key]
                    missing_config = True
            if missing_config:
                self.save_config(_config)
                raise UserWarning(f"配置文件缺少必要配置，需要补全：{self.config_file_path}")
            else:
                self.config = _config

    def get_all_config(self):
        return self.config

    def get_config(self, key):
        return self.config[key]

    def load_config(self):
        with open(self.config_file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def save_config(self, config=None):
        with open(self.config_file_path, "w", encoding="utf-8") as f:
            if config is not None:
                c = config
            else:
                c = self.config
            yaml.dump(c, f, allow_unicode=True)
