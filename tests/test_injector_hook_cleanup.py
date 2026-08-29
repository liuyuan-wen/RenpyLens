# -*- coding: utf-8 -*-

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import injector


class HookArtifactCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.game_dir = self.root / "game"
        self.game_dir.mkdir()
        (self.root / "lib").mkdir()
        self.exe_path = self.root / "ExampleGame.exe"
        self.exe_path.write_bytes(b"")
        self.hook_source = self.root / "source_hook.rpy"
        self.hook_source.write_text(
            "port = {{SOCKET_PORT}}\ncontrol = {{CONTROL_PORT}}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_game_file(self, relative_path: str, content: bytes = b"old") -> Path:
        path = self.game_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_inject_removes_all_residual_hook_variants(self):
        canonical_source = self._write_game_file("_translator_hook.rpy", b"old source")
        canonical_compiled = self._write_game_file("_translator_hook.rpyc")
        copied_compiled = self._write_game_file(
            "_translator_hook-副本20260626230410.rpyc"
        )
        nested_conflict = self._write_game_file(
            "sync/_TRANSLATOR_HOOK-CONFLICT.RPYC"
        )

        ok, message = injector.inject_hook(
            str(self.exe_path), str(self.hook_source), 19876
        )

        self.assertTrue(ok, message)
        self.assertEqual(
            canonical_source.read_text(encoding="utf-8"),
            "port = 19876\ncontrol = 19877\n",
        )
        self.assertFalse(canonical_compiled.exists())
        self.assertFalse(copied_compiled.exists())
        self.assertFalse(nested_conflict.exists())
        self.assertIn("cleaned 3 residual Hook file(s)", message)
        self.assertIn("sync/_TRANSLATOR_HOOK-CONFLICT.RPYC", message)

    def test_inject_preserves_unrelated_files(self):
        unrelated = [
            self._write_game_file("_translator_helper.rpy"),
            self._write_game_file("other_translator_hook.rpyc"),
            self._write_game_file("_translator_hook.txt"),
        ]

        ok, message = injector.inject_hook(
            str(self.exe_path), str(self.hook_source), 19876
        )

        self.assertTrue(ok, message)
        self.assertTrue(all(path.exists() for path in unrelated))

    def test_inject_stops_before_overwrite_when_residual_removal_fails(self):
        canonical_source = self._write_game_file("_translator_hook.rpy", b"old source")
        blocked = self._write_game_file("_translator_hook-conflict.rpyc")
        real_remove = os.remove

        def fail_for_blocked(path):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(blocked)):
                raise PermissionError("file is in use")
            return real_remove(path)

        with mock.patch.object(injector.os, "remove", side_effect=fail_for_blocked):
            ok, message = injector.inject_hook(
                str(self.exe_path), str(self.hook_source), 19876
            )

        self.assertFalse(ok)
        self.assertIn("_translator_hook-conflict.rpyc", message)
        self.assertIn("file is in use", message)
        self.assertEqual(canonical_source.read_bytes(), b"old source")

    def test_remove_hook_removes_canonical_and_renamed_files_recursively(self):
        artifacts = [
            self._write_game_file("_translator_hook.rpy"),
            self._write_game_file("_translator_hook.rpyc"),
            self._write_game_file("archive/_translator_hook (1).rpy"),
        ]
        unrelated = self._write_game_file("archive/story.rpy")

        ok, message = injector.remove_hook(str(self.exe_path))

        self.assertTrue(ok, message)
        self.assertTrue(all(not path.exists() for path in artifacts))
        self.assertTrue(unrelated.exists())
        self.assertIn("archive/_translator_hook (1).rpy", message)


if __name__ == "__main__":
    unittest.main()
