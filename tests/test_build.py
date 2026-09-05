# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build


class BuildOutputNameTests(unittest.TestCase):
    def test_reuses_standard_name_when_existing_file_is_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "RenpyLens_v1.2.3.exe").write_bytes(b"existing")

            self.assertEqual(
                build.select_output_name("v1.2.3", directory),
                "RenpyLens_v1.2.3",
            )

    def test_uses_next_numbered_name_when_standard_file_is_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "RenpyLens_v1.2.3.exe").write_bytes(b"locked")
            Path(directory, "RenpyLens_v1.2.3_1.exe").write_bytes(b"existing")

            with patch("builtins.open", side_effect=PermissionError):
                output_name = build.select_output_name("v1.2.3", directory)

            self.assertEqual(output_name, "RenpyLens_v1.2.3_2")


if __name__ == "__main__":
    unittest.main()
