# -*- coding: utf-8 -*-
"""Translation workbench window for recent entries and manual editing."""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QTimer, Qt, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class EntrySummaryDelegate(QStyledItemDelegate):
    """Paint the recent-entry prefix tags in bold."""

    _background = QColor("#111525")
    _background_selected = QColor("#22314f")
    _border = QColor("#232a3f")
    _text = QColor("#eeeeee")
    _text_selected = QColor("#ffffff")

    def paint(self, painter, option, index):
        text = str(index.data(Qt.DisplayRole) or "")
        first_line, second_line = (text.split("\n", 1) + [""])[:2]
        prefix, sep, source = first_line.partition(" ")
        if not sep:
            source = ""

        selected = bool(option.state & QStyle.State_Selected)

        painter.save()
        painter.fillRect(
            option.rect,
            self._background_selected if selected else self._background,
        )
        painter.setPen(QPen(self._border))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        normal_font = option.font
        bold_font = QFont(normal_font)
        bold_font.setBold(True)
        normal_metrics = QFontMetrics(normal_font)
        bold_metrics = QFontMetrics(bold_font)
        text_rect = option.rect.adjusted(8, 10, -8, -10)
        painter.setClipRect(text_rect)
        painter.setPen(self._text_selected if selected else self._text)

        x = text_rect.left()
        y = text_rect.top() + normal_metrics.ascent()
        space_width = normal_metrics.horizontalAdvance(" ")

        painter.setFont(bold_font)
        painter.drawText(x, y, prefix)

        painter.setFont(normal_font)
        prefix_width = bold_metrics.horizontalAdvance(prefix)
        painter.drawText(x + prefix_width + space_width, y, source)

        if second_line:
            painter.drawText(x, y + normal_metrics.lineSpacing(), second_line)

        painter.restore()

    def sizeHint(self, option, index):
        text = str(index.data(Qt.DisplayRole) or "")
        first_line, second_line = (text.split("\n", 1) + [""])[:2]
        prefix, sep, source = first_line.partition(" ")
        if not sep:
            source = ""

        normal_font = option.font
        bold_font = QFont(normal_font)
        bold_font.setBold(True)
        normal_metrics = QFontMetrics(normal_font)
        bold_metrics = QFontMetrics(bold_font)

        width = bold_metrics.horizontalAdvance(prefix)
        width += normal_metrics.horizontalAdvance(" ")
        width += normal_metrics.horizontalAdvance(source)
        width = max(width, normal_metrics.horizontalAdvance(second_line))
        width += 16

        height = normal_metrics.lineSpacing() * 2 + 20
        return QSize(width, height)


class TranslationWorkbench(QWidget):
    config_updated = pyqtSignal(dict)
    visibility_changed = pyqtSignal(bool)
    save_requested = pyqtSignal(dict)
    autosave_requested = pyqtSignal(dict)
    bulk_translate_requested = pyqtSignal()
    bulk_cancel_requested = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._base_window_title = "RenpyLens 译文工作台"
        self._entries_by_source: dict[str, dict[str, Any]] = {}
        self._current_source = ""
        self._is_pinned = self.config.get("workbench_pinned", False)
        self._has_unsaved_changes = False
        self._is_programmatic_text_change = False
        self._splitter_default_applied = False
        self._bulk_result_timer = QTimer(self)
        self._bulk_result_timer.setSingleShot(True)
        self._bulk_result_timer.timeout.connect(self.set_bulk_idle)

        self.setWindowTitle(self._base_window_title)
        self.resize(
            config.get("workbench_width", 960),
            config.get("workbench_height", 640),
        )
        self.move(
            config.get("workbench_x", 120),
            config.get("workbench_y", 120),
        )
        if self._is_pinned:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.setStyleSheet(
            """
            QWidget {
                background-color: #141827;
                color: #eee;
                font-family: "Microsoft YaHei", "Segoe UI";
                font-size: 20px;
            }
            QListWidget {
                background-color: #111525;
                border: 1px solid #333a52;
                border-radius: 6px;
                padding: 6px;
                font-size: 18px;
            }
            QListWidget::item {
                border-bottom: 1px solid #232a3f;
                padding: 10px 8px;
            }
            QListWidget::item:selected {
                background-color: #22314f;
                color: white;
            }
            QListWidget QScrollBar:vertical {
                background: #1a1a2e;
                width: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QListWidget QScrollBar:horizontal {
                background: #1a1a2e;
                height: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QListWidget QScrollBar::handle:vertical {
                background: #444;
                border-radius: 4px;
                min-height: 30px;
            }
            QListWidget QScrollBar::handle:horizontal {
                background: #444;
                border-radius: 4px;
                min-width: 30px;
            }
            QListWidget QScrollBar::handle:vertical:hover {
                background: #666;
            }
            QListWidget QScrollBar::handle:horizontal:hover {
                background: #666;
            }
            QListWidget QScrollBar::add-line:vertical,
            QListWidget QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QListWidget QScrollBar::add-line:horizontal,
            QListWidget QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QTextEdit {
                background-color: #111525;
                border: 1px solid #333a52;
                border-radius: 6px;
                padding: 8px;
                font-size: 20px;
            }
            QLabel#meta_title {
                color: #7aa2ff;
                font-size: 20px;
                font-weight: bold;
            }
            QLabel#workbench_game_title {
                color: #d7def4;
                font-size: 24px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #e94560;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff6b81;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
            QPushButton#editor_save_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b7cff, stop:1 #1f6feb);
                color: white;
                border: 1px solid #4d8cff;
                border-radius: 10px;
                padding: 11px 22px;
                font-size: 20px;
                font-weight: bold;
                min-width: 176px;
            }
            QPushButton#editor_save_btn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3988ff, stop:1 #2a78f0);
            }
            QPushButton#editor_save_btn:pressed {
                background: #185bd6;
            }
            QPushButton#editor_save_btn:disabled {
                background: #233047;
                color: #7a8498;
                border-color: #364055;
            }
            QPushButton#editor_cancel_btn {
                background-color: transparent;
                color: #cdd6eb;
                border: 1px solid #495673;
                border-radius: 10px;
                padding: 11px 20px;
                font-size: 20px;
                font-weight: bold;
                min-width: 140px;
            }
            QPushButton#editor_cancel_btn:hover {
                background-color: rgba(255, 255, 255, 0.04);
                color: #ffffff;
                border-color: #7482a2;
            }
            QPushButton#editor_cancel_btn:pressed {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QPushButton#editor_cancel_btn:disabled {
                background-color: transparent;
                color: #666f83;
                border-color: #3b4252;
            }
            QProgressBar {
                background-color: #0f1320;
                border: 1px solid #43506d;
                border-radius: 7px;
                padding: 2px;
                color: #f1f5ff;
                text-align: center;
                min-height: 38px;
                font-size: 17px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #2f8dff;
                border-radius: 5px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(12)
        toolbar_btn_style = """
            QPushButton {
                background-color: transparent;
                color: #888;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 8px 18px;
                font-size: 20px;
                font-weight: normal;
            }
            QPushButton:hover { color: #ccc; border-color: #666; }
            QPushButton:checked { color: #4a9eff; border-color: #4a9eff; }
        """

        self.game_title_label = QLabel("当前游戏：未选择")
        self.game_title_label.setObjectName("workbench_game_title")
        self.game_title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        toolbar.addWidget(self.game_title_label)
        toolbar.addStretch()

        bulk_container = QWidget()
        bulk_layout = QHBoxLayout(bulk_container)
        bulk_layout.setContentsMargins(0, 0, 0, 0)
        bulk_layout.setSpacing(8)

        self.bulk_stack = QStackedWidget()
        self.bulk_stack.setMinimumWidth(360)
        bulk_layout.addWidget(self.bulk_stack, 1)

        self.bulk_idle_page = QWidget()
        bulk_idle_layout = QHBoxLayout(self.bulk_idle_page)
        bulk_idle_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_bulk_translate = QPushButton("🚀 一键翻译全游戏")
        self.btn_bulk_translate.setCursor(Qt.PointingHandCursor)
        self.btn_bulk_translate.setToolTip("预取全游戏脚本文本，并分批翻译到本地缓存")
        self.btn_bulk_translate.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b7cff, stop:1 #1f6feb);
                color: white;
                border: 1px solid #4d8cff;
                border-radius: 11px;
                padding: 10px 22px;
                font-size: 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3988ff, stop:1 #2a78f0);
            }
            QPushButton:pressed { background: #185bd6; }
            QPushButton:disabled {
                background-color: #233047;
                color: #7a8498;
                border-color: #364055;
            }
            """
        )
        self.btn_bulk_translate.clicked.connect(self.bulk_translate_requested.emit)
        bulk_idle_layout.addWidget(self.btn_bulk_translate)
        self.bulk_stack.addWidget(self.bulk_idle_page)

        self.bulk_progress_page = QWidget()
        bulk_progress_layout = QHBoxLayout(self.bulk_progress_page)
        bulk_progress_layout.setContentsMargins(0, 0, 0, 0)
        self.bulk_progress = QProgressBar()
        self.bulk_progress.setRange(0, 100)
        self.bulk_progress.setValue(0)
        self.bulk_progress.setFormat("0%")
        bulk_progress_layout.addWidget(self.bulk_progress)
        self.bulk_stack.addWidget(self.bulk_progress_page)

        self.bulk_result_page = QWidget()
        bulk_result_layout = QHBoxLayout(self.bulk_result_page)
        bulk_result_layout.setContentsMargins(0, 0, 0, 0)
        self.bulk_result_label = QLabel("")
        self.bulk_result_label.setAlignment(Qt.AlignCenter)
        self.bulk_result_label.setMinimumHeight(38)
        self.bulk_result_label.setStyleSheet(
            """
            QLabel {
                border: 1px solid #43506d;
                border-radius: 7px;
                background-color: #0f1320;
                padding: 0 14px;
                font-size: 17px;
                font-weight: bold;
            }
            """
        )
        bulk_result_layout.addWidget(self.bulk_result_label)
        self.bulk_stack.addWidget(self.bulk_result_page)

        self.btn_bulk_cancel = QPushButton("取消")
        self.btn_bulk_cancel.setVisible(False)
        self.btn_bulk_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_bulk_cancel.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                color: #f6c178;
                border: 1px solid #8f6b39;
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover { color: #ffd9a0; border-color: #d39b4c; }
            QPushButton:disabled { color: #7d6a4f; border-color: #5a4d3d; }
            """
        )
        self.btn_bulk_cancel.clicked.connect(self.bulk_cancel_requested.emit)
        bulk_layout.addWidget(self.btn_bulk_cancel)
        toolbar.addWidget(bulk_container)

        self.btn_pin = QPushButton("📌 置顶")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(self._is_pinned)
        self.btn_pin.setStyleSheet(toolbar_btn_style)
        self.btn_pin.clicked.connect(self._toggle_pin)
        toolbar.addWidget(self.btn_pin)
        root.addLayout(toolbar)

        title = QLabel("最近遇到的文本")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #7aa2ff;")
        root.addWidget(title)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        root.addWidget(self.main_splitter, 1)

        self.list_widget = QListWidget()
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.list_widget.setTextElideMode(Qt.ElideNone)
        self.list_widget.setItemDelegate(EntrySummaryDelegate(self.list_widget))
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)
        self.main_splitter.addWidget(self.list_widget)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        detail_layout.setSpacing(10)
        self.main_splitter.addWidget(detail)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(16)
        detail_layout.addLayout(meta_row)

        self.type_label = QLabel("类型: -")
        self.type_label.setObjectName("meta_title")
        meta_row.addWidget(self.type_label)

        self.speaker_label = QLabel("角色: -")
        self.speaker_label.setObjectName("meta_title")
        meta_row.addWidget(self.speaker_label)

        self.status_label = QLabel("状态: -")
        self.status_label.setObjectName("meta_title")
        meta_row.addWidget(self.status_label)
        meta_row.addStretch()

        source_title = QLabel("原文")
        source_title.setObjectName("meta_title")
        detail_layout.addWidget(source_title)

        self.source_view = QTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setAcceptRichText(False)
        self.source_view.setMinimumHeight(160)
        detail_layout.addWidget(self.source_view)

        translation_title = QLabel("当前译文")
        translation_title.setObjectName("meta_title")
        detail_layout.addWidget(translation_title)

        self.translation_edit = QTextEdit()
        self.translation_edit.setAcceptRichText(False)
        self.translation_edit.textChanged.connect(self._on_translation_text_changed)
        detail_layout.addWidget(self.translation_edit, 1)

        btn_row = QHBoxLayout()
        self.btn_open_config = QPushButton("📂 打开配置目录")
        self.btn_open_config.setObjectName("open_config_btn")
        self.btn_open_config.setCursor(Qt.PointingHandCursor)
        self.btn_open_config.setToolTip("打开 config.json 和缓存所在的本地文件夹")
        self.btn_open_config.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                color: #888;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: transparent;
                color: #ccc;
                border-color: #666;
            }
            QPushButton:disabled {
                background-color: transparent;
                color: #666;
                border-color: #3b4252;
            }
            """
        )
        self.btn_open_config.clicked.connect(self._on_open_config_dir)
        btn_row.addWidget(self.btn_open_config)
        btn_row.addStretch()
        detail_layout.addLayout(btn_row)

        self.btn_cancel = QPushButton("取消修改")
        self.btn_cancel.setObjectName("editor_cancel_btn")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._reset_editor)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("保存译文")
        self.btn_save.setObjectName("editor_save_btn")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_current)
        btn_row.addWidget(self.btn_save)

        self._set_empty_state()
        self.set_bulk_idle()

    def set_game_title(self, game_title: str):
        game_title = str(game_title or "").strip()
        if game_title:
            self.game_title_label.setText(f"当前游戏：{game_title}")
            self.game_title_label.setToolTip(game_title)
        else:
            self.game_title_label.setText("当前游戏：未选择")
            self.game_title_label.setToolTip("")

    def has_unsaved_changes(self) -> bool:
        return bool(self._current_source and self._has_unsaved_changes)

    def set_bulk_idle(self):
        self._bulk_result_timer.stop()
        self.bulk_stack.setCurrentWidget(self.bulk_idle_page)
        self.btn_bulk_cancel.setVisible(False)
        self.btn_bulk_cancel.setEnabled(True)
        self.btn_bulk_translate.setEnabled(True)
        self.bulk_progress.setRange(0, 100)
        self.bulk_progress.setValue(0)
        self.bulk_progress.setFormat("0%")
        self.bulk_result_label.setText("")

    def set_bulk_preparing(self, message: str):
        self._bulk_result_timer.stop()
        self.bulk_stack.setCurrentWidget(self.bulk_progress_page)
        self.btn_bulk_cancel.setVisible(True)
        self.btn_bulk_cancel.setEnabled(True)
        self.bulk_progress.setRange(0, 100)
        self.bulk_progress.setValue(0)
        self.bulk_progress.setFormat(str(message or "0%"))

    def set_bulk_progress(self, done: int, total: int, status_text: str = ""):
        self._bulk_result_timer.stop()
        self.bulk_stack.setCurrentWidget(self.bulk_progress_page)
        self.btn_bulk_cancel.setVisible(True)
        self.btn_bulk_cancel.setEnabled(True)
        safe_total = max(int(total or 0), 0)
        safe_done = max(int(done or 0), 0)
        percent = 0
        if safe_total > 0:
            percent = min(100, int(round((safe_done / safe_total) * 100)))
        text = f"{percent}% ({safe_done}/{safe_total})"
        if status_text:
            text = f"{text} · {status_text}"
        self.bulk_progress.setRange(0, 100)
        self.bulk_progress.setValue(percent)
        self.bulk_progress.setFormat(text)

    def set_bulk_result(self, text: str, level: str = "info", auto_reset_ms: int = 5000):
        self.bulk_stack.setCurrentWidget(self.bulk_result_page)
        self.btn_bulk_cancel.setVisible(False)
        color_map = {
            "success": ("#dff6e6", "#1f8f4d"),
            "warning": ("#fff1d8", "#ad7a17"),
            "error": ("#ffdce2", "#b4233c"),
            "info": ("#dbeafe", "#2864d6"),
        }
        fg, border = color_map.get(level, color_map["info"])
        self.bulk_result_label.setStyleSheet(
            f"""
            QLabel {{
                color: {fg};
                border: 1px solid {border};
                border-radius: 7px;
                background-color: #0f1320;
                padding: 0 14px;
                font-size: 17px;
                font-weight: bold;
            }}
            """
        )
        self.bulk_result_label.setText(str(text or ""))
        if auto_reset_ms > 0:
            self._bulk_result_timer.start(int(auto_reset_ms))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._splitter_default_applied:
            QTimer.singleShot(0, self._apply_default_splitter_sizes)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        self._save_geometry()
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def closeEvent(self, event):
        self._save_geometry()
        if not self.confirm_discard_or_save(parent=self):
            event.ignore()
            return
        self.hide()
        event.ignore()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._save_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._save_geometry()

    def _apply_default_splitter_sizes(self):
        if self._splitter_default_applied or not hasattr(self, "main_splitter"):
            return
        total_width = max(self.main_splitter.size().width(), self.width() - 24)
        if total_width <= 0:
            return
        half_width = total_width // 2
        self.main_splitter.setSizes([half_width, total_width - half_width])
        self._splitter_default_applied = True

    def hide_with_autosave(self, parent: QWidget | None = None) -> bool:
        self._save_geometry()
        if not self.confirm_discard_or_save(parent=parent or self):
            return False
        self.hide()
        return True

    def save_pending_changes_silently(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        payload = self._build_save_payload()
        if not payload:
            self._set_dirty_state(False)
            return True
        self._apply_saved_payload(payload)
        self.autosave_requested.emit(payload)
        return True

    def discard_pending_changes(self):
        if not self.has_unsaved_changes():
            return
        entry = self._entries_by_source.get(self._current_source)
        if not entry:
            self._set_dirty_state(False)
            return
        self._apply_entry(entry)
        self._update_item_summary(self._current_source)

    def confirm_discard_or_save(self, parent: QWidget | None = None) -> bool:
        if not self.has_unsaved_changes():
            return True

        dialog = QDialog(parent or self)
        dialog.setWindowTitle("未保存译文")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumWidth(470)
        dialog.setStyleSheet(
            """
            QDialog {
                background-color: #101626;
                color: #edf2ff;
            }
            QLabel#unsaved_message {
                color: #edf2ff;
                font-size: 20px;
                line-height: 1.45;
            }
            QPushButton#unsaved_save_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b7cff, stop:1 #1f6feb);
                color: white;
                border: 1px solid #4d8cff;
                border-radius: 10px;
                padding: 11px 20px;
                font-size: 20px;
                font-weight: bold;
                min-width: 176px;
            }
            QPushButton#unsaved_save_btn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3988ff, stop:1 #2a78f0);
            }
            QPushButton#unsaved_save_btn:pressed {
                background: #185bd6;
            }
            QPushButton#unsaved_cancel_btn {
                background-color: transparent;
                color: #cdd6eb;
                border: 1px solid #495673;
                border-radius: 10px;
                padding: 11px 20px;
                font-size: 20px;
                font-weight: bold;
                min-width: 110px;
            }
            QPushButton#unsaved_cancel_btn:hover {
                color: #ffffff;
                border-color: #7482a2;
                background-color: rgba(255, 255, 255, 0.04);
            }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(18)

        message = QLabel("当前译文已修改，关闭前是否保存？")
        message.setObjectName("unsaved_message")
        message.setWordWrap(True)
        layout.addWidget(message)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("unsaved_cancel_btn")
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(dialog.reject)

        save_button = QPushButton("保存并关闭")
        save_button.setObjectName("unsaved_save_btn")
        save_button.setCursor(Qt.PointingHandCursor)
        save_button.clicked.connect(dialog.accept)

        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

        dialog.exec_()
        if dialog.result() == QDialog.Accepted:
            return self.save_pending_changes_silently()
        return False

    def _save_geometry(self):
        if self.isMinimized():
            return
        self.config["workbench_x"] = self.x()
        self.config["workbench_y"] = self.y()
        self.config["workbench_width"] = self.width()
        self.config["workbench_height"] = self.height()
        self.config_updated.emit(self.config)

    def update_config(self, new_config: dict):
        self.config = new_config
        pinned = self.config.get("workbench_pinned", self._is_pinned)
        if pinned != self._is_pinned:
            self._is_pinned = pinned
            self.btn_pin.setChecked(self._is_pinned)
            self._apply_pin_state()
            if self.isVisible():
                self.show()
                self.raise_()
        else:
            self.btn_pin.setChecked(self._is_pinned)

    def _apply_pin_state(self):
        if self._is_pinned:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)

    def _toggle_pin(self):
        self._is_pinned = self.btn_pin.isChecked()
        self.config["workbench_pinned"] = self._is_pinned
        self._apply_pin_state()
        self.show()
        self.raise_()
        self.config_updated.emit(self.config)

    def _set_editor_text(self, text: str):
        self._is_programmatic_text_change = True
        try:
            self.translation_edit.setPlainText(text)
        finally:
            self._is_programmatic_text_change = False

    def _set_dirty_state(self, dirty: bool, entry: dict[str, Any] | None = None):
        self._has_unsaved_changes = bool(dirty and self._current_source)
        self._update_status_label(entry)

    def _update_status_label(self, entry: dict[str, Any] | None = None):
        entry = entry or self._entries_by_source.get(self._current_source) or {}
        if self.has_unsaved_changes():
            status = "未保存草稿"
        else:
            status = "人工修改" if entry.get("is_manual") else "机翻/未人工修改"
        self.status_label.setText(f"状态: {status}")

    def _set_empty_state(self):
        self._current_source = ""
        self._has_unsaved_changes = False
        self.type_label.setText("类型: -")
        self.speaker_label.setText("角色: -")
        self.status_label.setText("状态: -")
        self.source_view.setPlainText("")
        self._set_editor_text("")
        self.translation_edit.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_cancel.setEnabled(False)

    def _build_display_entry(
        self,
        entry: dict[str, Any],
        translation_override: str | None = None,
        dirty_override: bool | None = None,
    ) -> dict[str, Any]:
        display_entry = dict(entry)
        if translation_override is not None:
            display_entry["translation"] = translation_override
        if dirty_override is not None:
            display_entry["is_dirty"] = dirty_override
        return display_entry

    def _format_entry_summary(self, entry: dict[str, Any]) -> str:
        kind = "对白" if entry.get("entry_type") != "choice" else "选项"
        if entry.get("is_dirty"):
            mark = "草稿"
        else:
            mark = "人工" if entry.get("is_manual") else "机翻"

        speaker = str(entry.get("speaker") or "").strip()
        speaker_label = speaker or "无角色"
        source = str(entry.get("source") or "").strip().replace("\n", " ")
        translation = str(entry.get("translation") or "").strip().replace("\n", " ")

        parts = [f"[{kind}][{mark}][{speaker_label}]"]
        parts.append(source or "(空原文)")
        line1 = " ".join(parts)
        line2 = translation or "(暂无译文)"
        return f"{line1}\n{line2}"

    def _find_item_by_source(self, source: str) -> QListWidgetItem | None:
        source = str(source or "").strip()
        if not source:
            return None
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item and item.data(Qt.UserRole) == source:
                return item
        return None

    def _update_item_summary(
        self,
        source: str,
        translation_override: str | None = None,
        dirty_override: bool | None = None,
    ):
        entry = self._entries_by_source.get(source)
        item = self._find_item_by_source(source)
        if not entry or not item:
            return
        display_entry = self._build_display_entry(
            entry,
            translation_override=translation_override,
            dirty_override=dirty_override,
        )
        item.setText(self._format_entry_summary(display_entry))

    def set_entries(self, entries: list[dict[str, Any]], selected_source: str = ""):
        preserve_source = selected_source or self._current_source
        active_source = self._current_source
        active_translation = self.translation_edit.toPlainText() if active_source else ""
        active_dirty = self.has_unsaved_changes()

        self._entries_by_source = {}
        for entry in entries:
            source = str(entry.get("source") or "").strip()
            if not source:
                continue
            self._entries_by_source[source] = dict(entry)

        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        current_row = -1
        for entry in entries:
            source = str(entry.get("source") or "").strip()
            if not source:
                continue
            display_entry = self._entries_by_source[source]
            if active_dirty and source == active_source:
                display_entry = self._build_display_entry(
                    display_entry,
                    translation_override=active_translation,
                    dirty_override=True,
                )
            item = QListWidgetItem(self._format_entry_summary(display_entry))
            item.setData(Qt.UserRole, source)
            self.list_widget.addItem(item)
            if source == preserve_source:
                current_row = self.list_widget.count() - 1

        if self.list_widget.count() == 0:
            self.list_widget.blockSignals(False)
            self._set_empty_state()
            return

        if current_row < 0:
            current_row = 0
        self.list_widget.setCurrentRow(current_row)
        current_item = self.list_widget.currentItem()
        self.list_widget.blockSignals(False)

        if not current_item:
            self._set_empty_state()
            return

        source = str(current_item.data(Qt.UserRole) or "")
        entry = self._entries_by_source.get(source)
        if not entry:
            self._set_empty_state()
            return

        self._current_source = source
        if active_dirty and source == active_source:
            self._apply_entry(
                entry,
                translation_override=active_translation,
                dirty_override=True,
                preserve_editor_text=True,
            )
        else:
            self._apply_entry(entry)

    def focus_entry(self, source: str):
        source = str(source or "").strip()
        if not source:
            return
        item = self._find_item_by_source(source)
        if not item:
            return
        self.list_widget.setCurrentItem(item)
        self.show()
        self.raise_()
        self.activateWindow()

    def _build_save_payload(
        self,
        source: str | None = None,
        translation: str | None = None,
    ) -> dict[str, Any] | None:
        source = str(source or self._current_source or "").strip()
        entry = self._entries_by_source.get(source)
        if not source or not entry:
            return None
        return {
            "source": source,
            "translation": (
                self.translation_edit.toPlainText().strip()
                if translation is None
                else str(translation).strip()
            ),
            "entry_type": entry.get("entry_type", "dialogue"),
            "speaker": entry.get("speaker", ""),
        }

    def _apply_saved_payload(self, payload: dict[str, Any]):
        source = str(payload.get("source") or "").strip()
        entry = self._entries_by_source.get(source)
        if not entry:
            return
        entry["translation"] = str(payload.get("translation") or "")
        entry["entry_type"] = payload.get("entry_type", entry.get("entry_type", "dialogue"))
        entry["speaker"] = payload.get("speaker", entry.get("speaker", ""))
        entry["is_manual"] = True
        entry.pop("is_dirty", None)
        if source == self._current_source:
            self._set_dirty_state(False, entry)
        self._update_item_summary(source)

    def _on_current_item_changed(self, current, previous):
        previous_source = str(previous.data(Qt.UserRole) or "") if previous else ""
        if previous_source and previous_source == self._current_source:
            self.save_pending_changes_silently()

        if not current:
            self._set_empty_state()
            return

        source = str(current.data(Qt.UserRole) or "")
        entry = self._entries_by_source.get(source)
        if not entry:
            self._set_empty_state()
            return

        self._current_source = source
        self._apply_entry(entry)

    def _apply_entry(
        self,
        entry: dict[str, Any],
        translation_override: str | None = None,
        dirty_override: bool = False,
        preserve_editor_text: bool = False,
    ):
        kind = "对白" if entry.get("entry_type") != "choice" else "选项"
        speaker = str(entry.get("speaker") or "").strip() or "-"
        dirty = bool(dirty_override)

        self.type_label.setText(f"类型: {kind}")
        self.speaker_label.setText(f"角色: {speaker}")
        self.source_view.setPlainText(str(entry.get("source") or ""))

        if preserve_editor_text and self.translation_edit.isEnabled():
            current_text = self.translation_edit.toPlainText()
            if translation_override is not None and current_text != translation_override:
                self._set_editor_text(translation_override)
        else:
            translation_text = str(
                entry.get("translation") if translation_override is None else translation_override
            )
            self._set_editor_text(translation_text)

        self.translation_edit.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_cancel.setEnabled(True)
        self._set_dirty_state(dirty, entry)

    def _on_translation_text_changed(self):
        if self._is_programmatic_text_change or not self._current_source:
            return
        entry = self._entries_by_source.get(self._current_source)
        if not entry:
            return
        current_text = self.translation_edit.toPlainText()
        dirty = current_text != str(entry.get("translation") or "")
        self._set_dirty_state(dirty, entry)
        self._update_item_summary(
            self._current_source,
            translation_override=current_text,
            dirty_override=dirty,
        )

    def _reset_editor(self):
        entry = self._entries_by_source.get(self._current_source)
        if not entry:
            return
        self._apply_entry(entry)
        self._update_item_summary(self._current_source)

    def _on_open_config_dir(self):
        """Open the local config directory used by RenpyLens."""
        from config import CONFIG_DIR
        import os
        import platform
        import subprocess

        try:
            if platform.system() == "Windows":
                os.startfile(CONFIG_DIR)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", CONFIG_DIR])
            else:
                subprocess.Popen(["xdg-open", CONFIG_DIR])
        except Exception as e:
            print(f"无法打开配置目录: {e}")

    def _save_current(self):
        payload = self._build_save_payload()
        if not payload:
            return
        self._apply_saved_payload(payload)
        self.save_requested.emit(payload)
