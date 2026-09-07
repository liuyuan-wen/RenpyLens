# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from translator import BaseTranslator


class StubTranslator(BaseTranslator):
    def __init__(self, responses):
        super().__init__({"source_lang": "English", "target_lang": "Chinese"})
        self.responses = iter(responses)
        self.calls = []

    def _call_api(self, system_prompt, user_content):
        self.calls.append(user_content)
        return next(self.responses)


class BatchTranslationTests(unittest.TestCase):
    def test_malformed_batch_falls_back_to_individual_translation(self):
        first = "Reg:\nBefore, in the Suburbs, there was corruption and poverty."
        second = "What happened?"
        translator = StubTranslator(
            [
                "[1] Reg：",
                "[1] Reg：",
                "[1] Reg：",
                "Reg：以前，郊区虽然存在腐败与贫困。",
                "发生了什么？",
            ]
        )

        results = translator.translate_batch([first, second])

        self.assertEqual(
            results,
            ["Reg：以前，郊区虽然存在腐败与贫困。", "发生了什么？"],
        )
        self.assertEqual(len(translator.calls), 5)
        self.assertTrue(translator.calls[0].startswith("[1] Reg:"))
        self.assertEqual(translator.calls[-2:], [first, second])

    def test_valid_numbered_batch_still_uses_one_request(self):
        translator = StubTranslator(["[1] 第一条\n[2] 第二条"])

        results = translator.translate_batch(["First", "Second"])

        self.assertEqual(results, ["第一条", "第二条"])
        self.assertEqual(len(translator.calls), 1)


if __name__ == "__main__":
    unittest.main()
