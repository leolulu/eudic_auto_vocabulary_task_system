from agent.dida365 import Dida365Agent
from agent.doubao import Doubao
from agent.eudic import Eudic
from constants.yaml import DIDA365_PASSWORD, DIDA365_USERNAME, DOUBAO_API_KEY, DOUBAO_MODEL_ACCESS_POINT, EUDIC_API_KEY
from dida365_project.api.dida365 import Dida365 as Dida365Api
from utils.yaml_config_manager import YamlConfigManager


class Agent:
    def __init__(self) -> None:
        self.config_manager = YamlConfigManager()

        self.dida = Dida365Agent(
            Dida365Api(
                username=self.config_manager.get_config(DIDA365_USERNAME),
                password=self.config_manager.get_config(DIDA365_PASSWORD),
            ),
        )

        self.doubao = Doubao(
            api_key=self.config_manager.get_config(DOUBAO_API_KEY),
            access_point=self.config_manager.get_config(DOUBAO_MODEL_ACCESS_POINT),
        )

        self.eudic = Eudic(api_key=self.config_manager.get_config(EUDIC_API_KEY))

    def substitute_new_doubao_agent(self):
        self.doubao = Doubao(
            api_key=self.config_manager.get_config(DOUBAO_API_KEY),
            access_point=self.config_manager.get_config(DOUBAO_MODEL_ACCESS_POINT),
        )
