import unittest
import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "semantic_cases" / "game"


class FixtureSourceTests(unittest.TestCase):
    def test_fixture_set_covers_all_phase_zero_constructs(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE.glob("*.rpy"))
        for marker in (
            "label ", "default ", "persistent.", "menu ", "if ", "while ",
            "jump ", "call ", "return", "renpy.random", "renpy.input",
            "call screen", "textbutton", "translate test",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_fixture_labels_are_unique(self):
        labels = []
        for path in FIXTURE.glob("*.rpy"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("label "):
                    labels.append(line.split(":", 1)[0])
        self.assertEqual(len(labels), len(set(labels)))

    def test_fixture_golden_has_runtime_contract(self):
        golden = json.loads((FIXTURE.parent / "golden.json").read_text(encoding="utf-8"))
        source = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE.glob("*.rpy"))
        for label in golden["entry_labels"]:
            self.assertIn("label " + label, source)
        self.assertIn("rollback", golden["required_events"])
        for path in golden["artifacts"].values():
            self.assertFalse(Path(path).is_absolute())


if __name__ == "__main__":
    unittest.main()
