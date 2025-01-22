import pickle
import requests
import os


class Client:
    def __init__(self, session_pickle_file_path="default.session") -> None:
        self.session_pickle_file_path = session_pickle_file_path
        if os.path.exists(self.session_pickle_file_path):
            self._load_session()
        else:
            self.session = requests.Session()
            self._save_session()

    def _load_session(self):
        with open(self.session_pickle_file_path, "rb") as f:
            self.session = pickle.load(f)

    def _save_session(self):
        with open(self.session_pickle_file_path, "wb") as f:
            pickle.dump(self.session, f)

    def set_header(self, header):
        self.session.headers.update(header)

    def get(self, url):
        response = self.session.get(url)
        self._save_session()
        return response

    def post(self, url, data):
        response = self.session.post(url, data)
        self._save_session()
        return response
