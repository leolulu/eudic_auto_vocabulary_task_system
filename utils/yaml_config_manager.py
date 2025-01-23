import os

import yaml

from constants.yaml import CONFIG_FILE_NAME, DOUBAO_API_KEY, DOUBAO_MODEL_ACCESS_POINT, EUDIC_API_KEY, DIDA365_USERNAME, DIDA365_PASSWORD


class YamlConfigManager:
    def __init__(self, config_file_path=CONFIG_FILE_NAME) -> None:
        self.config_file_path = config_file_path
        self.config = dict()
        if not os.path.exists(self.config_file_path):
            self.save_config(
                {
                    EUDIC_API_KEY: "请输入欧路词典API密钥",
                    DOUBAO_API_KEY: "请输入豆包API密钥",
                    DOUBAO_MODEL_ACCESS_POINT: "请输入豆包模型在线推理接入点",
                    DIDA365_USERNAME: "请输入Dida365用户名",
                    DIDA365_PASSWORD: "请输入Dida365密码",
                }
            )
            raise UserWarning(f"配置文件不存在，已生成默认配置文件：{self.config_file_path}")
        self.config = self.load_config()

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
