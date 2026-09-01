# -*- coding: utf-8 -*-

import re
import textwrap
import unittest
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[1] / "assets" / "_translator_hook.rpy"


def load_speaker_normalizer():
    source = HOOK_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^    def _translator_normalize_speaker\(.*?(?=^    def )",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError("Hook speaker normalizer not found")

    namespace = {}
    exec(textwrap.dedent(match.group(0)), namespace)
    return namespace["_translator_normalize_speaker"]


class HookPython2CompatibilityTests(unittest.TestCase):
    def test_hook_does_not_use_re_fullmatch(self):
        source = HOOK_PATH.read_text(encoding="utf-8")
        self.assertNotIn("_tre.fullmatch(", source)

    def test_identifier_normalization_keeps_whole_string_matching(self):
        normalize = load_speaker_normalizer()

        self.assertEqual(normalize("alice_t"), "alice")
        self.assertEqual(normalize("alice_smith"), "alice smith")
        self.assertEqual(normalize("alice-smith"), "alice-smith")


if __name__ == "__main__":
    unittest.main()
