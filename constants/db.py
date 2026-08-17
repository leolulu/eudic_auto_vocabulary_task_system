from pathlib import Path


DB_FILE_PATH = "word_his.db"
RUNTIME_DB_FILE_PATH = Path(__file__).resolve().parents[1] / "runtime_state.sqlite3"
