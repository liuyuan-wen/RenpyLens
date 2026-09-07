import re
import unittest
from pathlib import Path
from types import SimpleNamespace


HOOK_PATH = Path(__file__).resolve().parents[1] / "assets" / "_translator_hook.rpy"


def load_hook_function(name, extra_namespace=None):
    source = HOOK_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^    def {re.escape(name)}\(.*?(?=^    def |^    try:)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"Hook function {name} not found")

    namespace = dict(extra_namespace or {})
    exec("\n".join(line[4:] for line in match.group(0).splitlines()), namespace)
    return namespace[name]


class Text:
    def __init__(self, text, nested=False):
        self.text = [[text]] if nested else [text]

    def visit(self):
        return []


class ForeignTextSequence:
    """Models an engine-owned sequence that is not the script's list type."""

    def __init__(self, *values):
        self.values = values

    def __iter__(self):
        return iter(self.values)


class Button:
    def __init__(self, child, action=None):
        self.child = child
        self.action = action
        self.clicked = None

    def visit(self):
        return [self.child]


class Container:
    def __init__(self, *children):
        self.children = list(children)

    def visit(self):
        return list(reversed(self.children))


class ToggleField:
    pass


class Action:
    pass


class ScreenDisplayable(Container):
    def __init__(self, name, *children, modal=False):
        super().__init__(*children)
        self.screen_name = (name,)
        self.modal = modal
        self.phase = "UPDATE"


class CustomScreenHookTests(unittest.TestCase):
    def setUp(self):
        self.clean = lambda _renpy, value: str(value or "").strip()
        self.extract = load_hook_function(
            "_translator_extract_widget_text",
            {"_translator_clean_text": self.clean},
        )
        self.collect = load_hook_function(
            "_translator_collect_displayable_text",
            {"_translator_extract_widget_text": self.extract},
        )

    def test_collects_body_and_button_labels_separately(self):
        tree = Container(
            Text("Tutorial prompt"),
            Container(
                Button(Text("Yes.", nested=True), action=Action()),
                Button(Text("No.", nested=True), action=Action()),
            ),
        )
        tree.children[0].text = ForeignTextSequence(
            ForeignTextSequence("Tutorial ", "prompt")
        )

        body, choices = self.collect(object(), tree)

        self.assertEqual(body, ["Tutorial prompt"])
        self.assertEqual(choices, ["Yes.", "No."])

    def test_preserves_render_order_and_groups_each_button(self):
        tree = Container(
            Text("First paragraph"),
            Text("Second paragraph"),
            Button(
                Container(Text("EN"), Text("1.07"), Text("PGN")),
                action=Action(),
            ),
            Button(Text("Continue"), action=Action()),
        )

        body, choices = self.collect(object(), tree)

        self.assertEqual(body, ["First paragraph", "Second paragraph"])
        self.assertEqual(choices, ["EN 1.07 PGN", "Continue"])

    def test_toggle_button_label_is_body_not_numbered_choice(self):
        tree = Container(
            Button(Text("Yes"), action=Action()),
            Button(Text("Remember this choice"), action=ToggleField()),
            Button(Text("No"), action=Action()),
        )

        body, choices = self.collect(object(), tree)

        self.assertEqual(body, ["Remember this choice"])
        self.assertEqual(choices, ["Yes", "No"])

    def test_layout_buttons_remain_body_while_nested_actions_are_choices(self):
        tree = Container(
            Button(
                Container(
                    Text("Remember this choice"),
                    Container(
                        Button(Text("Yes"), action=Action()),
                        Button(Text("No"), action=Action()),
                    ),
                )
            ),
            Button(Text('Win ".../game"')),
        )

        body, choices = self.collect(object(), tree)

        self.assertEqual(body, ["Remember this choice", 'Win ".../game"'])
        self.assertEqual(choices, ["Yes", "No"])

    def test_payload_uses_transient_non_modal_screen(self):
        get_payload = load_hook_function(
            "_translator_get_custom_screen_payload",
            {"_translator_collect_displayable_text": self.collect},
        )
        screen = ScreenDisplayable(
            "tut_prompt",
            Text("Tutorial prompt"),
            Button(Text("Yes."), action=Action()),
            Button(Text("No."), action=Action()),
        )
        entry = SimpleNamespace(displayable=screen, tag="tut_prompt", zorder=0)
        scene_lists = SimpleNamespace(
            layers={"screens": [entry]},
            additional_transient=[("screens", "tut_prompt")],
        )
        renpy = SimpleNamespace(
            game=SimpleNamespace(context=lambda: SimpleNamespace(scene_lists=scene_lists))
        )

        payload = get_payload(renpy)

        self.assertEqual(payload["screen"], "tut_prompt")
        self.assertEqual(payload["what"], "Tutorial prompt")
        self.assertEqual(payload["choices"], ["Yes.", "No."])

    def test_ignores_non_modal_persistent_interface_screen(self):
        get_payload = load_hook_function(
            "_translator_get_custom_screen_payload",
            {"_translator_collect_displayable_text": self.collect},
        )
        screen = ScreenDisplayable("quick_menu", Text("Save"))
        entry = SimpleNamespace(displayable=screen, tag="quick_menu", zorder=100)
        scene_lists = SimpleNamespace(
            layers={"screens": [entry]}, additional_transient=[]
        )
        renpy = SimpleNamespace(
            game=SimpleNamespace(context=lambda: SimpleNamespace(scene_lists=scene_lists))
        )

        self.assertIsNone(get_payload(renpy))


if __name__ == "__main__":
    unittest.main()
