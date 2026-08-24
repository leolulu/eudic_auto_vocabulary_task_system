import unittest

from constants.prompt import USER_ASK_WORD


class WordPromptTest(unittest.TestCase):
    def test_examples_require_bold_target_word_and_inflections(self):
        self.assertIn("每条例句都必须包含这个词本身或它的屈折变体", USER_ASK_WORD)
        self.assertIn("Markdown 粗体语法单独加粗", USER_ASK_WORD)
        self.assertIn("时态变化", USER_ASK_WORD)
        self.assertIn("分词", USER_ASK_WORD)
        self.assertIn("名词复数", USER_ASK_WORD)
        self.assertIn("比较级或最高级", USER_ASK_WORD)
        self.assertIn("不要把目标词所在的整个短语或整条例句一起加粗", USER_ASK_WORD)


if __name__ == "__main__":
    unittest.main()
