import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from constants.db import RUNTIME_DB_FILE_PATH


TASK_STATUS_CREATING = "creating"
TASK_STATUS_CREATE_FAILED = "create_failed"
TASK_STATUS_ACTIVE = "active"
TASK_STATUS_CLOSED = "closed"
TASK_STATUS_DELETED = "deleted"

INTERACTION_STATUS_QUEUED = "queued"
INTERACTION_STATUS_PROCESSING = "processing"
INTERACTION_STATUS_CLARIFICATION_PREPARED = "clarification_prepared"
INTERACTION_STATUS_SENDING_CLARIFICATION = "sending_clarification"
INTERACTION_STATUS_AWAITING_CLARIFICATION = "awaiting_clarification"
INTERACTION_STATUS_READY_TO_APPLY = "ready_to_apply"
INTERACTION_STATUS_APPLYING_BODY = "applying_body"
INTERACTION_STATUS_BODY_APPLIED = "body_applied"
INTERACTION_STATUS_DELETING_COMMENTS = "deleting_comments"
INTERACTION_STATUS_DONE = "done"

COMMENT_ROLE_SOURCE = "source"
COMMENT_ROLE_USER_FOLLOWUP = "user_followup"
COMMENT_ROLE_SYSTEM_CLARIFICATION = "system_clarification"

SYSTEM_COMMENT_MARKER_PREFIX = "[[sentence-practice:clarification:"
SCHEMA_VERSION = 2


class _AutoClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SentencePracticeStateStore:
    def __init__(self, db_path=RUNTIME_DB_FILE_PATH) -> None:
        self.db_path = Path(db_path)
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(
            self.db_path,
            timeout=30,
            factory=_AutoClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"运行状态数据库版本 {current_version} 高于程序支持版本 {SCHEMA_VERSION}"
                )
            if current_version == 1:
                connection.execute(
                    "ALTER TABLE sentence_practice_interactions "
                    "RENAME COLUMN body_marker TO record_heading"
                )
                interactions = connection.execute(
                    "SELECT id FROM sentence_practice_interactions ORDER BY id"
                ).fetchall()
                for interaction in interactions:
                    connection.execute(
                        "UPDATE sentence_practice_interactions "
                        "SET record_heading = ? WHERE id = ?",
                        (
                            f"### 互动记录 · 第 {interaction['id']} 次",
                            interaction["id"],
                        ),
                    )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sentence_practice_tasks (
                    practice_date TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    groups_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_comment_count INTEGER,
                    last_etag TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sentence_practice_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_comment_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ai_result_json TEXT,
                    record_heading TEXT NOT NULL UNIQUE,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    lease_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES sentence_practice_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS sentence_practice_comments (
                    comment_id TEXT PRIMARY KEY,
                    interaction_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    title TEXT NOT NULL,
                    reply_comment_id TEXT,
                    remote_created_time TEXT,
                    remote_deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(interaction_id) REFERENCES sentence_practice_interactions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sentence_practice_interactions_task_status
                    ON sentence_practice_interactions(task_id, status);
                CREATE INDEX IF NOT EXISTS idx_sentence_practice_comments_interaction
                    ON sentence_practice_comments(interaction_id);
                CREATE INDEX IF NOT EXISTS idx_sentence_practice_comments_reply
                    ON sentence_practice_comments(reply_comment_id);
                """
            )
            if current_version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _row(row):
        return dict(row) if row is not None else None

    def reserve_daily_task(self, practice_date, task_id, project_id, title, groups):
        now = utc_now_text()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO sentence_practice_tasks (
                    practice_date, task_id, project_id, title, groups_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    practice_date,
                    task_id,
                    project_id,
                    title,
                    _json_dump(groups),
                    TASK_STATUS_CREATING,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sentence_practice_tasks WHERE practice_date = ?",
                (practice_date,),
            ).fetchone()
        return self._row(row)

    def get_task_by_date(self, practice_date):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentence_practice_tasks WHERE practice_date = ?",
                (practice_date,),
            ).fetchone()
        return self._row(row)

    def get_task_by_id(self, task_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentence_practice_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._row(row)

    def list_monitored_tasks(self):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT task.*
                FROM sentence_practice_tasks AS task
                LEFT JOIN sentence_practice_interactions AS interaction
                    ON interaction.task_id = task.task_id
                    AND interaction.status != ?
                WHERE task.status IN (?, ?) OR interaction.id IS NOT NULL
                ORDER BY task.practice_date
                """,
                (INTERACTION_STATUS_DONE, TASK_STATUS_CREATING, TASK_STATUS_ACTIVE),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_task_observation(
        self,
        task_id,
        *,
        status=None,
        comment_count=None,
        etag=None,
    ):
        assignments = ["updated_at = ?"]
        values = [utc_now_text()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if comment_count is not None:
            assignments.append("last_comment_count = ?")
            values.append(comment_count)
        if etag is not None:
            assignments.append("last_etag = ?")
            values.append(etag)
        values.append(task_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE sentence_practice_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )

    def record_remote_comments(self, task_id, project_id, comments):
        now = utc_now_text()
        discovered = []
        with self._connect() as connection:
            for comment in comments:
                comment_id = comment.get("id")
                title = comment.get("title") or ""
                if not comment_id:
                    continue
                existing = connection.execute(
                    "SELECT interaction_id FROM sentence_practice_comments WHERE comment_id = ?",
                    (comment_id,),
                ).fetchone()
                if existing is not None:
                    connection.execute(
                        """
                        UPDATE sentence_practice_comments
                        SET title = ?, reply_comment_id = ?, remote_created_time = ?,
                            remote_deleted = 0, updated_at = ?
                        WHERE comment_id = ?
                        """,
                        (
                            title,
                            comment.get("replyCommentId"),
                            comment.get("createdTime"),
                            now,
                            comment_id,
                        ),
                    )
                    continue
                if SYSTEM_COMMENT_MARKER_PREFIX in title:
                    continue

                reply_comment_id = comment.get("replyCommentId")
                parent = None
                if reply_comment_id:
                    parent = connection.execute(
                        """
                        SELECT comment.interaction_id
                        FROM sentence_practice_comments AS comment
                        JOIN sentence_practice_interactions AS interaction
                            ON interaction.id = comment.interaction_id
                        WHERE comment.comment_id = ?
                          AND comment.role = ?
                          AND interaction.status IN (?, ?, ?, ?, ?)
                        """,
                        (
                            reply_comment_id,
                            COMMENT_ROLE_SYSTEM_CLARIFICATION,
                            INTERACTION_STATUS_AWAITING_CLARIFICATION,
                            INTERACTION_STATUS_QUEUED,
                            INTERACTION_STATUS_PROCESSING,
                            INTERACTION_STATUS_CLARIFICATION_PREPARED,
                            INTERACTION_STATUS_SENDING_CLARIFICATION,
                        ),
                    ).fetchone()

                if parent is not None:
                    interaction_id = parent["interaction_id"]
                    connection.execute(
                        """
                        INSERT INTO sentence_practice_comments (
                            comment_id, interaction_id, role, title,
                            reply_comment_id, remote_created_time,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            comment_id,
                            interaction_id,
                            COMMENT_ROLE_USER_FOLLOWUP,
                            title,
                            reply_comment_id,
                            comment.get("createdTime"),
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE sentence_practice_interactions
                        SET status = ?, ai_result_json = NULL, next_retry_at = NULL,
                            attempt_count = 0, lease_until = NULL,
                            last_error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (INTERACTION_STATUS_QUEUED, now, interaction_id),
                    )
                    discovered.append(interaction_id)
                    continue

                cursor = connection.execute(
                    """
                    INSERT INTO sentence_practice_interactions (
                        source_comment_id, task_id, project_id, status,
                        record_heading, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        task_id,
                        project_id,
                        INTERACTION_STATUS_QUEUED,
                        f"pending:{comment_id}",
                        now,
                        now,
                    ),
                )
                interaction_id = cursor.lastrowid
                connection.execute(
                    "UPDATE sentence_practice_interactions "
                    "SET record_heading = ? WHERE id = ?",
                    (f"### 互动记录 · 第 {interaction_id} 次", interaction_id),
                )
                connection.execute(
                    """
                    INSERT INTO sentence_practice_comments (
                        comment_id, interaction_id, role, title,
                        reply_comment_id, remote_created_time,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        interaction_id,
                        COMMENT_ROLE_SOURCE,
                        title,
                        reply_comment_id,
                        comment.get("createdTime"),
                        now,
                        now,
                    ),
                )
                discovered.append(interaction_id)
        return discovered

    def get_interaction(self, interaction_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sentence_practice_interactions WHERE id = ?",
                (interaction_id,),
            ).fetchone()
        return self._row(row)

    def get_interaction_comments(self, interaction_id):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sentence_practice_comments
                WHERE interaction_id = ?
                ORDER BY created_at, comment_id
                """,
                (interaction_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_next_action(self, lease_seconds=300):
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="milliseconds")
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        recovery = {
            INTERACTION_STATUS_PROCESSING: INTERACTION_STATUS_QUEUED,
            INTERACTION_STATUS_SENDING_CLARIFICATION: INTERACTION_STATUS_CLARIFICATION_PREPARED,
            INTERACTION_STATUS_APPLYING_BODY: INTERACTION_STATUS_READY_TO_APPLY,
        }
        transition = {
            INTERACTION_STATUS_QUEUED: INTERACTION_STATUS_PROCESSING,
            INTERACTION_STATUS_CLARIFICATION_PREPARED: INTERACTION_STATUS_SENDING_CLARIFICATION,
            INTERACTION_STATUS_READY_TO_APPLY: INTERACTION_STATUS_APPLYING_BODY,
            INTERACTION_STATUS_BODY_APPLIED: INTERACTION_STATUS_DELETING_COMMENTS,
            INTERACTION_STATUS_DELETING_COMMENTS: INTERACTION_STATUS_DELETING_COMMENTS,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for volatile_status, recovered_status in recovery.items():
                connection.execute(
                    """
                    UPDATE sentence_practice_interactions
                    SET status = ?, lease_until = NULL, updated_at = ?
                    WHERE status = ? AND lease_until IS NOT NULL AND lease_until <= ?
                    """,
                    (recovered_status, now_text, volatile_status, now_text),
                )
            row = connection.execute(
                """
                SELECT * FROM sentence_practice_interactions
                WHERE status IN (?, ?, ?, ?, ?)
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY created_at, id
                LIMIT 1
                """,
                (
                    INTERACTION_STATUS_QUEUED,
                    INTERACTION_STATUS_CLARIFICATION_PREPARED,
                    INTERACTION_STATUS_READY_TO_APPLY,
                    INTERACTION_STATUS_BODY_APPLIED,
                    INTERACTION_STATUS_DELETING_COMMENTS,
                    now_text,
                    now_text,
                ),
            ).fetchone()
            if row is None:
                return None
            claimed_status = transition[row["status"]]
            connection.execute(
                """
                UPDATE sentence_practice_interactions
                SET status = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (claimed_status, lease_until, now_text, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM sentence_practice_interactions WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return self._row(claimed)

    def prepare_clarification(self, interaction_id, comment_id, question, reply_comment_id):
        now = utc_now_text()
        title = f"{question.strip()}\n\n直接回复这条评论即可。"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO sentence_practice_comments (
                    comment_id, interaction_id, role, title,
                    reply_comment_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comment_id,
                    interaction_id,
                    COMMENT_ROLE_SYSTEM_CLARIFICATION,
                    title,
                    reply_comment_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE sentence_practice_interactions
                SET status = ?, ai_result_json = ?, lease_until = NULL,
                    attempt_count = 0, next_retry_at = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    INTERACTION_STATUS_CLARIFICATION_PREPARED,
                    _json_dump({"action": "clarify", "clarification_question": question.strip()}),
                    now,
                    interaction_id,
                ),
            )
        return title

    def prepare_body_update(self, interaction_id, ai_result):
        now = utc_now_text()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sentence_practice_interactions
                SET status = ?, ai_result_json = ?, lease_until = NULL,
                    attempt_count = 0, next_retry_at = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    INTERACTION_STATUS_READY_TO_APPLY,
                    _json_dump(ai_result),
                    now,
                    interaction_id,
                ),
            )

    def set_interaction_status(self, interaction_id, status):
        now = utc_now_text()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sentence_practice_interactions
                SET status = ?, attempt_count = 0, lease_until = NULL,
                    next_retry_at = NULL, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, now, interaction_id),
            )

    def mark_comment_deleted(self, comment_id):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sentence_practice_comments
                SET remote_deleted = 1, updated_at = ?
                WHERE comment_id = ?
                """,
                (utc_now_text(), comment_id),
            )

    def record_failure(self, interaction_id, resume_status, error):
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM sentence_practice_interactions WHERE id = ?",
                (interaction_id,),
            ).fetchone()
            attempts = (row["attempt_count"] if row else 0) + 1
            delay_seconds = min(15 * (2 ** (attempts - 1)), 15 * 60)
            retry_at = (now + timedelta(seconds=delay_seconds)).isoformat(timespec="milliseconds")
            connection.execute(
                """
                UPDATE sentence_practice_interactions
                SET status = ?, attempt_count = ?, next_retry_at = ?,
                    lease_until = NULL, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    resume_status,
                    attempts,
                    retry_at,
                    f"{type(error).__name__}: {error}"[:2000],
                    now.isoformat(timespec="milliseconds"),
                    interaction_id,
                ),
            )
