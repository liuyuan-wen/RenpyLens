# -*- coding: utf-8 -*-
"""设置对话框 - 将所有配置项以 GUI 方式呈现"""

import os
import sys
import copy
import uuid
from urllib.parse import urlparse

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QFormLayout, QGroupBox, QTextEdit, QDoubleSpinBox,
    QScrollArea, QMessageBox, QKeySequenceEdit, QAbstractScrollArea, QApplication,
    QStyle, QStyleOptionComboBox, QStyleOptionSpinBox, QAbstractSpinBox,
    QStyledItemDelegate
)
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QKeySequence, QPainter, QPolygonF, QColor
from ui_theme import ui_px, ui_style, ui_rich_text, fit_window_to_screen
from i18n import LANGUAGE_OPTIONS, manager as i18n_manager, tr
from provider_registry import (
    get_provider_spec,
    iter_provider_options,
    provider_display_name,
    resolve_provider,
    update_provider,
)
from shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_ACTIONS, find_shortcut_conflicts, resolve_shortcuts

# 通用暗色样式
_DARK_STYLE = """
    QDialog {
        background-color: #1a1a2e;
        color: #eee;
        font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI";
        font-size: 18px;
    }
    QTabWidget::pane {
        border: 1px solid #333;
        border-radius: 4px;
        background-color: #1a1a2e;
    }
    QTabBar::tab {
        background-color: #16213e;
        color: #aaa;
        border: 1px solid #333;
        border-bottom: none;
        padding: 10px 22px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-size: 18px;
    }
    QTabBar::tab:selected {
        background-color: #1a1a2e;
        color: #4a9eff;
        border-bottom: 2px solid #4a9eff;
    }
    QTabBar::tab:hover { color: #ccc; }
    QGroupBox {
        border: 1px solid #333;
        border-radius: 6px;
        margin-top: 12px;
        padding-top: 18px;
        font-size: 18px;
        font-weight: bold;
        color: #4a9eff;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
    }
    QLabel { color: #ccc; font-size: 18px; }
    QLineEdit {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 12px; font-size: 18px;
    }
    QLineEdit:focus { border-color: #4a9eff; }
    QKeySequenceEdit {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 12px; font-size: 18px;
    }
    QKeySequenceEdit:focus { border-color: #4a9eff; }
    QComboBox {
        combobox-popup: 0;
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 44px 8px 12px; font-size: 18px; min-width: 180px;
        min-height: 28px;
    }
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #666; }
    QComboBox:focus { border-color: #4a9eff; }
    QComboBox QLineEdit {
        background: transparent; border: none; padding: 0px;
    }
    QComboBox::drop-down {
        subcontrol-origin: padding; subcontrol-position: top right;
        width: 36px; border-left: 1px solid #3d3e50;
        border-top-right-radius: 4px; border-bottom-right-radius: 4px;
    }
    QComboBox::drop-down:hover { background-color: #243454; }
    QComboBox::drop-down:on { background-color: #111a30; }
    QComboBox::down-arrow { image: none; }
    QSpinBox::up-arrow, QSpinBox::down-arrow,
    QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { image: none; }
    QSpinBox::up-button, QDoubleSpinBox::up-button {
        subcontrol-origin: padding; subcontrol-position: top right;
        width: 36px; border-left: 1px solid #3d3e50;
        border-bottom: 1px solid #3d3e50; border-top-right-radius: 4px;
    }
    QSpinBox::down-button, QDoubleSpinBox::down-button {
        subcontrol-origin: padding; subcontrol-position: bottom right;
        width: 36px; border-left: 1px solid #3d3e50;
        border-bottom-right-radius: 4px;
    }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
        background-color: #243454;
    }
    QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
    QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {
        background-color: #111a30;
    }
    QComboBox QAbstractItemView {
        background-color: #1a1a2e; color: #eee;
        border: 1px solid #444; outline: 0px;
        font-size: 18px;
        selection-background-color: #293d60;
        selection-color: #eee;
    }
    QComboBox QAbstractItemView::item {
        min-height: 26px; padding: 3px 12px;
    }
    QScrollArea { background: #1a1a2e; border: none; }
    QWidget#api_content, QWidget#advanced_content { background: #1a1a2e; }
    QScrollBar:vertical {
        background: #1a1a2e; width: 10px; margin: 2px;
    }
    QScrollBar::handle:vertical {
        background: #454b61; border-radius: 3px; min-height: 28px;
    }
    QScrollBar::handle:vertical:hover {
        background: #626d88;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: transparent;
    }
    QScrollBar:horizontal {
        background: #1a1a2e; height: 10px; margin: 2px;
    }
    QScrollBar::handle:horizontal {
        background: #454b61; border-radius: 3px; min-width: 28px;
    }
    QScrollBar::handle:horizontal:hover { background: #626d88; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: transparent;
    }
    QAbstractScrollArea::corner { background: #1a1a2e; }
    QSpinBox, QDoubleSpinBox {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 44px 8px 12px; font-size: 18px; min-width: 120px;
        min-height: 28px;
    }
    QSpinBox:focus, QDoubleSpinBox:focus { border-color: #4a9eff; }
    QCheckBox { color: #ccc; font-size: 18px; spacing: 8px; }
    QCheckBox::indicator {
        width: 20px; height: 20px;
        border: 1px solid #555; border-radius: 4px;
        background-color: #16213e;
    }
    QCheckBox::indicator:checked {
        background-color: #4a9eff;
        border-color: #4a9eff;
    }
    QPushButton {
        background-color: #e94560; color: white;
        border: none; border-radius: 6px;
        padding: 10px 28px; font-size: 18px; font-weight: bold;
    }
    QPushButton#primaryAction { font-weight: bold; }
    QPushButton:hover { background-color: #ff6b81; }
    QPushButton#cancel_btn, QPushButton#open_config_btn {
        background-color: transparent; color: #888;
        border: 1px solid #444;
    }
    QPushButton#cancel_btn:hover, QPushButton#open_config_btn:hover { color: #ccc; border-color: #666; }
    
    QTextEdit {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 4px; font-size: 16px;
    }
"""

# 常用语言列表
_LANGUAGES = [
    "简体中文", "繁體中文", "English", "日本語", "한국어",
    "Français", "Deutsch", "Español", "Português",
    "Русский", "Italiano", "ไทย", "Tiếng Việt",
    "Bahasa Indonesia", "العربية", "Türkçe",
]
    # "Chinese", "English", "Japanese", "Korean",
    # "French", "German", "Spanish", "Portuguese",
    # "Russian", "Italian", "Thai", "Vietnamese",
    # "Indonesian", "Arabic", "Turkish",


class _NoWheelMixin:
    """Let a wheel gesture scroll the containing page instead of editing a value."""

    def wheelEvent(self, event):
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            QApplication.sendEvent(parent.viewport(), event)
        else:
            event.ignore()


class _ControlArrowMixin:
    """Paint crisp, DPI-independent arrows inside Qt's native hit targets."""

    def paintEvent(self, event):
        super().paintEvent(event)
        is_combo = isinstance(self, QComboBox)
        option = QStyleOptionComboBox() if is_combo else QStyleOptionSpinBox()
        self.initStyleOption(option)
        control = QStyle.CC_ComboBox if is_combo else QStyle.CC_SpinBox
        arrows = [(QStyle.SC_ComboBoxArrow, False, self.isEnabled())] if is_combo else [
            (QStyle.SC_SpinBoxUp, True,
             self.isEnabled() and bool(option.stepEnabled & QAbstractSpinBox.StepUpEnabled)),
            (QStyle.SC_SpinBoxDown, False,
             self.isEnabled() and bool(option.stepEnabled & QAbstractSpinBox.StepDownEnabled)),
        ]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for subcontrol, upward, enabled in arrows:
            rect = self.style().subControlRect(control, option, subcontrol, self)
            if rect.isEmpty():
                continue
            active = bool(option.activeSubControls & subcontrol)
            color = "#999aaa" if enabled else "#50546a"
            if enabled and active:
                if option.state & QStyle.State_Sunken or (is_combo and self.view().isVisible()):
                    color = "#4a9eff"
                elif option.state & QStyle.State_MouseOver:
                    color = "#eee"
            painter.setBrush(QColor(color))
            center = rect.center()
            x, y = center.x() + 0.5, center.y() + 0.5
            half_width, half_height = (6, 3.5) if is_combo else (5, 3)
            direction = -1 if upward else 1
            painter.drawPolygon(QPolygonF([
                QPointF(x - half_width, y - direction * half_height),
                QPointF(x + half_width, y - direction * half_height),
                QPointF(x, y + direction * half_height),
            ]))


class NoWheelComboBox(_ControlArrowMixin, _NoWheelMixin, QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setMaxVisibleItems(8)
        self.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def showPopup(self):
        view = self.view()
        # Reserve the scrollbar before Qt captures the opening animation.
        # Otherwise a long list can first paint at full width, then narrow.
        view.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn if self.count() > self.maxVisibleItems()
            else Qt.ScrollBarAsNeeded
        )
        view.ensurePolished()
        view.doItemsLayout()
        super().showPopup()


class NoWheelSpinBox(_ControlArrowMixin, _NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_ControlArrowMixin, _NoWheelMixin, QDoubleSpinBox):
    pass


class SettingsDialog(QDialog):
    """配置对话框 - 3 标签页"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self._channel_config = copy.deepcopy(config)
        if not isinstance(self._channel_config.get("custom_openai_channels"), list):
            self._channel_config["custom_openai_channels"] = []
        self._selected_channel_id = None
        self._changed = False  # 标记是否有修改

        self.setWindowTitle(tr("settings.title"))
        # Keep pages compact; only the tab bar may widen the initial window.
        self.resize(ui_px(840), ui_px(680))
        self.setStyleSheet(ui_style(_DARK_STYLE))

        layout = QVBoxLayout(self)
        layout.setSpacing(ui_px(12))
        layout.setContentsMargins(ui_px(16), ui_px(16), ui_px(16), ui_px(16))

        # 标签页
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_translation_tab(), tr("settings.tab.translation"))
        self.tabs.addTab(self._create_apikey_tab(), tr("settings.tab.api"))
        self.tabs.addTab(self._create_advanced_tab(), tr("settings.tab.advanced"))
        self.tabs.addTab(self._create_about_tab(), tr("settings.tab.about"))
        layout.addWidget(self.tabs)

        # 按钮行
        btn_layout = QHBoxLayout()
        
        self.btn_open_config = QPushButton(tr("common.open_config"))
        self.btn_open_config.setObjectName("open_config_btn")
        self.btn_open_config.setToolTip(tr("common.open_config_tip"))
        self.btn_open_config.clicked.connect(self._on_open_config_dir)
        btn_layout.addWidget(self.btn_open_config)
        
        btn_layout.addStretch()
        self.btn_cancel = QPushButton(tr("common.cancel"))
        self.btn_cancel.setObjectName("cancel_btn")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        self.btn_save = QPushButton(tr("settings.save"))
        self.btn_save.setObjectName("primaryAction")
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        self.ensurePolished()
        margins = layout.contentsMargins()
        tab_width = self.tabs.tabBar().sizeHint().width()
        self.setMinimumSize(max(ui_px(840), tab_width + margins.left() + margins.right()), ui_px(680))
        saved_size = config.get("settings_window_size")
        if (isinstance(saved_size, (list, tuple)) and len(saved_size) == 2
                and all(isinstance(value, int) and value > 0 for value in saved_size)):
            self.resize(*saved_size)
        else:
            self.resize(self.minimumSize())
        fit_window_to_screen(self)

    # ── 标签页 1: 翻译设置 ──────────────────────────

    def _create_translation_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(ui_px(10))
        vbox.setContentsMargins(ui_px(20), ui_px(20), ui_px(20), ui_px(20))

        # 提示（顶部，占满整行宽度）
        hint = QLabel(tr("settings.translation_hint"))
        hint.setStyleSheet(ui_style("color: #666; font-size: 18px;"))
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        # 语言设置表单
        form = QFormLayout()
        form.setSpacing(ui_px(14))
        form.setLabelAlignment(Qt.AlignRight)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.ui_language_combo = NoWheelComboBox()
        for label, locale in LANGUAGE_OPTIONS:
            self.ui_language_combo.addItem(tr(label) if locale == "auto" else label, locale)
        language_index = self.ui_language_combo.findData(self.config.get("ui_language", "auto"))
        self.ui_language_combo.setCurrentIndex(max(0, language_index))
        form.addRow(tr("settings.ui_language"), self.ui_language_combo)

        # 源语言
        self.source_lang_combo = NoWheelComboBox()
        self.source_lang_combo.addItems(_LANGUAGES)
        self.source_lang_combo.setCurrentText(self.config.get("source_lang", "English"))
        self.source_lang_combo.setEditable(True)
        form.addRow(tr("settings.source_language"), self.source_lang_combo)

        # 目标语言
        self.target_lang_combo = NoWheelComboBox()
        self.target_lang_combo.addItems(_LANGUAGES)
        self.target_lang_combo.setCurrentText(self.config.get("target_lang", "Chinese"))
        self.target_lang_combo.setEditable(True)
        form.addRow(tr("settings.target_language"), self.target_lang_combo)

        self.batch_prompt_input = QTextEdit()
        self.batch_prompt_input.setAcceptRichText(False)
        self.batch_prompt_input.setMaximumHeight(100)
        self.batch_prompt_input.setPlainText(self.config.get("batch_prompt", "You are a game localization expert specializing in visual novels. LOCALIZE ALL numbered lines into {target_lang} so they read as if originally written in {target_lang}. Dialogue should sound natural, narration should flow like polished prose. Never translate word-for-word. Output ONLY translations in the same numbered format [1]...[2]... No extra text."))
        self.batch_prompt_input.setToolTip(tr("settings.prompt_tip", target_lang="{target_lang}"))
        form.addRow(tr("settings.batch_prompt"), self.batch_prompt_input)

        self.temp_spin = NoWheelDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(self.config.get("temperature", 0.3))
        form.addRow(tr("settings.temperature"), self.temp_spin)

        self.keep_names_check = QCheckBox(tr("settings.keep_names"))
        self.keep_names_check.setChecked(self.config.get("keep_original_names", True))
        # A one-widget row spans both columns.  Putting translated checkbox
        # text in the field column would add the label-column width to it.
        form.addRow(self.keep_names_check)

        vbox.addLayout(form)

        vbox.addStretch()

        return tab

    # ── 标签页 2: API 设置 ──────────────────────────

    def _create_apikey_tab(self) -> QWidget:
        tab = QWidget()
        main_vbox = QVBoxLayout(tab)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        
        content = QWidget()
        content.setObjectName("api_content")
        layout = QVBoxLayout(content)
        layout.setSpacing(ui_px(8))
        layout.setContentsMargins(ui_px(20), ui_px(12), ui_px(20), ui_px(20))
        
        # Helper to create group box
        def create_api_group(title, key_prefix, default_url, key_placeholder):
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setSpacing(ui_px(10))
            url_input = QLineEdit(self.config.get(f"{key_prefix}_url", default_url))
            url_input.setPlaceholderText(tr("settings.default_url", url=default_url))
            form.addRow("API URL:", url_input)
            
            row, key_input = self._create_key_input(
                self.config.get(f"{key_prefix}_api_key", ""),
                key_placeholder
            )
            form.addRow("API Key:", row)
            return group, url_input, key_input

        layout.addWidget(self._create_openai_channel_group())

        # Gemini
        gemini_group, self.gemini_url_input, self.gemini_key_input = create_api_group(
            "Google Gemini", "gemini", "https://generativelanguage.googleapis.com", tr("settings.get_at", site="aistudio.google.com")
        )
        layout.addWidget(gemini_group)

        # 智谱AI
        zhipu_group, self.zhipu_url_input, self.zhipu_key_input = create_api_group(
            "Zhipu AI (GLM)", "zhipu", "https://open.bigmodel.cn", tr("settings.get_at", site="open.bigmodel.cn")
        )
        layout.addWidget(zhipu_group)
        
        # Anthropic
        anthropic_group, self.anthropic_url_input, self.anthropic_key_input = create_api_group(
            "Anthropic Claude", "anthropic", "https://api.anthropic.com", tr("settings.enter_x_api_key")
        )
        layout.addWidget(anthropic_group)

        # 内置通道
        builtin_group = QGroupBox(tr("settings.builtin_channel"))
        builtin_form = QFormLayout(builtin_group)
        builtin_form.setSpacing(ui_px(10))
        builtin_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        builtin_row, self.builtin_key_input = self._create_key_input(
            self.config.get("builtin_api_key", ""),
            tr("settings.server_auth")
        )
        builtin_form.addRow("API Key:", builtin_row)

        layout.insertWidget(0, builtin_group)

        layout.addStretch()
        scroll.setWidget(content)
        main_vbox.addWidget(scroll)
        return tab

    def _create_openai_channel_group(self) -> QGroupBox:
        group = QGroupBox(tr("settings.group.openai_channels"))
        form = QFormLayout(group)
        form.setSpacing(ui_px(10))
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.channel_combo = NoWheelComboBox()
        form.addRow(tr("settings.channel"), self.channel_combo)

        self.channel_name_input = QLineEdit()
        form.addRow(tr("settings.channel_name"), self.channel_name_input)

        self.channel_url_input = QLineEdit()
        self.channel_url_input.setPlaceholderText("https://example.com/v1")
        form.addRow("API URL:", self.channel_url_input)

        key_row, self.channel_key_input = self._create_key_input(
            "", tr("settings.enter_api_key")
        )
        form.addRow("API Key:", key_row)

        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(ui_px(8))
        self.btn_channel_add = QPushButton(tr("settings.channel_add"))
        self.btn_channel_copy = QPushButton(tr("settings.channel_copy"))
        self.btn_channel_delete = QPushButton(tr("settings.channel_delete"))
        for button in (
            self.btn_channel_add, self.btn_channel_copy, self.btn_channel_delete
        ):
            button.setStyleSheet(ui_style("padding: 7px 14px; font-size: 16px;"))
            action_layout.addWidget(button)
        action_layout.addStretch()
        form.addRow(actions)

        self.channel_combo.currentIndexChanged.connect(
            self._on_openai_channel_changed
        )
        self.btn_channel_add.clicked.connect(self._add_openai_channel)
        self.btn_channel_copy.clicked.connect(self._copy_openai_channel)
        self.btn_channel_delete.clicked.connect(self._delete_openai_channel)
        active = self.config.get("translation_engine", "")
        active_spec = get_provider_spec(active)
        selected = active if active_spec and active_spec.protocol == "openai" else "openai"
        if not active_spec and resolve_provider(self._channel_config, active):
            selected = active
        self._refresh_openai_channel_combo(selected)
        return group

    def _refresh_openai_channel_combo(self, selected_id: str | None = None):
        self.channel_combo.blockSignals(True)
        try:
            self.channel_combo.clear()
            for provider_id, name, spec in iter_provider_options(self._channel_config):
                if spec is not None and spec.protocol != "openai":
                    continue
                display_name = provider_display_name(spec, tr) if spec else name
                self.channel_combo.addItem(display_name, provider_id)
            index = self.channel_combo.findData(selected_id)
            self.channel_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.channel_combo.blockSignals(False)
        self._selected_channel_id = self.channel_combo.currentData()
        self._load_openai_channel_editor()

    def _store_openai_channel_editor(self):
        provider_id = self._selected_channel_id
        if not provider_id:
            return
        values = {
            "url": self.channel_url_input.text().strip(),
            "api_key": self.channel_key_input.text().strip(),
        }
        if get_provider_spec(provider_id) is None:
            values["name"] = self.channel_name_input.text().strip()
        update_provider(self._channel_config, provider_id, **values)

    def _load_openai_channel_editor(self):
        provider_id = self._selected_channel_id
        provider = resolve_provider(self._channel_config, provider_id)
        if provider is None:
            return
        spec = get_provider_spec(provider_id)
        name = provider_display_name(spec, tr) if spec else provider.name
        self.channel_name_input.setText(name)
        self.channel_name_input.setReadOnly(spec is not None)
        self.channel_url_input.setText(provider.url)
        self.channel_key_input.setText(provider.api_key)
        self.btn_channel_delete.setEnabled(spec is None)

    def _on_openai_channel_changed(self, _index: int):
        self._store_openai_channel_editor()
        self._selected_channel_id = self.channel_combo.currentData()
        self._load_openai_channel_editor()

    def _unique_channel_name(self, base_name: str) -> str:
        existing = set()
        for _provider_id, name, spec in iter_provider_options(self._channel_config):
            if spec is not None and spec.protocol != "openai":
                continue
            display_name = provider_display_name(spec, tr) if spec else str(name)
            existing.add(display_name.strip().casefold())
        candidate = base_name.strip() or tr("settings.new_channel_default")
        suffix = 2
        while candidate.casefold() in existing:
            candidate = f"{base_name} {suffix}"
            suffix += 1
        return candidate

    def _add_openai_channel(self):
        self._store_openai_channel_editor()
        provider_id = f"custom-{uuid.uuid4().hex}"
        name = self._unique_channel_name(tr("settings.new_channel_default"))
        self._channel_config.setdefault("custom_openai_channels", []).append({
            "id": provider_id,
            "name": name,
            "url": "http://localhost:8000/v1",
            "api_key": "",
            "model": "",
            "profile": "generic",
        })
        self._refresh_openai_channel_combo(provider_id)

    def _copy_openai_channel(self):
        self._store_openai_channel_editor()
        source = resolve_provider(self._channel_config, self._selected_channel_id)
        if source is None:
            return
        provider_id = f"custom-{uuid.uuid4().hex}"
        name = self._unique_channel_name(
            tr("settings.channel_copy_name", name=source.name)
        )
        self._channel_config.setdefault("custom_openai_channels", []).append({
            "id": provider_id,
            "name": name,
            "url": source.url,
            "api_key": "",
            "model": source.model,
            "profile": source.profile,
        })
        self._refresh_openai_channel_combo(provider_id)

    def _delete_openai_channel(self):
        provider_id = self._selected_channel_id
        if not provider_id or get_provider_spec(provider_id) is not None:
            return
        provider = resolve_provider(self._channel_config, provider_id)
        if provider is None:
            return
        answer = QMessageBox.question(
            self,
            tr("settings.channel_delete_title"),
            tr("settings.channel_delete_confirm", name=provider.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        channels = self._channel_config.get("custom_openai_channels", [])
        self._channel_config["custom_openai_channels"] = [
            item for item in channels
            if not isinstance(item, dict) or item.get("id") != provider_id
        ]
        self._refresh_openai_channel_combo("openai")

    def _validate_openai_channels(self) -> bool:
        self._store_openai_channel_editor()
        names = set()
        for provider_id, name, spec in iter_provider_options(self._channel_config):
            if spec is not None and spec.protocol != "openai":
                continue
            display_name = provider_display_name(spec, tr) if spec else str(name).strip()
            normalized_name = display_name.casefold()
            if not display_name or normalized_name in names:
                QMessageBox.warning(
                    self,
                    tr("settings.channel_invalid_title"),
                    tr("settings.channel_name_invalid"),
                )
                return False
            names.add(normalized_name)
            provider = resolve_provider(self._channel_config, provider_id)
            parsed = urlparse(provider.url if provider else "")
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                QMessageBox.warning(
                    self,
                    tr("settings.channel_invalid_title"),
                    tr("settings.channel_url_invalid", name=display_name),
                )
                return False
        return True

    def _create_key_input(self, value: str, placeholder: str) -> tuple:
        """创建密码模式的 API Key 输入框 + 可见的显示/隐藏按钮
        返回 (container_widget, line_edit)"""
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(ui_px(6))

        edit = QLineEdit()
        edit.setText(value)
        edit.setPlaceholderText(placeholder)
        edit.setEchoMode(QLineEdit.Password)
        h.addWidget(edit)

        btn = QPushButton("🙈")
        btn.setFixedSize(ui_px(36), ui_px(36))
        btn.setToolTip(tr("common.show_hide_key"))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(ui_style("""
            QPushButton {
                background-color: #16213e; color: #888;
                border: 1px solid #444; border-radius: 4px;
                font-size: 16px; padding: 0;
            }
            QPushButton:hover { color: #4a9eff; border-color: #4a9eff; }
        """))
        def _toggle_echo():
            if edit.echoMode() == QLineEdit.Password:
                edit.setEchoMode(QLineEdit.Normal)
                btn.setText("👁")
            else:
                edit.setEchoMode(QLineEdit.Password)
                btn.setText("🙈")
        btn.clicked.connect(_toggle_echo)
        h.addWidget(btn)

        return container, edit

    # ── 标签页 3: 高级设置 ──────────────────────────

    def _create_advanced_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("advanced_content")
        layout = QVBoxLayout(content)
        layout.setSpacing(ui_px(8))
        layout.setContentsMargins(ui_px(20), ui_px(12), ui_px(20), ui_px(20))

        # API 请求
        api_group = QGroupBox(tr("settings.group.api_request"))
        api_form = QFormLayout(api_group)
        api_form.setSpacing(ui_px(10))

        self.api_timeout_spin = NoWheelSpinBox()
        self.api_timeout_spin.setRange(10, 3600)
        self.api_timeout_spin.setSingleStep(30)
        self.api_timeout_spin.setValue(self.config.get("api_timeout_seconds", 120))
        self.api_timeout_spin.setSuffix(tr("settings.seconds"))
        self.api_timeout_spin.setToolTip(
            tr("settings.timeout_tip")
        )
        api_form.addRow(tr("settings.timeout"), self.api_timeout_spin)

        layout.addWidget(api_group)

        # 性能设置
        perf_group = QGroupBox(tr("settings.group.realtime"))
        perf_form = QFormLayout(perf_group)
        perf_form.setSpacing(ui_px(10))

        self.prefetch_spin = NoWheelSpinBox()
        self.prefetch_spin.setRange(3, 20)
        self.prefetch_spin.setValue(self.config.get("prefetch_count", 10))
        self.prefetch_spin.setToolTip(tr("settings.prefetch_tip"))
        perf_form.addRow(tr("settings.prefetch"), self.prefetch_spin)

        self.debounce_spin = NoWheelSpinBox()
        self.debounce_spin.setRange(0, 5000)
        self.debounce_spin.setSingleStep(50)
        self.debounce_spin.setValue(self.config.get("debounce_ms", 200))
        self.debounce_spin.setSuffix(" ms")
        self.debounce_spin.setToolTip(tr("settings.debounce_tip"))
        perf_form.addRow(tr("settings.debounce"), self.debounce_spin)

        layout.addWidget(perf_group)

        # 一键翻译全游戏
        bulk_group = QGroupBox(tr("settings.group.bulk"))
        bulk_form = QFormLayout(bulk_group)
        bulk_form.setSpacing(ui_px(10))

        self.bulk_translate_batch_size_spin = NoWheelSpinBox()
        self.bulk_translate_batch_size_spin.setRange(1, 1000)
        self.bulk_translate_batch_size_spin.setValue(
            self.config.get("bulk_translate_batch_size", 10)
        )
        self.bulk_translate_batch_size_spin.setSuffix(tr("settings.items_per_request"))
        self.bulk_translate_batch_size_spin.setToolTip(
            tr("settings.batch_size_tip")
        )
        bulk_form.addRow(tr("settings.batch_size"), self.bulk_translate_batch_size_spin)

        self.bulk_translate_rpm_spin = NoWheelSpinBox()
        self.bulk_translate_rpm_spin.setRange(1, 600)
        self.bulk_translate_rpm_spin.setValue(self.config.get("bulk_translate_rpm", 60))
        self.bulk_translate_rpm_spin.setSuffix(tr("settings.requests_per_minute"))
        self.bulk_translate_rpm_spin.setToolTip(tr("settings.rpm_tip"))
        bulk_form.addRow(tr("settings.rpm"), self.bulk_translate_rpm_spin)

        layout.addWidget(bulk_group)

        # 运行与显示
        runtime_group = QGroupBox(tr("settings.group.runtime"))
        runtime_form = QFormLayout(runtime_group)
        runtime_form.setSpacing(ui_px(10))
        runtime_form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.socket_port_spin = NoWheelSpinBox()
        self.socket_port_spin.setRange(1024, 65535)
        self.socket_port_spin.setValue(self.config.get("socket_port", 19876))
        self.socket_port_spin.setToolTip(tr("settings.port_tip"))
        runtime_form.addRow(tr("settings.port"), self.socket_port_spin)

        self.show_character_name_check = QCheckBox(tr("settings.show_speaker"))
        self.show_character_name_check.setChecked(self.config.get("show_character_name", True))
        self.show_character_name_check.setToolTip(tr("settings.show_speaker_tip"))
        runtime_form.addRow(self.show_character_name_check)

        self.force_topmost_check = QCheckBox(tr("settings.force_topmost"))
        self.force_topmost_check.setChecked(self.config.get("force_topmost", True))
        self.force_topmost_check.setToolTip(tr("settings.force_topmost_tip"))
        runtime_form.addRow(self.force_topmost_check)

        layout.addWidget(runtime_group)

        shortcut_group = QGroupBox(tr("settings.group.shortcuts"))
        shortcut_form = QFormLayout(shortcut_group)
        shortcut_form.setSpacing(ui_px(10))
        shortcut_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.shortcut_inputs = {}
        current_shortcuts = resolve_shortcuts(self.config)
        for action in SHORTCUT_ACTIONS:
            editor = QKeySequenceEdit()
            if hasattr(editor, "setMaximumSequenceLength"):
                editor.setMaximumSequenceLength(1)
            editor.setKeySequence(QKeySequence(current_shortcuts[action]))
            clear_button = QPushButton(tr("settings.shortcut_clear"))
            clear_button.setObjectName("cancel_btn")
            clear_button.setMinimumWidth(clear_button.sizeHint().width())
            clear_button.clicked.connect(editor.clear)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(ui_px(6))
            row_layout.addWidget(editor, 1)
            row_layout.addWidget(clear_button)
            shortcut_form.addRow(tr(f"settings.shortcut.{action}"), row)
            self.shortcut_inputs[action] = editor

        restore_button = QPushButton(tr("settings.shortcut_restore_defaults"))
        restore_button.setObjectName("cancel_btn")
        restore_button.clicked.connect(self._restore_default_shortcuts)
        shortcut_form.addRow("", restore_button)
        layout.addWidget(shortcut_group)
        layout.addStretch()
        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        return tab

    def _restore_default_shortcuts(self):
        for action, editor in self.shortcut_inputs.items():
            editor.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS[action]))

    # ── 标签页 4: 关于 ──────────────────────────
    def _create_about_tab(self):
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(ui_px(16))
        
        version = self.config.get("version", "v1.6.0")
        
        community_section = (
            f'<b>{tr("settings.community")}</b>1058127921<br>'
            f'<span style="color: #aaa; font-size: 18px;">{tr("settings.community_welcome")}</span><br>'
            f'<span style="color: #aaa; font-size: 18px;">{tr("settings.community_contact")}</span><br>'
            if i18n_manager().locale == "zh_CN"
            else ""
        )
        self.about_info_label = QLabel(
            f'<div style="font-size: 20px; font-weight: bold; margin-bottom: 10px;">RenpyLens {version}</div>'
            '<div style="line-height: 1.5; color: #ddd; font-size: 18px;">'
            f'{tr("settings.about_description")}<br>'
            f'<b>{tr("settings.developer")}</b>wenliuyuan<br>'
            f'<b>{tr("settings.license")}</b>GPLv3<br>'
            f'<b>{tr("settings.project")}</b><a href="https://github.com/liuyuan-wen/RenpyLens" style="color: #4a9eff; text-decoration: none;">https://github.com/liuyuan-wen/RenpyLens</a><br>'
            f'<b>{tr("settings.discord")}</b><a href="https://discord.gg/c4putqY5zs" style="color: #4a9eff; text-decoration: none;">https://discord.gg/c4putqY5zs</a><br>'
            f'{community_section}'
            f'<span style="color: #aaa; font-size: 18px;">{tr("settings.enjoy")}</span>'
            '</div>'
        )
        self.about_info_label.setOpenExternalLinks(True)
        self.about_info_label.setText(ui_rich_text(self.about_info_label.text()))
        self.about_info_label.setWordWrap(True)

        vbox.addWidget(self.about_info_label)

        vbox.addStretch()
        return tab

    # ── 保存 ──────────────────────────────────────

    def _on_save(self):
        """将所有 GUI 值写回 config dict"""
        if not self._validate_openai_channels():
            return
        shortcuts = {
            action: editor.keySequence().toString(QKeySequence.PortableText).split(",", 1)[0]
            for action, editor in self.shortcut_inputs.items()
        }
        conflicts = find_shortcut_conflicts(shortcuts)
        if conflicts:
            first, second = conflicts[0]
            QMessageBox.warning(
                self,
                tr("settings.shortcut_conflict_title"),
                tr(
                    "settings.shortcut_conflict",
                    first=tr(f"settings.shortcut.{first}"),
                    second=tr(f"settings.shortcut.{second}"),
                ),
            )
            return
        self.config["ui_language"] = self.ui_language_combo.currentData() or "auto"
        self.config["source_lang"] = self.source_lang_combo.currentText().strip()
        self.config["target_lang"] = self.target_lang_combo.currentText().strip()

        api_mappings = [
            ("gemini", self.gemini_url_input, self.gemini_key_input),
            ("zhipu", self.zhipu_url_input, self.zhipu_key_input),
            ("anthropic", self.anthropic_url_input, self.anthropic_key_input),
        ]
        for prefix, url_input, key_input in api_mappings:
            self.config[f"{prefix}_url"] = url_input.text().strip()
            self.config[f"{prefix}_api_key"] = key_input.text().strip()

        for provider_id, _name, spec in iter_provider_options(self._channel_config):
            if spec is None or spec.protocol != "openai":
                continue
            provider = resolve_provider(self._channel_config, provider_id)
            if provider:
                update_provider(
                    self.config,
                    provider_id,
                    url=provider.url,
                    api_key=provider.api_key,
                    model=provider.model,
                )
        self.config["custom_openai_channels"] = copy.deepcopy(
            self._channel_config.get("custom_openai_channels", [])
        )
        selected_engine = self.config.get("translation_engine", "builtin")
        if get_provider_spec(selected_engine) is None and not resolve_provider(
            self.config, selected_engine
        ):
            self.config["translation_engine"] = "builtin"

        self.config["builtin_api_key"] = self.builtin_key_input.text().strip()

        self.config["prefetch_count"] = self.prefetch_spin.value()
        self.config["debounce_ms"] = self.debounce_spin.value()
        self.config["api_timeout_seconds"] = self.api_timeout_spin.value()
        self.config["bulk_translate_batch_size"] = self.bulk_translate_batch_size_spin.value()
        self.config["bulk_translate_rpm"] = self.bulk_translate_rpm_spin.value()
        self.config["socket_port"] = self.socket_port_spin.value()
        self.config["force_topmost"] = self.force_topmost_check.isChecked()
        self.config["show_character_name"] = self.show_character_name_check.isChecked()
        self.config["shortcuts"] = shortcuts

        self.config["batch_prompt"] = self.batch_prompt_input.toPlainText().strip()
        self.config["temperature"] = self.temp_spin.value()
        self.config["keep_original_names"] = self.keep_names_check.isChecked()

        self._changed = True
        self.accept()

    @property
    def changed(self) -> bool:
        return self._changed

    def _on_open_config_dir(self):
        """打开配置所在的文件夹"""
        from config import CONFIG_DIR
        import platform
        import os
        import subprocess
        try:
            if platform.system() == "Windows":
                os.startfile(CONFIG_DIR)
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", CONFIG_DIR])
            else:  # Linux
                subprocess.Popen(["xdg-open", CONFIG_DIR])
        except Exception as e:
            print(f"无法打开配置目录: {e}")

