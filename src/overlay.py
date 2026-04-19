# -*- coding: utf-8 -*-
"""Translation overlay window with read mode and inline edit mode."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QApplication,
    QMenu,
    QAction,
    QTextEdit,
    QPushButton,
    QHBoxLayout,
)
from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QPainterPath, QColor, QFontMetrics, QPen, QTransform, QTextCursor
import win32con
import win32gui


class OutlinedLabel(QLabel):
    """Label that draws white text with a black outline."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._outline_width = 2
        self._font_size = 22
        self._font_family = "Microsoft YaHei"
        self._font_bold = True
        self._text_color = QColor(255, 255, 255)
        self._outline_color = QColor(0, 0, 0)
        self.setFont(QFont(self._font_family, self._font_size, QFont.Bold if self._font_bold else QFont.Normal))
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")

    def set_font_size(self, size: int):
        self._font_size = size
        self.setFont(QFont(self._font_family, size, QFont.Bold if self._font_bold else QFont.Normal))
        self.update()

    def set_font_family(self, family: str):
        self._font_family = family
        self.setFont(QFont(family, self._font_size, QFont.Bold if self._font_bold else QFont.Normal))
        self.update()

    def set_font_bold(self, bold: bool):
        self._font_bold = bold
        self.setFont(QFont(self._font_family, self._font_size, QFont.Bold if bold else QFont.Normal))
        self.update()

    def set_text_color(self, color_str: str):
        self._text_color = QColor(color_str)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        text = self.text()
        if not text:
            return

        text = text.replace("<b>", "").replace("</b>", "")
        text = text.replace("<div style='font-weight: 900;'>", "").replace("</div>", "")

        import re

        tokens = re.split(r"(</i>|<i>)", text)
        char_list = []
        is_italic = False
        for token in tokens:
            if token == "<i>":
                is_italic = True
            elif token == "</i>":
                is_italic = False
            elif token:
                for char in token:
                    char_list.append((char, is_italic))

        font_normal = self.font()
        fm_normal = QFontMetrics(font_normal)
        available_width = self.width() - self._outline_width * 4

        lines = []
        current_line = []
        current_line_width = 0
        for char, italic in char_list:
            if char == "\n":
                lines.append(current_line)
                current_line = []
                current_line_width = 0
                continue

            char_w = fm_normal.horizontalAdvance(char)
            if current_line_width + char_w > available_width and current_line:
                lines.append(current_line)
                current_line = [(char, italic)]
                current_line_width = char_w
            else:
                current_line.append((char, italic))
                current_line_width += char_w

        if current_line:
            lines.append(current_line)

        path = QPainterPath()
        y_offset = fm_normal.ascent() + self._outline_width
        for line in lines:
            x_offset = self._outline_width * 2
            merged_chunks = []
            for char, italic in line:
                if not merged_chunks:
                    merged_chunks.append([char, italic])
                elif merged_chunks[-1][1] == italic:
                    merged_chunks[-1][0] += char
                else:
                    merged_chunks.append([char, italic])

            for text_chunk, italic in merged_chunks:
                sub_path = QPainterPath()
                sub_path.addText(0, 0, font_normal, text_chunk)
                if italic:
                    sub_path = QTransform().shear(-0.25, 0.0).map(sub_path)
                path.addPath(sub_path.translated(x_offset, y_offset))
                x_offset += fm_normal.horizontalAdvance(text_chunk)

            y_offset += fm_normal.height()

        painter.setPen(
            QPen(
                self._outline_color,
                self._outline_width * 2,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
        )
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._text_color)
        painter.drawPath(path)
        painter.end()

        needed_height = int(y_offset + self._outline_width * 2)
        if getattr(self, "_last_needed_height", None) != needed_height:
            self._last_needed_height = needed_height
            self.setFixedHeight(needed_height)


class TranslationOverlay(QWidget):
    config_updated = pyqtSignal(dict)
    visibility_changed = pyqtSignal(bool)
    edit_saved = pyqtSignal(dict)
    autosave_requested = pyqtSignal(dict)
    show_workbench_requested = pyqtSignal()

    READ_MIN_WIDTH = 280
    EDIT_MIN_WIDTH = 420
    EDIT_MIN_HEIGHT = 150
    RESIZE_HOTZONE = 16

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._drag_pos = None
        self._is_resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        self._edit_context = {"dialogue": None, "choices": []}
        self._editing_target = None
        self._pending_text = None
        self._edit_base_text = ""
        self._edit_is_dirty = False
        self._is_programmatic_text_change = False

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.label = OutlinedLabel("等待游戏文本...", self)
        self.label.set_font_family(config.get("font_family", "Microsoft YaHei"))
        self.label.set_font_size(config.get("font_size", 22))
        self.label.set_font_bold(config.get("font_bold", True))
        self.label.set_text_color(config.get("font_color", "#FFFFFF"))
        layout.addWidget(self.label)

        self.editor_container = QWidget(self)
        self.editor_container.setObjectName("editorContainer")
        self.editor_container.setStyleSheet(
            """
            QWidget#editorContainer {
                background-color: rgba(12, 17, 29, 244);
                border: 1px solid rgba(79, 108, 160, 196);
                border-radius: 10px;
            }
            QWidget#editorFooter {
                background: transparent;
                border: none;
            }
            QTextEdit {
                background-color: rgba(7, 11, 20, 238);
                color: #f3f6ff;
                border: 1px solid #333a52;
                border-radius: 6px;
                padding: 10px;
                font-size: 24px;
                selection-background-color: #446ed6;
            }
            QTextEdit[placeholderText]:empty {
                color: #7e8ba5;
            }
            QPushButton {
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton#editCancelButton {
                background-color: transparent;
                color: #cdd6eb;
                border: 1px solid #495673;
                border-radius: 8px;
                padding: 8px 16px;
                min-width: 104px;
            }
            QPushButton#editCancelButton:hover {
                background-color: rgba(255, 255, 255, 0.04);
                color: #ffffff;
                border-color: #7482a2;
            }
            QPushButton#editCancelButton:pressed {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QPushButton#editSaveButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b7cff, stop:1 #1f6feb);
                color: white;
                border: 1px solid #4d8cff;
                border-radius: 8px;
                padding: 8px 18px;
                min-width: 116px;
            }
            QPushButton#editSaveButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3988ff, stop:1 #2a78f0);
            }
            QPushButton#editSaveButton:pressed {
                background: #185bd6;
            }
            QLabel#resizeHandle {
                color: #9abfff;
                border: none;
                background: transparent;
                padding: 0px;
            }
            """
        )
        editor_layout = QVBoxLayout(self.editor_container)
        editor_layout.setContentsMargins(8, 8, 8, 8)
        editor_layout.setSpacing(6)

        self.edit_text = QTextEdit()
        self.edit_text.setAcceptRichText(False)
        self.edit_text.setMinimumHeight(88)
        self.edit_text.textChanged.connect(self._on_edit_text_changed)
        self.edit_text.setPlaceholderText("在这里直接修改当前对白或选项译文")
        editor_layout.addWidget(self.edit_text, 1)

        self.editor_footer = QWidget(self.editor_container)
        self.editor_footer.setObjectName("editorFooter")
        self.editor_footer.setCursor(Qt.OpenHandCursor)
        self.editor_footer.setToolTip("拖动底部空白区域移动窗口")
        btn_row = QHBoxLayout(self.editor_footer)
        btn_row.setContentsMargins(0, 0, 34, 0)
        btn_row.setSpacing(6)
        btn_row.addStretch()

        self.btn_cancel_edit = QPushButton("取消")
        self.btn_cancel_edit.setObjectName("editCancelButton")
        self.btn_cancel_edit.setCursor(Qt.PointingHandCursor)
        self.btn_cancel_edit.clicked.connect(self.cancel_edit)
        btn_row.addWidget(self.btn_cancel_edit)

        self.btn_save_edit = QPushButton("保存")
        self.btn_save_edit.setObjectName("editSaveButton")
        self.btn_save_edit.setCursor(Qt.PointingHandCursor)
        self.btn_save_edit.clicked.connect(self._emit_save)
        btn_row.addWidget(self.btn_save_edit)

        self.resize_handle = QLabel("◢", self.editor_container)
        self.resize_handle.setObjectName("resizeHandle")
        self.resize_handle.setAlignment(Qt.AlignCenter)
        self.resize_handle.setCursor(Qt.SizeFDiagCursor)
        self.resize_handle.setToolTip("拖动这里调整编辑窗口大小")
        self.resize_handle.setFixedSize(26, 26)

        editor_layout.addWidget(self.editor_footer)

        self.editor_container.installEventFilter(self)
        self.editor_footer.installEventFilter(self)
        self.resize_handle.installEventFilter(self)
        self._apply_editor_fonts()
        self._position_resize_handle()

        self.editor_container.hide()
        layout.addWidget(self.editor_container)

        self.setGeometry(
            config.get("overlay_x", 560),
            config.get("overlay_y", 800),
            config.get("overlay_width", 800),
            80,
        )

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def _save_config(self):
        from config import save_config

        save_config(self.config)
        self.config_updated.emit(self.config)

    def _screen_limits(self) -> tuple[int, int]:
        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            geometry = primary_screen.availableGeometry()
            return geometry.width(), geometry.height()
        return 1920, 1080

    def _clamp_overlay_width(self, width: int) -> int:
        screen_width, _ = self._screen_limits()
        max_width = max(self.READ_MIN_WIDTH, screen_width - 24)
        return max(self.READ_MIN_WIDTH, min(int(width), max_width))

    def _minimum_edit_width(self) -> int:
        return self.EDIT_MIN_WIDTH

    def _minimum_edit_height(self) -> int:
        return self.EDIT_MIN_HEIGHT

    def _clamp_edit_size(self, width: int, height: int) -> tuple[int, int]:
        screen_width, screen_height = self._screen_limits()
        min_width = self._minimum_edit_width()
        min_height = self._minimum_edit_height()
        max_width = max(min_width, screen_width - 24)
        max_height = max(min_height, screen_height - 24)
        return (
            max(min_width, min(int(width), max_width)),
            max(min_height, min(int(height), max_height)),
        )

    def _apply_editor_fonts(self):
        family = self.config.get("font_family", "Microsoft YaHei")
        base_size = int(self.config.get("font_size", 22))
        editor_text_px = max(24, min(base_size + 2, 28))
        button_px = max(17, min(editor_text_px - 6, 18))

        editor_font = QFont(family)
        editor_font.setPixelSize(editor_text_px)
        editor_font.setBold(False)
        self.edit_text.setFont(editor_font)
        self.edit_text.setMinimumHeight(max(88, editor_text_px * 4))

        button_font = QFont(family)
        button_font.setPixelSize(button_px)
        button_font.setBold(True)
        self.btn_cancel_edit.setFont(button_font)
        self.btn_save_edit.setFont(button_font)

        handle_font = QFont(family)
        handle_font.setPixelSize(20)
        handle_font.setBold(True)
        self.resize_handle.setFont(handle_font)

    def _position_resize_handle(self):
        if not hasattr(self, "resize_handle") or self.resize_handle.parent() is not self.editor_container:
            return
        margin = 4
        x = max(margin, self.editor_container.width() - self.resize_handle.width() - margin)
        y = max(margin, self.editor_container.height() - self.resize_handle.height() - margin)
        self.resize_handle.move(x, y)
        self.resize_handle.raise_()

    def _set_edit_dirty_state(self, dirty: bool, base_text: str | None = None):
        self._edit_is_dirty = bool(dirty and self._editing_target)
        if base_text is not None:
            self._edit_base_text = str(base_text)

    def _set_edit_text_programmatically(self, text: str):
        self._is_programmatic_text_change = True
        try:
            self.edit_text.setPlainText(str(text or ""))
        finally:
            self._is_programmatic_text_change = False

    def _on_edit_text_changed(self):
        if self._is_programmatic_text_change or not self._editing_target:
            return
        current_text = self.edit_text.toPlainText()
        self._set_edit_dirty_state(current_text != self._edit_base_text)

    def _build_edit_payload(self) -> dict | None:
        if not self._editing_target:
            return None
        payload = dict(self._editing_target)
        payload["translation"] = self.edit_text.toPlainText().strip()
        return payload

    def _same_edit_target(self, left: dict | None, right: dict | None) -> bool:
        if not left or not right:
            return False
        return (
            str(left.get("source") or "") == str(right.get("source") or "")
            and str(left.get("entry_type") or "") == str(right.get("entry_type") or "")
            and int(left.get("choice_index", -1)) == int(right.get("choice_index", -1))
        )

    def _resolve_follow_target(self, dialogue_target: dict | None, choice_targets: list[dict]) -> dict | None:
        if not self._editing_target:
            return None

        entry_type = self._editing_target.get("entry_type")
        if entry_type == "choice":
            choice_index = int(self._editing_target.get("choice_index", -1))
            if 0 <= choice_index < len(choice_targets):
                return dict(choice_targets[choice_index])
            if dialogue_target:
                return dict(dialogue_target)
            return None

        if dialogue_target:
            return dict(dialogue_target)
        return None

    def _apply_edit_target(self, target: dict, move_cursor_end: bool = True):
        self._editing_target = dict(target)
        base_text = str(target.get("translation") or "")
        self._set_edit_dirty_state(False, base_text=base_text)
        self._set_edit_text_programmatically(base_text)
        self.label.hide()
        self.editor_container.show()
        self._restore_edit_window()
        self.edit_text.setFocus()
        if move_cursor_end:
            self.edit_text.moveCursor(QTextCursor.End)
        self._enforce_topmost()

    def _exit_edit_mode(self):
        self._editing_target = None
        self._set_edit_dirty_state(False, base_text="")
        self.editor_container.hide()
        self.label.show()
        if self._pending_text is not None:
            self.label.setText(self._pending_text)
            self._pending_text = None
        self._restore_read_window()

    def _autosave_current_edit_if_needed(self):
        if not self._editing_target or not self._edit_is_dirty:
            return
        payload = self._build_edit_payload()
        if not payload:
            return
        self._set_edit_dirty_state(False, base_text=self.edit_text.toPlainText())
        self.autosave_requested.emit(payload)

    def _restore_read_window(self):
        self.config["overlay_width"] = self._clamp_overlay_width(self.config.get("overlay_width", self.width()))
        self.resize(self.config["overlay_width"], self.height())
        self._adjust_height()

    def _restore_edit_window(self):
        width = self.config.get("overlay_edit_width", 480)
        height = self.config.get("overlay_edit_height", 150)
        width, height = self._clamp_edit_size(width, height)
        self.config["overlay_edit_width"] = width
        self.config["overlay_edit_height"] = height
        self.resize(width, height)
        self._position_resize_handle()

    def _persist_window_geometry(self, include_size: bool = False):
        self.config["overlay_x"] = self.x()
        self.config["overlay_y"] = self.y()
        if include_size:
            if self._editing_target:
                self.config["overlay_edit_width"] = self.width()
                self.config["overlay_edit_height"] = self.height()
            else:
                self.config["overlay_width"] = self.width()
        self._save_config()

    def eventFilter(self, watched, event):
        if watched is self.editor_container and event.type() == QEvent.Resize:
            self._position_resize_handle()
            return False
        if watched is self.editor_footer:
            return self._handle_editor_footer_event(event)
        if watched is self.resize_handle:
            return self._handle_editor_resize_event(event)
        return super().eventFilter(watched, event)

    def _handle_editor_footer_event(self, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self.editor_footer.setCursor(Qt.ClosedHandCursor)
            return True
        if event.type() == QEvent.MouseMove:
            if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPos() - self._drag_pos)
                self.config["overlay_x"] = self.x()
                self.config["overlay_y"] = self.y()
                return True
            self.editor_footer.setCursor(Qt.OpenHandCursor)
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            moved = self._drag_pos is not None
            self._drag_pos = None
            self.editor_footer.setCursor(Qt.OpenHandCursor)
            if moved:
                self._persist_window_geometry()
            return moved
        return False

    def _handle_editor_resize_event(self, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._is_resizing = True
            self._resize_start_pos = event.globalPos()
            self._resize_start_size = self.size()
            return True
        if event.type() == QEvent.MouseMove and self._is_resizing and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self._resize_start_pos
            width, height = self._clamp_edit_size(
                self._resize_start_size.width() + delta.x(),
                self._resize_start_size.height() + delta.y(),
            )
            self.resize(width, height)
            self.config["overlay_edit_width"] = width
            self.config["overlay_edit_height"] = height
            return True
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            resized = self._is_resizing
            self._is_resizing = False
            self._resize_start_pos = None
            self._resize_start_size = None
            if resized:
                self._persist_window_geometry(include_size=True)
            return resized
        return False

    def _enforce_topmost(self):
        if not self.isVisible() or not self.config.get("force_topmost", False):
            return
        try:
            hwnd = int(self.winId())
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
        except Exception as e:
            print(f"[Overlay] Failed to enforce topmost: {e}")

    def update_config(self, new_config: dict):
        self.config = new_config
        self.label.set_font_family(self.config.get("font_family", "Microsoft YaHei"))
        self.label.set_font_size(self.config.get("font_size", 22))
        self.label.set_font_bold(self.config.get("font_bold", True))
        self.label.set_text_color(self.config.get("font_color", "#FFFFFF"))
        self._apply_editor_fonts()
        if self._editing_target:
            self._restore_edit_window()
        else:
            self._restore_read_window()
        self._enforce_topmost()

    def set_edit_context(self, dialogue_target: dict | None, choice_targets: list[dict]):
        self._edit_context = {
            "dialogue": dict(dialogue_target) if dialogue_target else None,
            "choices": [dict(item) for item in (choice_targets or [])],
        }
        if not self._editing_target:
            return

        next_target = self._resolve_follow_target(
            self._edit_context.get("dialogue"),
            self._edit_context.get("choices", []),
        )
        if self._same_edit_target(self._editing_target, next_target):
            return

        self._autosave_current_edit_if_needed()
        if self._same_edit_target(self._editing_target, next_target):
            return

        if next_target:
            self._apply_edit_target(next_target)
            return

        self._exit_edit_mode()

    def set_text(self, text: str):
        if self._editing_target:
            self._pending_text = text
            return
        self._pending_text = None
        self.label.setText(text)
        self._enforce_topmost()
        QTimer.singleShot(20, self._adjust_height)

    def start_edit(self, target: dict | None):
        if not target:
            return
        self._apply_edit_target(dict(target))

    def cancel_edit(self):
        if not self._editing_target:
            return
        self._exit_edit_mode()

    def _emit_save(self):
        if not self._editing_target:
            return
        payload = self._build_edit_payload()
        if not payload:
            return
        self._exit_edit_mode()
        self.edit_saved.emit(payload)

    def reset_to_default_position(self):
        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            screen = primary_screen.geometry()
            screen_w = screen.width()
            screen_h = screen.height()
            width = self.config.get("overlay_width", 800)
            x = (screen_w - width) // 2
            y = int(screen_h * 0.75)
        else:
            x, y = 560, 800

        self.move(x, y)
        self._persist_window_geometry()

    def _adjust_height(self):
        if self.editor_container.isVisible():
            self._restore_edit_window()
            return
        needed = self.label.height() + 12
        width = self._clamp_overlay_width(self.config.get("overlay_width", self.width()))
        self.config["overlay_width"] = width
        self.resize(width, max(needed, 40))

    def set_font_size(self, size: int):
        self.config["font_size"] = size
        self.label.set_font_size(size)
        self._apply_editor_fonts()
        self._adjust_height()
        self._save_config()

    def set_font_family(self, family: str):
        self.config["font_family"] = family
        self.label.set_font_family(family)
        self._apply_editor_fonts()
        self._adjust_height()
        self._save_config()

    def set_font_bold(self, bold: bool):
        self.config["font_bold"] = bold
        self.label.set_font_bold(bold)
        self._apply_editor_fonts()
        self._adjust_height()
        self._save_config()

    def set_text_color(self, color_str: str):
        self.config["font_color"] = color_str
        self.label.set_text_color(color_str)
        self._save_config()

    def mousePressEvent(self, event):
        if self.editor_container.isVisible():
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            if event.pos().x() >= self.width() - self.RESIZE_HOTZONE:
                self._is_resizing = True
            else:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.editor_container.isVisible():
            return super().mouseMoveEvent(event)
        if self._is_resizing:
            new_width = self._clamp_overlay_width(event.globalPos().x() - self.x())
            self.resize(new_width, self.height())
            self.config["overlay_width"] = new_width
        elif self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            self.config["overlay_x"] = self.x()
            self.config["overlay_y"] = self.y()
        else:
            self.setCursor(Qt.SizeHorCursor if event.pos().x() >= self.width() - self.RESIZE_HOTZONE else Qt.OpenHandCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.editor_container.isVisible():
            return super().mouseReleaseEvent(event)
        changed = self._drag_pos is not None or self._is_resizing
        self._drag_pos = None
        self._is_resizing = False
        self.setCursor(Qt.OpenHandCursor)
        if changed:
            self._persist_window_geometry(include_size=True)
        event.accept()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #555;
                padding: 5px;
            }
            QMenu::item:selected {
                background-color: #4a9eff;
            }
            """
        )

        if self._editing_target:
            cancel_action = menu.addAction("取消编辑")
            cancel_action.triggered.connect(self.cancel_edit)
            menu.exec_(self.mapToGlobal(pos))
            return

        copy_action = menu.addAction("复制文本")
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(self.label.text()))

        if self._edit_context.get("dialogue"):
            menu.addSeparator()
            edit_dialogue_action = menu.addAction("编辑当前对白")
            edit_dialogue_action.triggered.connect(
                lambda checked=False, target=self._edit_context["dialogue"]: self.start_edit(target)
            )

        choice_targets = self._edit_context.get("choices", [])
        if choice_targets:
            choice_menu = menu.addMenu("编辑选项")
            for target in choice_targets:
                title = str(target.get("menu_label") or "选项").strip()
                action = choice_menu.addAction(title)
                action.triggered.connect(
                    lambda checked=False, item=target: self.start_edit(item)
                )

        menu.addSeparator()
        workbench_action = menu.addAction("显示工作台")
        workbench_action.triggered.connect(self.show_workbench_requested.emit)

        size_menu = menu.addMenu("字体大小")
        for size in [16, 18, 20, 22, 24, 28, 32, 36, 40]:
            act = size_menu.addAction(f"{size}px" + (" ✓" if size == self.config.get("font_size") else ""))
            act.triggered.connect(lambda checked=False, value=size: self.set_font_size(value))

        family_menu = menu.addMenu("字体")
        families = [
            ("微软雅黑", "Microsoft YaHei"),
            ("等线", "DengXian"),
            ("黑体", "SimHei"),
            ("宋体", "SimSun"),
            ("楷体", "KaiTi"),
            ("仿宋", "FangSong"),
        ]
        current_family = self.config.get("font_family", "Microsoft YaHei")
        for name, family in families:
            act = family_menu.addAction(name + (" ✓" if family == current_family else ""))
            act.triggered.connect(lambda checked=False, value=family: self.set_font_family(value))

        family_menu.addSeparator()
        bold_action = family_menu.addAction("粗体")
        bold_action.setCheckable(True)
        bold_action.setChecked(self.config.get("font_bold", True))
        bold_action.triggered.connect(self.set_font_bold)

        color_menu = menu.addMenu("字体颜色")
        colors = [
            ("白色", "#FFFFFF"),
            ("灰色", "#AAAAAA"),
            ("红色", "#FF5555"),
            ("绿色", "#32CD32"),
            ("黄色", "#FFD700"),
            ("蓝色", "#55A0FF"),
            ("粉色", "#FF80DF"),
        ]
        current_color = self.config.get("font_color", "#FFFFFF")
        for name, hex_val in colors:
            act = color_menu.addAction(name + (" ✓" if hex_val == current_color else ""))
            act.triggered.connect(lambda checked=False, value=hex_val: self.set_text_color(value))

        width_menu = menu.addMenu("文本框宽度")
        primary_screen = QApplication.primaryScreen()
        screen_width = primary_screen.geometry().width() if primary_screen else 1920
        current_width = self.config.get("overlay_width", self.width())

        pcts = [30, 40, 50, 60, 80, 100]
        closest_pct = None
        min_diff = float("inf")
        for pct in pcts:
            diff = abs(current_width - int(screen_width * pct / 100))
            if diff < min_diff:
                min_diff = diff
                closest_pct = pct

        for pct in pcts:
            target_width = int(screen_width * pct / 100)
            checked = (pct == closest_pct) and (min_diff < screen_width * 0.05)
            act = width_menu.addAction(f"{pct}%" + (" ✓" if checked else ""))
            act.triggered.connect(lambda checked=False, value=target_width: self._set_width(value))

        menu.addSeparator()

        show_name_action = QAction("显示说话人名称", self)
        show_name_action.setCheckable(True)
        show_name_action.setChecked(self.config.get("show_character_name", True))
        show_name_action.triggered.connect(self._toggle_show_name)
        menu.addAction(show_name_action)

        force_topmost_action = QAction("强力置顶 (解决全屏)", self)
        force_topmost_action.setCheckable(True)
        force_topmost_action.setChecked(self.config.get("force_topmost", True))
        force_topmost_action.triggered.connect(self._toggle_force_topmost)
        menu.addAction(force_topmost_action)

        menu.addSeparator()
        quit_action = menu.addAction("关闭浮窗")
        quit_action.triggered.connect(self.hide)

        menu.exec_(self.mapToGlobal(pos))

    def _set_width(self, width: int):
        width = self._clamp_overlay_width(width)
        self.resize(width, self.height())
        self.config["overlay_width"] = width
        self._adjust_height()
        self._save_config()

    def _toggle_show_name(self, checked: bool):
        self.config["show_character_name"] = checked
        self._save_config()

    def _toggle_force_topmost(self, checked: bool):
        self.config["force_topmost"] = checked
        self._enforce_topmost()
        self._save_config()
