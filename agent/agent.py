from agent.dida365 import Dida365Agent
from agent.doubao import Doubao
from agent.doubao_online import DoubaoOnline
from agent.eudic import Eudic
from constants.yaml import DIDA365_PASSWORD, DIDA365_USERNAME, DOUBAO_WEBSERVER_ENDPOINT, EUDIC_API_KEY
from dida365_project.api.dida365 import Dida365 as Dida365Api
from utils.yaml_config_manager import YamlConfigManager


class Agent:
    def __init__(self) -> None:
        self.config_manager = YamlConfigManager()
        self.dida = self.get_dida()
        self.doubao = self.get_doubao()
        self.eudic = self.get_eudic()

    def get_dida(self):
        return Dida365Agent(
            Dida365Api(
                username=self.config_manager.get_config(DIDA365_USERNAME),
                password=self.config_manager.get_config(DIDA365_PASSWORD),
            ),
        )

    def get_doubao(self):
        # self.doubao = Doubao(
        #     api_key=self.config_manager.get_config(DOUBAO_API_KEY),
        #     access_point=self.config_manager.get_config(DOUBAO_MODEL_ACCESS_POINT),
        # )
        return DoubaoOnline(endpoint=self.config_manager.get_config(DOUBAO_WEBSERVER_ENDPOINT))

    def get_eudic(self):
        return Eudic(api_key=self.config_manager.get_config(EUDIC_API_KEY))

    def substitute_new_doubao_agent(self):
        # self.doubao = Doubao(
        #     api_key=self.config_manager.get_config(DOUBAO_API_KEY),
        #     access_point=self.config_manager.get_config(DOUBAO_MODEL_ACCESS_POINT),
        # )
        self.doubao = DoubaoOnline(endpoint=self.config_manager.get_config(DOUBAO_WEBSERVER_ENDPOINT))
