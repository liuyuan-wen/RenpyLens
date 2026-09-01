import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as config_module  # noqa: E402
from config import DEFAULT_CONFIG  # noqa: E402
from provider_registry import (  # noqa: E402
    PROVIDER_SPECS,
    resolve_provider,
    update_provider,
)
from main import MainWindow  # noqa: E402
from translator import (  # noqa: E402
    BuiltinTranslator,
    OpenAICompatibleTranslator,
    _normalize_openai_chat_url,
    create_translator,
)


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_has_stable_unique_presets(self):
        provider_ids = [spec.id for spec in PROVIDER_SPECS]
        self.assertEqual(len(provider_ids), len(set(provider_ids)))
        self.assertLess(provider_ids.index("builtin"), provider_ids.index("openrouter"))

        config = copy.deepcopy(DEFAULT_CONFIG)
        expected = {
            "openrouter": ("https://openrouter.ai/api/v1", "openrouter/auto"),
            "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
            "minimax": ("https://api.minimaxi.com/v1", "MiniMax-M3"),
        }
        for provider_id, (url, model) in expected.items():
            provider = resolve_provider(config, provider_id)
            self.assertEqual(provider.url, url)
            self.assertEqual(provider.model, model)
            self.assertTrue(provider.recommended_models)

    def test_custom_channel_resolves_and_updates_by_stable_id(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["custom_openai_channels"] = [{
            "id": "custom-123",
            "name": "Local server",
            "url": "http://localhost:1234/v1",
            "api_key": "secret",
            "model": "local-model",
            "profile": "generic",
        }]
        provider = resolve_provider(config, "custom-123")
        self.assertTrue(provider.is_custom)
        self.assertEqual(provider.name, "Local server")
        update_provider(config, "custom-123", name="Renamed", model="other-model")
        provider = resolve_provider(config, "custom-123")
        self.assertEqual((provider.name, provider.model), ("Renamed", "other-model"))

    def test_legacy_flat_provider_settings_are_preserved_on_load(self):
        saved = {
            "translation_engine": "custom",
            "openai_api_key": "legacy-openai-key",
            "custom_url": "https://legacy.example/v1",
            "custom_model": "legacy-model",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(saved), encoding="utf-8")
            with patch.object(config_module, "CONFIG_DIR", temp_dir), patch.object(
                config_module, "CONFIG_FILE", str(config_path)
            ):
                loaded = config_module.load_config()
        self.assertEqual(loaded["openai_api_key"], "legacy-openai-key")
        self.assertEqual(loaded["custom_url"], "https://legacy.example/v1")
        self.assertEqual(loaded["custom_model"], "legacy-model")
        self.assertEqual(loaded["custom_openai_channels"], [])

    def test_compatible_endpoint_normalization(self):
        cases = {
            "https://openrouter.ai/api/v1": "https://openrouter.ai/api/v1/chat/completions",
            "https://api.groq.com/openai/v1": "https://api.groq.com/openai/v1/chat/completions",
            "https://api.minimaxi.com/v1": "https://api.minimaxi.com/v1/chat/completions",
            "https://example.com/v1/chat/completions": "https://example.com/v1/chat/completions",
        }
        for base_url, expected in cases.items():
            self.assertEqual(_normalize_openai_chat_url(base_url), expected)

        config = copy.deepcopy(DEFAULT_CONFIG)
        for provider_id in ("openrouter", "groq", "minimax"):
            translator = create_translator(provider_id, config)
            self.assertIsInstance(translator, OpenAICompatibleTranslator)
            self.assertTrue(translator.api_url.endswith("/chat/completions"))
            translator.close()

        expected_static_urls = {
            "deepseek": "https://api.deepseek.com/chat/completions",
            "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "volcengine": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        }
        for provider_id, expected_url in expected_static_urls.items():
            translator = create_translator(provider_id, config)
            self.assertEqual(translator.api_url, expected_url)
            translator.close()

    def test_unknown_provider_falls_back_to_builtin(self):
        translator = create_translator("custom-missing", copy.deepcopy(DEFAULT_CONFIG))
        self.assertIsInstance(translator, BuiltinTranslator)

    def test_main_window_can_rebuild_provider_on_focus_change(self):
        class CacheStub:
            def is_empty(self):
                return True

        class ButtonStub:
            def setEnabled(self, _enabled):
                pass

        window = SimpleNamespace(
            config=copy.deepcopy(DEFAULT_CONFIG),
            translator=None,
            _translator_lock=threading.RLock(),
            cache=CacheStub(),
            btn_clear_cache=ButtonStub(),
            _refresh_workbench_entries=lambda: None,
        )
        MainWindow._rebuild_translator(window, clear_cache=False)
        self.assertIsNotNone(window.translator)
        window.translator.close()


class ProviderSettingsTests(unittest.TestCase):
    def _run_gui_script(self, script: str):
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_add_copy_save_and_cancel_are_draft_based(self):
        self._run_gui_script("""
import copy
from PyQt5.QtWidgets import QApplication
from config import DEFAULT_CONFIG
from settings_dialog import SettingsDialog
app = QApplication([])
config = copy.deepcopy(DEFAULT_CONFIG)
config['openrouter_api_key'] = 'do-not-copy'
dialog = SettingsDialog(config)
dialog._refresh_openai_channel_combo('openrouter')
assert dialog.channel_name_input.isReadOnly()
assert not dialog.btn_channel_delete.isEnabled()
dialog._copy_openai_channel()
copied = dialog._channel_config['custom_openai_channels'][-1]
assert copied['profile'] == 'openrouter'
assert copied['model'] == 'openrouter/auto'
assert copied['api_key'] == ''
dialog.channel_name_input.setText('My Router')
dialog.channel_url_input.setText('https://example.com/v1')
dialog.channel_key_input.setText('new-key')
dialog._on_save()
saved = config['custom_openai_channels'][-1]
assert saved['name'] == 'My Router' and saved['api_key'] == 'new-key'
untouched = copy.deepcopy(DEFAULT_CONFIG)
cancelled = SettingsDialog(untouched)
cancelled._add_openai_channel()
cancelled.reject()
assert untouched['custom_openai_channels'] == []
""")

    def test_deleting_active_custom_channel_falls_back_to_builtin(self):
        self._run_gui_script("""
import copy
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication, QMessageBox
from config import DEFAULT_CONFIG
from settings_dialog import SettingsDialog
app = QApplication([])
config = copy.deepcopy(DEFAULT_CONFIG)
config['custom_openai_channels'] = [{
    'id': 'custom-active', 'name': 'Active',
    'url': 'https://example.com/v1', 'api_key': '',
    'model': 'any-model', 'profile': 'generic',
}]
config['translation_engine'] = 'custom-active'
dialog = SettingsDialog(config)
dialog._refresh_openai_channel_combo('custom-active')
with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
    dialog._delete_openai_channel()
dialog._on_save()
assert config['translation_engine'] == 'builtin'
assert config['custom_openai_channels'] == []
""")


if __name__ == "__main__":
    unittest.main()
