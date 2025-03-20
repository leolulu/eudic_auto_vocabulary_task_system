import re
import time
import traceback
from typing import List, Tuple
from urllib.parse import quote

import schedule

from agent.agent import Agent
from constants.dida365 import QUESTION_PREFIX, QUESTION_SUFFIX, VOCAB_BOOK_PROJECT_ID
from constants.prompt import SYSTEM_WORD_TEACHER, USER_ASK_EXP, USER_ASK_WORD
from constants.yaml import ANKI_PUSH_ENDPOINT
from dida365_project.models.task import Task
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
        words = self.acquire_words(2)
        print(f"添加单词本生词:{words}")
        for word in words:
            content = self.get_doubao_explanation_by_doubao(word.word)
            content += "\n\n[通过web添加anki生词](" + f"{YamlConfigManager().get_config(ANKI_PUSH_ENDPOINT)}?word={quote(word.word)}" + ")"
            try:
                self.agent.dida.add_task(word.word, content)
            except:
                traceback.print_exc()
            finally:
                try:
                    self.agent.dida.find_task(word.word, if_reload_data=True)
                    add_word_to_his_set(word.word)
                except:
                    pass

    def search_questions(self):
        self.agent.dida.dida.get_latest_data()
        task_with_question: List[Tuple[Task, List[str]]] = []
        for task in [t for t in self.agent.dida.dida.active_tasks if t.content and t.project_id == VOCAB_BOOK_PROJECT_ID]:
            questions = [q for q in re.findall(QUESTION_PREFIX + r"(.*?)" + QUESTION_SUFFIX, task.content) if q]
            if questions:
                task_with_question.append((task, questions))
        print(f"搜索问题结果:{task_with_question}")
        return task_with_question

    def answer_question(self):
        for task, questions in self.search_questions():
            for question in questions:
                self.agent.substitute_new_doubao_agent()
                self.agent.doubao.add_system_message(
                    "{}{}{}".format(
                        re.sub(QUESTION_PREFIX + r"(.*?)" + QUESTION_SUFFIX, "", task.content),
                        "-" * 30,
                        USER_ASK_EXP,
                    )
                )
                answer = self.agent.doubao.chat(question)
                task.update_content(
                    task.content.replace(
                        f"{QUESTION_PREFIX}{question}{QUESTION_SUFFIX}",
                        f"➡️Q:{question} ↔️ A:{answer}⬅️",
                    )
                )
            self.agent.dida.update_task(task.task_dict)


if __name__ == "__main__":
    b = Bearer()

    schedule.every(1).minutes.do(b.bear_eudic_to_dida365)
    schedule.every(10).seconds.do(b.answer_question)

    while True:
        schedule.run_pending()
        time.sleep(1)
