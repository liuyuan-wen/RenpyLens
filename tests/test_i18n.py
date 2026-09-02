# -*- coding: utf-8 -*-

import ast
import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyQt5.QtCore import QCoreApplication  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from hwid_utils import NO_EXPIRY  # noqa: E402
from i18n import (  # noqa: E402
    LocalizedString,
    manager,
    localized_node_name,
    resolve_locale,
    set_language,
    tr,
    validate_catalogs,
)
from settings_dialog import SettingsDialog  # noqa: E402
from main import MainWindow  # noqa: E402
from workbench import TranslationWorkbench  # noqa: E402


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else None
        if existing is None:
            cls.app = QApplication([])

    def tearDown(self):
        set_language("zh_CN", self.app)

    def test_locale_resolution_and_unknown_fallback(self):
        self.assertEqual(resolve_locale("auto", "zh_HK"), "zh_TW")
        self.assertEqual(resolve_locale("auto", "ja_JP"), "ja_JP")
        self.assertEqual(resolve_locale("auto", "ko_KR"), "ko_KR")
        self.assertEqual(resolve_locale("auto", "ru_RU"), "ru_RU")
        self.assertEqual(resolve_locale("auto", "fr_FR"), "en_US")
        self.assertEqual(resolve_locale("en_US", "zh_CN"), "en_US")

    def test_catalogs_have_matching_keys_and_placeholders(self):
        self.assertEqual(validate_catalogs(), [])

    def test_dynamic_message_retains_identity_for_live_translation(self):
        set_language("en_US", self.app)
        message = tr("status.selected", name="Example.exe")
        self.assertIsInstance(message, LocalizedString)
        self.assertEqual(message.params, {"name": "Example.exe"})
        self.assertIn("Example.exe", message)
        set_language("ja_JP", self.app)
        rerendered = tr(message.key, **message.params)
        self.assertIn("Example.exe", rerendered)
        self.assertNotEqual(message, rerendered)

    def test_runtime_statuses_and_builtin_nodes_follow_interface_language(self):
        expected = {
            "en_US": ("No expiration", "Global node"),
            "ja_JP": ("有効期限なし", "グローバルノード"),
            "ko_KR": ("만료 없음", "글로벌 노드"),
            "ru_RU": ("Без срока действия", "Глобальный узел"),
        }
        for locale, (expiry, node) in expected.items():
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                self.assertEqual(tr("status.no_expiry"), expiry)
                self.assertEqual(localized_node_name("全球节点"), node)
                self.assertNotIn("翻译失败", tr("translation.failed_detail", detail="x"))

    def test_about_labels_use_locale_appropriate_colons(self):
        keys = (
            "settings.developer",
            "settings.license",
            "settings.project",
            "settings.discord",
            "settings.community",
        )
        for locale in ("zh_CN", "zh_TW", "ja_JP"):
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                for key in keys:
                    self.assertTrue(tr(key).endswith("："), (locale, key, tr(key)))
        for locale in ("en_US", "ko_KR", "ru_RU"):
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                for key in keys:
                    self.assertTrue(tr(key).endswith(": "), (locale, key, tr(key)))

    def test_no_expiry_label_is_rendered_in_the_active_language(self):
        class FakeLabel:
            def setText(self, text):
                self.value = str(text)

            def setStyleSheet(self, _style):
                pass

            def text(self):
                return self.value

        class ExpiryLabelHost:
            config = {"builtin_api_expiry": NO_EXPIRY}
            api_expiry_label = FakeLabel()

        host = ExpiryLabelHost()
        expected = {
            "en_US": "No expiration",
            "ja_JP": "有効期限なし",
            "ko_KR": "만료 없음",
            "ru_RU": "Без срока действия",
        }
        for locale, text in expected.items():
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                MainWindow._update_api_expiry_label(host)
                self.assertIn(text, host.api_expiry_label.text())

    def test_settings_persists_ui_language_without_changing_game_languages(self):
        if self.app is None:
            script = """
import copy
from PyQt5.QtWidgets import QApplication
from config import DEFAULT_CONFIG
from settings_dialog import SettingsDialog
app=QApplication([]); cfg=copy.deepcopy(DEFAULT_CONFIG)
cfg['source_lang']='English'; cfg['target_lang']='日本語'
d=SettingsDialog(cfg); d.ui_language_combo.setCurrentIndex(d.ui_language_combo.findData('ru_RU')); d._on_save()
assert cfg['ui_language']=='ru_RU' and cfg['source_lang']=='English' and cfg['target_lang']=='日本語'
"""
            self._run_gui_subprocess(script)
            return
        set_language("en_US", self.app)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["source_lang"] = "English"
        config["target_lang"] = "日本語"
        dialog = SettingsDialog(config)
        dialog.ui_language_combo.setCurrentIndex(
            dialog.ui_language_combo.findData("ru_RU")
        )
        dialog._on_save()
        self.assertEqual(config["ui_language"], "ru_RU")
        self.assertEqual(config["source_lang"], "English")
        self.assertEqual(config["target_lang"], "日本語")
        dialog.deleteLater()

    def test_settings_dialog_stays_compact_and_translated_controls_fit(self):
        script = """
import copy
from PyQt5.QtWidgets import QApplication
from config import DEFAULT_CONFIG
from i18n import set_language
from settings_dialog import SettingsDialog
app = QApplication.instance() or QApplication([])
for locale in ('zh_CN', 'zh_TW', 'en_US', 'ja_JP', 'ko_KR', 'ru_RU'):
    set_language(locale, app)
    dialog = SettingsDialog(copy.deepcopy(DEFAULT_CONFIG))
    dialog.show(); app.processEvents()
    assert dialog.width() == 840, (locale, dialog.width())
    for name in ('btn_open_config', 'btn_cancel', 'btn_save'):
        button = getattr(dialog, name)
        assert button.width() >= button.sizeHint().width(), (locale, name)
    dialog.tabs.setCurrentIndex(0); app.processEvents()
    assert dialog.keep_names_check.width() >= dialog.keep_names_check.sizeHint().width(), locale
    dialog.tabs.setCurrentIndex(1); app.processEvents()
    for name in ('btn_channel_add', 'btn_channel_copy', 'btn_channel_delete'):
        button = getattr(dialog, name)
        assert button.width() >= button.sizeHint().width(), (locale, name)
    dialog.tabs.setCurrentIndex(2); app.processEvents()
    for checkbox in (dialog.show_character_name_check, dialog.force_topmost_check):
        assert checkbox.width() >= checkbox.sizeHint().width(), (locale, checkbox.text())
    dialog.tabs.setCurrentIndex(3); app.processEvents()
    about_html = dialog.about_info_label.text()
    assert 'https://discord.gg/c4putqY5zs' in about_html, locale
    assert 'qq.jpg' not in about_html, locale
    dialog.close(); dialog.deleteLater()
"""
        if self.app is None:
            self._run_gui_subprocess(script)
            return
        namespace = {}
        exec(script, namespace, namespace)

    def test_workbench_language_switch_preserves_user_translation(self):
        if self.app is None:
            script = """
import copy
from PyQt5.QtWidgets import QApplication
from config import DEFAULT_CONFIG
from i18n import set_language
from workbench import TranslationWorkbench
app=QApplication([]); set_language('en_US', app); w=TranslationWorkbench(copy.deepcopy(DEFAULT_CONFIG))
w.set_entries([{'source':'原始游戏文本','translation':'User translation','entry_type':'dialogue','speaker':'Alice','is_manual':True}])
w.translation_edit.setPlainText('Unsaved user edit'); set_language('ja_JP', app); app.processEvents()
assert w.translation_edit.toPlainText()=='Unsaved user edit' and w.source_view.toPlainText()=='原始游戏文本' and '翻訳' in w.windowTitle()
"""
            self._run_gui_subprocess(script)
            return
        set_language("en_US", self.app)
        workbench = TranslationWorkbench(copy.deepcopy(DEFAULT_CONFIG))
        workbench.set_entries(
            [{
                "source": "原始游戏文本",
                "translation": "User translation",
                "entry_type": "dialogue",
                "speaker": "Alice",
                "is_manual": True,
            }]
        )
        workbench.translation_edit.setPlainText("Unsaved user edit")
        set_language("ja_JP", self.app)
        self.app.processEvents()
        self.assertEqual(workbench.translation_edit.toPlainText(), "Unsaved user edit")
        self.assertEqual(workbench.source_view.toPlainText(), "原始游戏文本")
        self.assertIn("翻訳", workbench.windowTitle())
        workbench.deleteLater()

    def test_workbench_footer_buttons_fit_all_locales(self):
        for locale in ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "ru_RU"):
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                workbench = TranslationWorkbench(copy.deepcopy(DEFAULT_CONFIG))
                workbench.resize(1235, 729)
                workbench.show()
                self.app.processEvents()
                for button in (
                    workbench.btn_open_config,
                    workbench.btn_cancel,
                    workbench.btn_save,
                ):
                    required_width = button.fontMetrics().horizontalAdvance(button.text())
                    self.assertGreaterEqual(button.width(), required_width)
                self.assertLessEqual(
                    workbench.btn_cancel.geometry().right(),
                    workbench.btn_save.geometry().left(),
                )
                button_centers = {
                    workbench.btn_open_config.geometry().center().y(),
                    workbench.btn_cancel.geometry().center().y(),
                    workbench.btn_save.geometry().center().y(),
                }
                self.assertLessEqual(max(button_centers) - min(button_centers), 1)
                workbench.close()
                workbench.deleteLater()

    def _run_gui_subprocess(self, script: str):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ui_display_calls_do_not_contain_direct_cjk_literals(self):
        display_methods = {
            "setText", "setWindowTitle", "setToolTip", "setPlaceholderText",
            "addAction", "addMenu", "addTab", "addRow", "setSuffix",
        }
        constructors = {"QLabel", "QPushButton", "QAction", "QGroupBox", "QCheckBox"}
        failures = []
        for filename in ("main.py", "settings_dialog.py", "overlay.py", "workbench.py"):
            tree = ast.parse((ROOT / "src" / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    call_name = node.func.id
                else:
                    continue
                if call_name not in display_methods | constructors:
                    continue
                for argument in node.args[:2]:
                    fragments = []
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        fragments.append(argument.value)
                    elif isinstance(argument, ast.JoinedStr):
                        fragments.extend(
                            value.value for value in argument.values
                            if isinstance(value, ast.Constant) and isinstance(value.value, str)
                        )
                    if any(any("\u4e00" <= char <= "\u9fff" for char in text) for text in fragments):
                        failures.append(f"{filename}:{node.lineno}:{call_name}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
