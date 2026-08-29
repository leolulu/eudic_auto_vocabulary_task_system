import unittest

from constants.prompt import USER_ASK_WORD


class WordPromptTest(unittest.TestCase):
    def test_part_of_speech_uses_bold_english_abbreviations(self):
        self.assertIn("词性名称统一使用英语学习词典中常见的英文简写", USER_ASK_WORD)
        self.assertIn("**vt.**", USER_ASK_WORD)
        self.assertIn("**vi.**", USER_ASK_WORD)
        self.assertIn("**adj.**", USER_ASK_WORD)
        self.assertIn("Markdown 粗体语法单独加粗每个词性简写", USER_ASK_WORD)
        self.assertIn("“词性：”等引导文字保持普通文本，不要加粗", USER_ASK_WORD)
        self.assertIn("呈现格式例如：词性：**adj.**", USER_ASK_WORD)
        self.assertIn("输出中不要使用中文词性名称", USER_ASK_WORD)

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
