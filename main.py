import re
from typing import List, Tuple

from agent.agent import Agent
from constants.dida365 import QUESTION_PREFIX, QUESTION_SUFFIX
from constants.prompt import SYSTEM_WORD_TEACHER, USER_ASK_WORD
from dida365_project.models.task import Task


class Bearer:
    def __init__(self) -> None:
        self.agent = Agent()

    def acquire_words(self, days: int = 1):
        words = filter(
            lambda w: w.is_in_last_days_range(days),
            self.agent.eudic.get_words_in_book(),
        )
        return list(words)

    def get_doubao_explanation_by_doubao(self, word: str):
        self.agent.doubao.add_system_message(SYSTEM_WORD_TEACHER)
        answer = self.agent.doubao.chat(USER_ASK_WORD.format(word=word))
        return answer

    def run(self):
        words = self.acquire_words(1)
        print(f"添加单词本生词:{words}")
        for word in words:
            answer = self.get_doubao_explanation_by_doubao(word.word)
            self.agent.dida.add_task(word.word, answer)

    def search_questions(self):
        self.agent.dida.dida.get_latest_data()
        task_with_question: List[Tuple[Task, List[str]]] = []
        for task in [t for t in self.agent.dida.dida.active_tasks if t.content]:
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
                        "以上是问题的背景信息，在回答问题的时候可以作为参考。\n重要格式要求：不要分段分行，所有回答内容都在一行内写完，但可以使用不同行内样式增强阅读体验。",
                    )
                )
                answer = self.agent.doubao.chat(question)
                task.update_content(
                    task.content.replace(
                        f"{QUESTION_PREFIX}{question}{QUESTION_SUFFIX}",
                        f"【Q:{question}    A:{answer}】",
                    )
                )
            self.agent.dida.update_task(task.task_dict)


if __name__ == "__main__":
    b = Bearer()
    # b.run()
    b.answer_question()
