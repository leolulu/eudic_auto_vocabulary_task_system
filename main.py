import re
import time
import traceback
from typing import List, Tuple
from urllib.parse import quote

import schedule

import constants.anki as anki_constants
import constants.dida365 as dida365_constants
from agent.agent import Agent
from constants.prompt import SYSTEM_WORD_TEACHER, USER_ASK_EXP, USER_ASK_WORD
from constants.yaml import ANKI_PUSH_ENDPOINT
from dida365_project.models.task import Task
from models.anki import UserQuery
from utils.markdown_to_html_util import markdown_to_html
from utils.word_his_db import add_word_to_his_set, if_exists_in_his_set
from utils.yaml_config_manager import YamlConfigManager


class Bearer:
    def __init__(self) -> None:
        self.agent = Agent()

    def acquire_words(self, days: int = 1):
        words = self.agent.eudic.get_words_in_book()
        words = [w for w in words if w.is_in_last_days_range(days)]
        words = [w for w in words if not if_exists_in_his_set(w.word)]
        return list(words)

    def get_doubao_explanation_by_doubao(self, word: str):
        self.agent.doubao.add_system_message(SYSTEM_WORD_TEACHER)
        answer = self.agent.doubao.chat(USER_ASK_WORD.format(word=word))
        return answer

    def bear_eudic_to_dida365(self):
        """deprecated"""
        words = self.acquire_words(2)
        print(f"添加单词本生词:{words}")
        for word in words:
            content = self.get_doubao_explanation_by_doubao(word.word)
            content += "\n\n[通过web添加anki生词](" + f"{YamlConfigManager().get_config(ANKI_PUSH_ENDPOINT)}?word={quote(word.word)}" + ")"
            try:
                self.agent.dida.add_task(word.word, content)
            except:  # noqa: E722
                traceback.print_exc()
            finally:
                try:
                    self.agent.dida.find_task(word.word, if_reload_data=True)
                    add_word_to_his_set(word.word)
                except:  # noqa: E722
                    pass

    def bear_eudic_to_anki(self):
        words = self.acquire_words(2)
        print(f"添加单词本生词:{words}")
        for word in words:
            content = self.get_doubao_explanation_by_doubao(word.word)
            content = markdown_to_html(content)
            try:
                self.agent.anki_client.add_note(word.word, content)
            except:  # noqa: E722
                traceback.print_exc()
            finally:
                if self.agent.anki_client.search_note_existence(word.word):
                    add_word_to_his_set(word.word)

    def search_questions_from_dida365(self):
        """deprecated"""
        self.agent.dida.dida.get_latest_data()
        task_with_question: List[Tuple[Task, List[str]]] = []
        for task in [t for t in self.agent.dida.dida.active_tasks if t.content and t.project_id == dida365_constants.VOCAB_BOOK_PROJECT_ID]:
            questions = [
                q for q in re.findall(dida365_constants.QUESTION_PREFIX + r"(.*?)" + dida365_constants.QUESTION_SUFFIX, task.content) if q
            ]
            if questions:
                task_with_question.append((task, questions))
        print(f"搜索问题结果:{task_with_question}")
        return task_with_question

    def answer_question_from_dida365(self):
        """deprecated"""
        for task, questions in self.search_questions_from_dida365():
            for question in questions:
                self.agent.substitute_new_doubao_agent()
                self.agent.doubao.add_system_message(
                    "{}{}{}".format(
                        re.sub(dida365_constants.QUESTION_PREFIX + r"(.*?)" + dida365_constants.QUESTION_SUFFIX, "", task.content),
                        "-" * 30,
                        USER_ASK_EXP,
                    )
                )
                answer = self.agent.doubao.chat(question)
                answer = answer.strip()
                task.update_content(
                    task.content.replace(
                        f"{dida365_constants.QUESTION_PREFIX}{question}{dida365_constants.QUESTION_SUFFIX}",
                        f"➡️Q:{question} ↔️ A:{answer}⬅️",
                    )
                )
            self.agent.dida.update_task(task.task_dict)

    def search_and_answer_questions_from_anki(self):
        note_with_question: list[UserQuery] = self.agent.anki_client.search_user_query()
        print(f"搜索Anki问题结果:{note_with_question}")
        for user_query in note_with_question:
            self.agent.substitute_new_doubao_agent()
            self.agent.doubao.add_system_message(
                "{}{}{}".format(
                    re.sub(anki_constants.QUESTION_PREFIX + r"(.*?)" + anki_constants.QUESTION_SUFFIX, "", user_query.note_content),
                    "-" * 30,
                    USER_ASK_EXP,
                )
            )
            answer = self.agent.doubao.chat(user_query.query)
            answer = answer.strip()
            answer = re.sub(r"\*\*\*\*(.*?)\*\*\*\*", r"<b><i>\1</i></b>", answer)
            answer = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", answer)
            answer = re.sub(r"\*(.*?)\*", r"<i>\1</i>", answer)
            user_query.note_content = user_query.note_content.replace(
                f"{anki_constants.QUESTION_PREFIX}{user_query.query}{anki_constants.QUESTION_SUFFIX}",
                f'<span style="background-color: #feecd0;">Q:{user_query.query}</span> <span style="background-color: #dbeafe;">A:{answer}</span>',
            )
            self.agent.anki_client.update_note_fields(user_query.id, {"答案": user_query.note_content})


if __name__ == "__main__":
    b = Bearer()

    schedule.every(1).minutes.do(b.bear_eudic_to_anki)
    schedule.every(3).minutes.do(b.agent.anki_client.request_anki_to_sync)
    schedule.every(10).seconds.do(b.search_and_answer_questions_from_anki)
    schedule.every(1).day.at("00:01").do(b.agent.dida.renew_overdue_task)

    while True:
        schedule.run_pending()
        time.sleep(1)
