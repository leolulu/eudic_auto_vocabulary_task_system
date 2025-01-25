import pickle

from constants.db import DB_FILE_PATH


def get_or_create_his_set():
    try:
        with open(DB_FILE_PATH, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        data = set()
        with open(DB_FILE_PATH, "wb") as f:
            pickle.dump(data, f)
        return data


def add_word_to_his_set(word):
    his_set = get_or_create_his_set()
    his_set.add(word)
    with open(DB_FILE_PATH, "wb") as f:
        pickle.dump(his_set, f)


def if_exists_in_his_set(word):
    his_set = get_or_create_his_set()
    return word in his_set
