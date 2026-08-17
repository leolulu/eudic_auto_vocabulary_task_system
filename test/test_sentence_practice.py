import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from constants.dida365 import VOCAB_BOOK_PROJECT_ID
from dida365_project.models.task import Task
from sentence_practice import (
    SentencePracticeService,
    append_review_to_body,
    build_practice_task_body,
    group_vocabulary_tasks,
    parse_ai_review,
)
from utils.sentence_practice_db import (
    COMMENT_ROLE_SOURCE,
    COMMENT_ROLE_SYSTEM_CLARIFICATION,
    COMMENT_ROLE_USER_FOLLOWUP,
    INTERACTION_STATUS_AWAITING_CLARIFICATION,
    INTERACTION_STATUS_BODY_APPLIED,
    INTERACTION_STATUS_DONE,
    INTERACTION_STATUS_PROCESSING,
    INTERACTION_STATUS_QUEUED,
    INTERACTION_STATUS_SENDING_CLARIFICATION,
    TASK_STATUS_ACTIVE,
    SentencePracticeStateStore,
)


class FakeDida:
    def __init__(self):
        self.tasks = {}
        self.comments = {}
        self.active_tasks = []
        self.deleted_comment_ids = []
        self.posted_payloads = []
        self.etag_counter = 0

    def _next_etag(self):
        self.etag_counter += 1
        return f"etag-{self.etag_counter}"

    def add_remote_task(self, task_dict):
        value = deepcopy(task_dict)
        value.setdefault("commentCount", 0)
        value.setdefault("etag", self._next_etag())
        value.setdefault("deleted", 0)
        self.tasks[value["id"]] = value
        self._refresh_active_tasks()

    def add_remote_comment(self, task_id, comment_id, title, reply_comment_id=None):
        comment = {
            "id": comment_id,
            "title": title,
            "replyCommentId": reply_comment_id,
            "createdTime": f"2026-08-17T10:00:{len(self.comments.get(task_id, [])):02d}.000+0000",
        }
        self.comments.setdefault(task_id, []).append(comment)
        self._touch_comments(task_id)

    def _touch_comments(self, task_id):
        self.tasks[task_id]["commentCount"] = len(self.comments.get(task_id, []))
        self.tasks[task_id]["etag"] = self._next_etag()
        self._refresh_active_tasks()

    def _refresh_active_tasks(self):
        self.active_tasks = [
            Task(deepcopy(task))
            for task in self.tasks.values()
            if task.get("status") == Task.STATUS_ACTIVE and not task.get("deleted")
        ]

    def get_latest_data(self):
        self._refresh_active_tasks()

    def get_task(self, task_id):
        task = self.tasks.get(task_id)
        return deepcopy(task) if task else None

    def get_task_comments(self, project_id, task_id):
        return deepcopy(self.comments.get(task_id, []))

    def create_task_comment(
        self,
        project_id,
        task_id,
        title,
        *,
        comment_id,
        reply_comment_id=None,
        created_time=None,
    ):
        if not any(item["id"] == comment_id for item in self.comments.get(task_id, [])):
            self.add_remote_comment(task_id, comment_id, title, reply_comment_id)
        return comment_id

    def delete_task_comment(self, project_id, task_id, comment_id):
        self.deleted_comment_ids.append(comment_id)
        self.comments[task_id] = [
            item for item in self.comments.get(task_id, []) if item["id"] != comment_id
        ]
        self._touch_comments(task_id)

    def post_task(self, payload):
        self.posted_payloads.append(deepcopy(payload))
        for task in payload.get("add", []):
            if task["id"] not in self.tasks:
                self.add_remote_task(task)
        for task in payload.get("update", []):
            self.tasks[task["id"]] = deepcopy(task)
            self.tasks[task["id"]]["etag"] = self._next_etag()
            self.tasks[task["id"]]["commentCount"] = len(
                self.comments.get(task["id"], [])
            )
            self.tasks[task["id"]].setdefault("deleted", 0)
            self._refresh_active_tasks()
        return {
            "id2etag": {
                task["id"]: self.tasks[task["id"]]["etag"]
                for task in payload.get("add", []) + payload.get("update", [])
            },
            "id2error": {},
        }


class FakeDidaAgent:
    def __init__(self, dida, candidates=None):
        self.dida = dida
        self.candidates = candidates or []

    def get_today_vocabulary_tasks(self):
        self.dida.get_latest_data()
        return list(self.candidates)


class FakeDoubao:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, user_message, system_message=None):
        self.calls.append((user_message, system_message))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class SentencePracticeGroupingTest(unittest.TestCase):
    def test_grouping_covers_every_word_without_single_tail(self):
        for count in range(1, 25):
            tasks = [SimpleNamespace(id=f"id-{index:02d}", title=f"word{index}") for index in range(count)]
            groups = group_vocabulary_tasks(tasks, "2026-08-17")
            sizes = [len(group["words"]) for group in groups]

            self.assertEqual(sum(sizes), count)
            self.assertEqual(len({word for group in groups for word in group["words"]}), count)
            if count == 1:
                self.assertEqual(sizes, [1])
            else:
                self.assertTrue(all(2 <= size <= 4 for size in sizes), (count, sizes))

    def test_grouping_is_deterministic_even_when_input_order_changes(self):
        tasks = [SimpleNamespace(id=f"id-{index:02d}", title=f"word{index}") for index in range(15)]

        first = group_vocabulary_tasks(tasks, "2026-08-17")
        second = group_vocabulary_tasks(list(reversed(tasks)), "2026-08-17")

        self.assertEqual(first, second)


class SentencePracticeBodyTest(unittest.TestCase):
    def test_append_review_preserves_the_whole_conversation(self):
        groups = [
            {"group_id": 1, "words": ["orbit", "fragile"], "task_ids": ["1", "2"]},
            {"group_id": 2, "words": ["whisper", "anchor"], "task_ids": ["3", "4"]},
        ]
        body = build_practice_task_body("2026-08-17", groups)
        interaction = {
            "record_heading": "### 互动记录 · 第 1 次"
        }
        comments = [
            {"role": COMMENT_ROLE_SOURCE, "title": "I wrote this first."},
            {
                "role": COMMENT_ROLE_SYSTEM_CLARIFICATION,
                "title": "你指的是第一组吗？\n[[sentence-practice:clarification:source-1]]",
            },
            {"role": COMMENT_ROLE_USER_FOLLOWUP, "title": "对，是第一组。"},
        ]
        result = append_review_to_body(
            body,
            interaction,
            comments,
            {
                "action": "apply",
                "updates": [
                    {
                        "group_id": 1,
                        "relevant_history": [
                            {"role": COMMENT_ROLE_SOURCE, "content": "I wrote this first."},
                            {
                                "role": COMMENT_ROLE_SYSTEM_CLARIFICATION,
                                "content": "你指的是第一组吗？",
                            },
                            {"role": COMMENT_ROLE_USER_FOLLOWUP, "content": "对，是第一组。"},
                        ],
                        "feedback_markdown": "**搭配正确。**",
                    }
                ],
            },
        )

        self.assertIn("I wrote this first.", result)
        self.assertIn("你指的是第一组吗？", result)
        self.assertIn("对，是第一组。", result)
        self.assertIn("**搭配正确。**", result)
        first_group = result.split("## 第 2 组", 1)[0]
        self.assertNotIn("_等待用户作答。_", first_group)
        self.assertIn("_等待用户作答。_", result.split("## 第 2 组", 1)[1])
        self.assertEqual(
            append_review_to_body(
                result,
                interaction,
                comments,
                {
                    "action": "apply",
                    "updates": [
                        {
                            "group_id": 1,
                            "relevant_history": [
                                {"role": COMMENT_ROLE_SOURCE, "content": "I wrote this first."}
                            ],
                            "feedback_markdown": "重复",
                        }
                    ],
                },
            ),
            result,
        )

    def test_parse_ai_review_validates_groups_and_accepts_json_fence(self):
        result = parse_ai_review(
            '```json\n{"action":"apply","updates":[{"group_id":2,"relevant_history":[{"role":"source","content":"An answer."}],"feedback_markdown":"Good"}]}\n```',
            {1, 2},
        )

        self.assertEqual(result["updates"][0]["group_id"], 2)
        self.assertEqual(
            parse_ai_review(
                '{"action":"clarify","clarification_question":"你指的是哪一组？"}',
                {1, 2},
            )["action"],
            "clarify",
        )

    def test_multi_group_review_only_writes_each_relevant_excerpt(self):
        groups = [
            {"group_id": 1, "words": ["orbit"], "task_ids": ["1"]},
            {"group_id": 2, "words": ["anchor"], "task_ids": ["2"]},
        ]
        body = build_practice_task_body("2026-08-17", groups)
        comments = [
            {
                "role": COMMENT_ROLE_SOURCE,
                "title": "The satellite left orbit.\n\nThe anchor sank.",
            }
        ]
        ai_result = parse_ai_review(
            '{"action":"apply","updates":['
            '{"group_id":1,"relevant_history":[{"role":"source","content":"The satellite left orbit."}],"feedback_markdown":"第一组点评"},'
            '{"group_id":2,"relevant_history":[{"role":"source","content":"The anchor sank."}],"feedback_markdown":"第二组点评"}'
            ']}',
            {1, 2},
        )

        result = append_review_to_body(
            body,
            {"record_heading": "### 互动记录 · 第 1 次"},
            comments,
            ai_result,
        )
        first_group, second_group = result.split("## 第 2 组", 1)

        self.assertIn("The satellite left orbit.", first_group)
        self.assertNotIn("The anchor sank.", first_group)
        self.assertIn("The anchor sank.", second_group)
        self.assertNotIn("The satellite left orbit.", second_group)

    def test_generated_body_contains_no_machine_metadata(self):
        body = build_practice_task_body(
            "2026-08-17",
            [{"group_id": 1, "words": ["orbit"], "task_ids": ["1"]}],
        )

        self.assertNotIn("<!--", body)
        self.assertNotIn("sentence-practice:", body)

    def test_partial_body_write_only_adds_the_missing_group(self):
        groups = [
            {"group_id": 1, "words": ["orbit"], "task_ids": ["1"]},
            {"group_id": 2, "words": ["anchor"], "task_ids": ["2"]},
        ]
        body = build_practice_task_body("2026-08-17", groups)
        interaction = {"record_heading": "### 互动记录 · 第 1 次"}
        comments = [
            {
                "role": COMMENT_ROLE_SOURCE,
                "title": "The satellite left orbit.\n\nThe anchor sank.",
            }
        ]
        updates = [
            {
                "group_id": 1,
                "relevant_history": [
                    {"role": COMMENT_ROLE_SOURCE, "content": "The satellite left orbit."}
                ],
                "feedback_markdown": "第一组点评",
            },
            {
                "group_id": 2,
                "relevant_history": [
                    {"role": COMMENT_ROLE_SOURCE, "content": "The anchor sank."}
                ],
                "feedback_markdown": "第二组点评",
            },
        ]
        partial = append_review_to_body(
            body,
            interaction,
            comments,
            {"action": "apply", "updates": updates[:1]},
        )

        repaired = append_review_to_body(
            partial,
            interaction,
            comments,
            {"action": "apply", "updates": updates},
        )

        self.assertEqual(repaired.count(interaction["record_heading"]), 2)
        self.assertEqual(repaired.count("The satellite left orbit."), 1)
        self.assertEqual(repaired.count("The anchor sank."), 1)


class SentencePracticeStateStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.store = SentencePracticeStateStore(self.db_path)
        self.store.reserve_daily_task(
            "2026-08-17",
            "task-1",
            "project-1",
            "练习",
            [{"group_id": 1, "words": ["hello"], "task_ids": ["word-1"]}],
        )
        self.store.update_task_observation("task-1", status=TASK_STATUS_ACTIVE)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_persists_and_comment_discovery_is_idempotent(self):
        comment = {
            "id": "source-1",
            "title": "my sentence",
            "replyCommentId": None,
            "createdTime": "2026-08-17T10:00:00.000+0000",
        }

        self.store.record_remote_comments("task-1", "project-1", [comment])
        self.store.record_remote_comments("task-1", "project-1", [comment])
        claimed = self.store.claim_next_action()

        self.assertEqual(claimed["status"], INTERACTION_STATUS_PROCESSING)
        self.assertEqual(claimed["record_heading"], "### 互动记录 · 第 1 次")
        self.assertEqual(
            len(self.store.get_interaction_comments(claimed["id"])),
            1,
        )
        reopened = SentencePracticeStateStore(self.db_path)
        self.assertEqual(reopened.get_interaction(claimed["id"])["source_comment_id"], "source-1")

    def test_schema_v1_machine_marker_is_migrated_to_readable_heading(self):
        self.store.record_remote_comments(
            "task-1",
            "project-1",
            [{"id": "source-1", "title": "answer", "replyCommentId": None}],
        )
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE sentence_practice_interactions "
                "SET record_heading = '<!-- sentence-practice:interaction:source-1 -->'"
            )
            connection.execute(
                "ALTER TABLE sentence_practice_interactions "
                "RENAME COLUMN record_heading TO body_marker"
            )
            connection.execute("PRAGMA user_version = 1")

        migrated = SentencePracticeStateStore(self.db_path)
        interaction = migrated.get_interaction(1)

        self.assertEqual(interaction["record_heading"], "### 互动记录 · 第 1 次")
        self.assertNotIn("body_marker", interaction)

    def test_multiple_replies_to_one_clarification_stay_in_one_interaction(self):
        self.store.record_remote_comments(
            "task-1",
            "project-1",
            [{"id": "source-1", "title": "unclear", "replyCommentId": None}],
        )
        interaction = self.store.claim_next_action()
        self.store.prepare_clarification(
            interaction["id"], "bot-1", "哪一组？", "source-1"
        )
        self.assertNotIn(
            "sentence-practice:",
            self.store.get_interaction_comments(interaction["id"])[-1]["title"],
        )
        sending = self.store.claim_next_action()
        self.assertEqual(sending["status"], INTERACTION_STATUS_SENDING_CLARIFICATION)
        self.store.set_interaction_status(
            interaction["id"], INTERACTION_STATUS_AWAITING_CLARIFICATION
        )

        self.store.record_remote_comments(
            "task-1",
            "project-1",
            [
                {"id": "reply-1", "title": "第一组", "replyCommentId": "bot-1"},
                {"id": "reply-2", "title": "再补充一句", "replyCommentId": "bot-1"},
            ],
        )

        self.assertEqual(
            self.store.get_interaction(interaction["id"])["status"],
            INTERACTION_STATUS_QUEUED,
        )
        self.assertEqual(
            [item["comment_id"] for item in self.store.get_interaction_comments(interaction["id"])],
            ["source-1", "bot-1", "reply-1", "reply-2"],
        )

    def test_late_reply_after_body_write_becomes_a_new_interaction(self):
        self.store.record_remote_comments(
            "task-1",
            "project-1",
            [{"id": "source-1", "title": "unclear", "replyCommentId": None}],
        )
        interaction = self.store.claim_next_action()
        self.store.prepare_clarification(
            interaction["id"], "bot-1", "哪一组？", "source-1"
        )
        self.store.set_interaction_status(
            interaction["id"], INTERACTION_STATUS_BODY_APPLIED
        )

        self.store.record_remote_comments(
            "task-1",
            "project-1",
            [{"id": "late-1", "title": "补充内容", "replyCommentId": "bot-1"}],
        )

        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT id, source_comment_id FROM sentence_practice_interactions ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [(row["id"], row["source_comment_id"]) for row in rows],
            [(interaction["id"], "source-1"), (interaction["id"] + 1, "late-1")],
        )


class SentencePracticeServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SentencePracticeStateStore(Path(self.temp_dir.name) / "state.sqlite3")
        self.dida = FakeDida()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_service(self, responses, candidates=None):
        return SentencePracticeService(
            FakeDidaAgent(self.dida, candidates),
            FakeDoubao(*responses),
            self.store,
            now_provider=lambda: datetime(2026, 8, 17, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    def reserve_active_practice(self):
        groups = [{"group_id": 1, "words": ["orbit", "fragile"], "task_ids": ["w1", "w2"]}]
        self.store.reserve_daily_task(
            "2026-08-17", "practice-1", VOCAB_BOOK_PROJECT_ID, "练习", groups
        )
        self.store.update_task_observation("practice-1", status=TASK_STATUS_ACTIVE)
        self.dida.add_remote_task(
            {
                "id": "practice-1",
                "projectId": VOCAB_BOOK_PROJECT_ID,
                "title": "练习",
                "content": build_practice_task_body("2026-08-17", groups),
                "status": Task.STATUS_ACTIVE,
                "kind": Task.KIND_TEXT,
            }
        )

    def test_generate_daily_task_is_idempotent_and_has_no_repeat_rule(self):
        candidates = [SimpleNamespace(id=f"word-{index}", title=f"word{index}") for index in range(5)]
        service = self.make_service([], candidates)

        first = service.generate_daily_task()
        second = service.generate_daily_task()

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(len(self.dida.tasks), 1)
        created = self.dida.tasks[first["task_id"]]
        self.assertEqual(created["projectId"], VOCAB_BOOK_PROJECT_ID)
        self.assertNotIn("repeatFlag", created)
        self.assertEqual(created["status"], Task.STATUS_ACTIVE)
        self.assertIn("无需标注组号", created["content"])

    def test_direct_answer_is_written_back_then_comment_is_deleted(self):
        self.reserve_active_practice()
        self.dida.add_remote_comment(
            "practice-1",
            "source-1",
            "The fragile satellite escaped its orbit.",
        )
        service = self.make_service(
            ['{"action":"apply","updates":[{"group_id":1,"relevant_history":[{"role":"source","content":"The fragile satellite escaped its orbit."}],"feedback_markdown":"两个词都使用正确。"}]}']
        )

        service.poll_and_process()

        self.assertIn("The fragile satellite escaped its orbit.", self.dida.tasks["practice-1"]["content"])
        self.assertIn("两个词都使用正确。", self.dida.tasks["practice-1"]["content"])
        self.assertEqual(self.dida.comments["practice-1"], [])
        self.assertEqual(self.dida.deleted_comment_ids, ["source-1"])
        with self.store._connect() as connection:
            interaction = connection.execute(
                "SELECT * FROM sentence_practice_interactions"
            ).fetchone()
        self.assertEqual(interaction["status"], INTERACTION_STATUS_DONE)

    def test_ai_failure_is_persisted_and_keeps_the_user_comment(self):
        self.reserve_active_practice()
        self.dida.add_remote_comment(
            "practice-1",
            "source-1",
            "The fragile satellite escaped its orbit.",
        )
        service = self.make_service([RuntimeError("AI unavailable")])

        service.poll_and_process()

        self.assertEqual(
            [item["id"] for item in self.dida.comments["practice-1"]],
            ["source-1"],
        )
        self.assertNotIn("### 互动记录 ·", self.dida.tasks["practice-1"]["content"])
        with self.store._connect() as connection:
            interaction = connection.execute(
                "SELECT * FROM sentence_practice_interactions"
            ).fetchone()
        self.assertEqual(interaction["status"], INTERACTION_STATUS_QUEUED)
        self.assertEqual(interaction["attempt_count"], 1)
        self.assertIn("AI unavailable", interaction["last_error"])

    def test_clarification_chain_is_preserved_in_body_and_deleted_leaf_first(self):
        self.reserve_active_practice()
        self.dida.add_remote_comment("practice-1", "source-1", "I rewrote it.")
        service = self.make_service(
            [
                '{"action":"clarify","clarification_question":"你指的是包含 orbit 和 fragile 的第一组吗？"}',
                '{"action":"apply","updates":[{"group_id":1,"relevant_history":[{"role":"source","content":"I rewrote it."},{"role":"system_clarification","content":"你指的是包含 orbit 和 fragile 的第一组吗？"},{"role":"user_followup","content":"对，就是第一组。"}],"feedback_markdown":"补充后可以确认用法正确。"}]}',
            ]
        )

        service.poll_and_process()
        comments = self.dida.comments["practice-1"]
        bot_comment = next(item for item in comments if item["id"] != "source-1")
        self.assertEqual(bot_comment["replyCommentId"], "source-1")
        self.dida.add_remote_comment(
            "practice-1",
            "followup-1",
            "对，就是第一组。",
            bot_comment["id"],
        )

        service.poll_and_process()

        content = self.dida.tasks["practice-1"]["content"]
        self.assertIn("I rewrote it.", content)
        self.assertIn("你指的是包含 orbit 和 fragile 的第一组吗？", content)
        self.assertIn("对，就是第一组。", content)
        self.assertEqual(
            self.dida.deleted_comment_ids,
            ["followup-1", bot_comment["id"], "source-1"],
        )
        self.assertEqual(self.dida.comments["practice-1"], [])

    def test_already_discovered_input_finishes_after_user_completes_task(self):
        self.reserve_active_practice()
        self.dida.add_remote_comment(
            "practice-1",
            "source-1",
            "The fragile satellite escaped its orbit.",
        )
        service = self.make_service(
            ['{"action":"apply","updates":[{"group_id":1,"relevant_history":[{"role":"source","content":"The fragile satellite escaped its orbit."}],"feedback_markdown":"用法正确。"}]}']
        )

        service.poll_and_process(max_actions=1)
        self.dida.tasks["practice-1"]["status"] = Task.STATUS_COMPLETED
        self.dida._refresh_active_tasks()
        service.poll_and_process()

        self.assertEqual(
            self.dida.tasks["practice-1"]["status"],
            Task.STATUS_COMPLETED,
        )
        self.assertIn("用法正确。", self.dida.tasks["practice-1"]["content"])
        self.assertEqual(self.dida.comments["practice-1"], [])


class DidaCommentApiContractTest(unittest.TestCase):
    def make_client(self):
        from dida365_project.api.dida365 import Dida365

        client = Dida365.__new__(Dida365)
        client._initialize_http()
        client.session = Mock()
        return client

    def test_comment_list_uses_project_scoped_path(self):
        client = self.make_client()
        response = Mock()
        response.json.return_value = []
        client.session.get.return_value = response

        self.assertEqual(client.get_task_comments("project 1", "task/1"), [])

        self.assertTrue(
            client.session.get.call_args.args[0].endswith(
                "/project/project 1/task/task/1/comments"
            )
        )
        response.raise_for_status.assert_called_once()

    def test_create_comment_preserves_client_id_and_reply_id(self):
        client = self.make_client()
        response = Mock()
        client._request_write_with_connect_retry = Mock(return_value=response)

        result = client.create_task_comment(
            "project-1",
            "task-1",
            "clarify",
            comment_id="comment-1",
            reply_comment_id="source-1",
            created_time="2026-08-17T10:00:00.000+0000",
        )

        self.assertEqual(result, "comment-1")
        payload = json.loads(
            client._request_write_with_connect_retry.call_args.kwargs["data"]
        )
        self.assertEqual(payload["id"], "comment-1")
        self.assertEqual(payload["replyCommentId"], "source-1")
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
