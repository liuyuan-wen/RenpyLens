# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config


class ConfigMigrationTests(unittest.TestCase):
    def test_legacy_builtin_nodes_restore_stable_localizable_names(self):
        legacy = {
            "builtin_url": "https://frp-bar.com:50588/",
            "builtin_nodes": [
                {"name": "�й���½�ڵ�", "url": "https://frp-bar.com:50588/"},
                {
                    "name": "����/���ýڵ�",
                    "url": "https://flush-communities-maintained-polyester.trycloudflare.com",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(config, "CONFIG_DIR", directory), patch.object(
                config, "CONFIG_FILE", str(config_path)
            ):
                loaded = config.load_config()

        self.assertEqual(
            loaded["builtin_nodes"],
            config.DEFAULT_CONFIG["builtin_nodes"],
        )
        self.assertEqual(
            loaded["builtin_url"],
            config.DEFAULT_CONFIG["builtin_nodes"][0]["url"],
        )


if __name__ == "__main__":
    unittest.main()
