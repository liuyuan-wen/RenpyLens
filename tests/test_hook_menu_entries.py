# -*- coding: utf-8 -*-

import re
import textwrap
import unittest
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[1] / "assets" / "_translator_hook.rpy"


def load_menu_entry_extractor():
    source = HOOK_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^    def _translator_extract_menu_entries\(.*?(?=^    def )",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError("Hook menu-entry extractor not found")

    namespace = {
        "_translator_menu_item_is_visible": lambda renpy, item: item[1] is not False,
        "_translator_clean_text": lambda renpy, text: str(text or "").strip(),
    }
    exec(textwrap.dedent(match.group(0)), namespace)
    return namespace["_translator_extract_menu_entries"]


class Menu:
    def __init__(self, items):
        self.items = items


class HookMenuEntryTests(unittest.TestCase):
    def test_caption_is_separated_from_selectable_choices(self):
        extract = load_menu_entry_extractor()
        menu = Menu(
            [
                ("But he made me change my mind.", None, None),
                ("Don't you think he took advantage?", None, [object()]),
                ("I'm glad you met someone nice.", None, [object()]),
            ]
        )

        caption, choices = extract(object(), menu)

        self.assertEqual(caption, "But he made me change my mind.")
        self.assertEqual(
            choices,
            [
                "Don't you think he took advantage?",
                "I'm glad you met someone nice.",
            ],
        )

    def test_hidden_items_and_duplicate_choices_are_excluded(self):
        extract = load_menu_entry_extractor()
        menu = Menu(
            [
                ("Caption", None, None),
                ("Hidden", False, [object()]),
                ("Visible", True, [object()]),
                ("Visible", True, [object()]),
            ]
        )

        caption, choices = extract(object(), menu)

        self.assertEqual(caption, "Caption")
        self.assertEqual(choices, ["Visible"])


if __name__ == "__main__":
    unittest.main()
