import importlib
import inspect
import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class SemanticKernelContractTests(unittest.TestCase):
    def test_package_exposes_stage_one_predictor(self):
        package = importlib.import_module("renpylens_semantics")
        predictor = package.predict
        self.assertEqual(package.KERNEL_API_VERSION, "2")
        self.assertEqual(inspect.signature(predictor).parameters["services"].default, None)

    def test_python_only_modules_import_without_renpy(self):
        for name in (
            "model",
            "symbols",
            "expressions",
            "python_semantics",
            "call_semantics",
            "screen_semantics",
            "graph_machine",
            "renpy_adapter",
            "audit",
        ):
            with self.subTest(module=name):
                self.assertIsNotNone(importlib.import_module("renpylens_semantics." + name))

    def test_runtime_entrypoints_are_thin_wrappers(self):
        root = Path(__file__).resolve().parents[2]
        if not (root / "graph" / "renpy_story_graph_runtime.py").is_file():
            self.skipTest("Standalone story-graph tools are kept locally")
        graph_wrapper = (root / "graph" / "renpy_story_graph_runtime.py").read_text(
            encoding="utf-8"
        )
        audit_wrapper = (root / "graph" / "validate_renpy_story_graph.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("renpylens_semantics", graph_wrapper)
        self.assertIn("renpylens_semantics.audit", audit_wrapper)
        self.assertNotIn("def _python_semantics(", graph_wrapper)

    def test_semantic_services_do_not_depend_on_builder(self):
        package = Path(__file__).resolve().parents[2] / "src" / "renpylens_semantics"
        for name in ("expressions.py", "python_semantics.py", "call_semantics.py",
                     "screen_semantics.py", "renpy_adapter.py", "audit.py"):
            source = (package / name).read_text(encoding="utf-8")
            self.assertNotIn("graph_builder", source, name)

    def test_hook_delegates_prediction_to_kernel(self):
        hook = (Path(__file__).resolve().parents[2] / "assets" / "_translator_hook.rpy").read_text(
            encoding="utf-8"
        )
        self.assertIn("_translator_kernel_predict", hook)
        marker = hook.index("def _translator_predict")
        self.assertIn("return _translator_kernel_predict(", hook[marker:marker + 1200])


if __name__ == "__main__":
    unittest.main()
