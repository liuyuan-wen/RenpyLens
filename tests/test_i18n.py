# -*- coding: utf-8 -*-

import ast
import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyQt5.QtCore import QCoreApplication, QPoint, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QWidgetAction,
)

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
from main import (  # noqa: E402
    MainWindow,
    RPGMAKER_TOOL_FEATURES,
    RPGMakerFeatureCheckBox,
    RPGMakerFeatureRow,
)
from engine_adapters import ENGINE_RENPY, ENGINE_RPGMAKER_MV  # noqa: E402
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

    def test_non_simplified_chinese_support_messages_do_not_show_qq_group(self):
        for locale in ("zh_TW", "en_US", "ja_JP", "ko_KR", "ru_RU"):
            with self.subTest(locale=locale):
                set_language(locale, self.app)
                messages = (
                    tr("status.support", number=""),
                    tr("dialog.update_question", latest="v2", current="v1", number=""),
                    tr("rate.key_expired"),
                )
                for message in messages:
                    self.assertNotIn("QQ", message.upper())
                    self.assertNotIn("1058127921", message)

        set_language("zh_CN", self.app)
        self.assertIn("QQ群", tr("status.support", number="1058127921"))
        self.assertIn("1058127921", tr("status.support", number="1058127921"))

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
        if self.app is None:
            script = """
import copy
from PyQt5.QtWidgets import QApplication
from config import DEFAULT_CONFIG
from i18n import set_language
from workbench import TranslationWorkbench
app = QApplication([])
for locale in ('zh_CN', 'zh_TW', 'en_US', 'ja_JP', 'ko_KR', 'ru_RU'):
    set_language(locale, app)
    workbench = TranslationWorkbench(copy.deepcopy(DEFAULT_CONFIG))
    workbench.resize(1235, 729); workbench.show(); app.processEvents()
    for button in (workbench.btn_open_config, workbench.btn_cancel, workbench.btn_save):
        required_width = button.fontMetrics().horizontalAdvance(button.text())
        assert button.width() >= required_width, (locale, button.text())
    assert workbench.btn_cancel.geometry().right() <= workbench.btn_save.geometry().left(), locale
    workbench.close(); workbench.deleteLater()
"""
            self._run_gui_subprocess(script)
            return
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

    def _make_qol_control_host(self, engine=ENGINE_RPGMAKER_MV, cache_id="game-a"):
        class WidgetStub:
            def __init__(self):
                self.visible = False
                self.enabled = True
                self.checked = False
                self._text = ""
                self.tooltip = ""

            def setVisible(self, value): self.visible = bool(value)
            def isHidden(self): return not self.visible
            def setEnabled(self, value): self.enabled = bool(value)
            def setChecked(self, value): self.checked = bool(value)
            def isChecked(self): return self.checked
            def setText(self, value): self._text = str(value)
            def text(self): return self._text
            def setToolTip(self, value): self.tooltip = str(value)
            def blockSignals(self, _value): pass
            def fontMetrics(self): return self
            def horizontalAdvance(self, value): return len(str(value)) * 10
            def setFixedWidth(self, value): self.width = int(value)

        host = SimpleNamespace()
        host.config = copy.deepcopy(DEFAULT_CONFIG)
        host._current_game = SimpleNamespace(engine=engine, cache_id=cache_id)
        host._selected_game_detected_running = False
        host._game_process = None
        host._hook_config_dirty = False
        host.rpgmaker_qol_container = WidgetStub()
        host.btn_rpgmaker_qol = WidgetStub()
        host.btn_rpgmaker_qol_help = WidgetStub()
        host.btn_rpgmaker_qol_features = WidgetStub()
        host.rpgmaker_qol_feature_checks = {
            feature: WidgetStub() for feature in RPGMAKER_TOOL_FEATURES
        }
        host.rpgmaker_qol_feature_help = {
            feature: WidgetStub() for feature in RPGMAKER_TOOL_FEATURES
        }
        host._is_rpgmaker_selected = MainWindow._is_rpgmaker_selected.__get__(host)
        host._rpgmaker_qol_enabled = MainWindow._rpgmaker_qol_enabled.__get__(host)
        host._rpgmaker_qol_features = MainWindow._rpgmaker_qol_features.__get__(host)
        host._format_rpgmaker_tooltip = MainWindow._format_rpgmaker_tooltip
        host._refresh_rpgmaker_qol_control = MainWindow._refresh_rpgmaker_qol_control.__get__(host)
        return host

    def test_rpgmaker_qol_control_visibility(self):
        host = self._make_qol_control_host()
        host._refresh_rpgmaker_qol_control()
        self.assertFalse(host.rpgmaker_qol_container.isHidden())
        self.assertFalse(host.btn_rpgmaker_qol.isChecked())
        self.assertTrue(host.btn_rpgmaker_qol.enabled)
        self.assertTrue(host.btn_rpgmaker_qol_features.enabled)

        host._current_game = SimpleNamespace(engine=ENGINE_RENPY, cache_id="renpy")
        host._refresh_rpgmaker_qol_control()
        self.assertFalse(host.rpgmaker_qol_container.isHidden())
        self.assertTrue(host.btn_rpgmaker_qol.enabled)
        self.assertTrue(host.btn_rpgmaker_qol_features.enabled)

    def test_rpgmaker_qol_toggle_prompts_without_rpgmaker_game(self):
        host = self._make_qol_control_host(engine=ENGINE_RENPY, cache_id="renpy")
        host._current_game = None
        host.btn_rpgmaker_qol.setChecked(True)
        with patch("main.QMessageBox.information") as information, patch(
            "main.tr", side_effect=lambda key: key
        ):
            MainWindow._on_rpgmaker_qol_toggled(host, True)

        self.assertFalse(host.btn_rpgmaker_qol.isChecked())
        self.assertEqual(host.btn_rpgmaker_qol.text(), "main.rpgmaker_qol_off")
        information.assert_called_once_with(
            host,
            "main.rpgmaker_qol_help_title",
            "main.rpgmaker_qol_no_game",
        )
        self.assertEqual(host.config["rpgmaker_qol_games"], {})

    def test_rpgmaker_feature_checkbox_uses_full_width_as_hit_area(self):
        checkbox = RPGMakerFeatureCheckBox("Feature")
        checkbox.resize(320, 40)
        checkbox.show()
        self.app.processEvents()

        QTest.mouseClick(
            checkbox,
            Qt.LeftButton,
            pos=QPoint(checkbox.width() - 8, checkbox.height() // 2),
        )

        self.assertTrue(checkbox.isChecked())
        checkbox.close()
        checkbox.deleteLater()

    def test_rpgmaker_feature_row_gap_is_clickable_without_closing_menu(self):
        menu = QMenu()
        row = RPGMakerFeatureRow()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(8)
        checkbox = QCheckBox("Feature")
        help_button = QPushButton("?")
        help_button.setFixedSize(28, 28)
        layout.addWidget(checkbox, 1)
        layout.addWidget(help_button)
        row.clicked.connect(checkbox.toggle)
        action = QWidgetAction(menu)
        action.setDefaultWidget(row)
        menu.addAction(action)
        menu.popup(QPoint(100, 100))
        self.app.processEvents()

        gap_x = (checkbox.geometry().right() + help_button.geometry().left()) // 2

        QTest.mouseClick(
            row,
            Qt.LeftButton,
            pos=QPoint(gap_x, row.height() // 2),
        )
        self.app.processEvents()

        self.assertTrue(checkbox.isChecked())
        self.assertTrue(menu.isVisible())
        menu.close()
        menu.deleteLater()

    def test_rpgmaker_tooltips_break_after_semicolons_and_periods(self):
        self.assertEqual(
            MainWindow._format_rpgmaker_tooltip(
                "First; second； third. 第四句。 fifth"
            ),
            "First;\nsecond；\nthird.\n第四句。\nfifth",
        )
        wrapped = MainWindow._format_rpgmaker_tooltip(
            "This tooltip contains enough words to require several compact lines"
        )
        self.assertGreater(len(wrapped.splitlines()), 1)
        self.assertTrue(all(len(line) <= 26 for line in wrapped.splitlines()))

    def test_rpgmaker_main_help_opens_detailed_dialog(self):
        script = f"""
import sys
sys.path.insert(0, {str(ROOT / 'src')!r})
from PyQt5.QtWidgets import QApplication, QDialog, QTextEdit, QWidget
import main
app = QApplication([])
QDialog.exec_ = lambda self: QDialog.Accepted
messages = {{
    'main.rpgmaker_qol_help_title': 'RPGM tools',
    'main.rpgmaker_qol_help_dialog': 'Detailed tool help',
}}
main.tr = lambda key: messages[key]
host = QWidget()
dialog = main.MainWindow._show_rpgmaker_qol_help_dialog(host)
content = dialog.findChild(QTextEdit, 'rpgmakerQolHelpContent')
assert dialog.windowTitle() == 'RPGM tools'
assert dialog.width() >= 560
assert content is not None and content.toPlainText() == 'Detailed tool help'
assert 'font-size: 21px' in dialog.styleSheet()
"""
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rpgmaker_detailed_help_introduces_shortcuts(self):
        set_language("zh_CN", self.app)
        help_text = tr("main.rpgmaker_qol_help_dialog")
        self.assertIn("快捷键（游戏内按键）：", help_text)
        for shortcut in ("G：切换高速移动", "H：切换穿墙", "N：切换随机遇敌", "K：立即战胜"):
            self.assertIn(shortcut, help_text)

    def test_rpgmaker_qol_toggle_is_remembered_per_game(self):
        host = self._make_qol_control_host(cache_id="game-a")
        with patch("main.save_config") as save:
            MainWindow._on_rpgmaker_qol_toggled(host, True)
        self.assertTrue(host.config["rpgmaker_qol_games"]["game-a"])
        self.assertEqual(
            [key for key, value in host.config["rpgmaker_qol_features"]["game-a"].items() if value],
            ["textSpeed"],
        )
        self.assertTrue(host._hook_config_dirty)
        save.assert_called_once_with(host.config)

        host._current_game = SimpleNamespace(engine=ENGINE_RPGMAKER_MV, cache_id="game-b")
        host._refresh_rpgmaker_qol_control()
        self.assertFalse(host.btn_rpgmaker_qol.isChecked())
        host._current_game = SimpleNamespace(engine=ENGINE_RPGMAKER_MV, cache_id="game-a")
        host._refresh_rpgmaker_qol_control()
        self.assertTrue(host.btn_rpgmaker_qol.isChecked())

    def test_rpgmaker_qol_feature_defaults_migration_and_per_game_storage(self):
        host = self._make_qol_control_host(cache_id="new-game")
        self.assertEqual(
            host._rpgmaker_qol_features(),
            {
                "textSpeed": True,
                "messageOpacity": False,
                "autoAdvance": False,
                "moveSpeed": False,
                "through": False,
                "encounters": False,
                "battleVictory": False,
            },
        )

        host.config["rpgmaker_qol_games"]["old-game"] = True
        host._current_game = SimpleNamespace(engine=ENGINE_RPGMAKER_MV, cache_id="old-game")
        migrated = host._rpgmaker_qol_features()
        self.assertTrue(all(migrated[key] for key in (
            "textSpeed", "moveSpeed", "through", "encounters", "battleVictory"
        )))
        self.assertFalse(migrated["messageOpacity"])
        self.assertFalse(migrated["autoAdvance"])

        with patch("main.save_config") as save:
            MainWindow._on_rpgmaker_qol_feature_toggled(host, "through", False)
        stored = host.config["rpgmaker_qol_features"]["old-game"]
        self.assertFalse(stored["through"])
        self.assertTrue(stored["textSpeed"])
        self.assertTrue(host._hook_config_dirty)
        save.assert_called_once_with(host.config)

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
