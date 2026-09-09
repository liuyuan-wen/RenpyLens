# -*- coding: utf-8 -*-
"""Translation overlay window with read mode and inline edit mode."""

from __future__ import annotations

from collections import OrderedDict
import re

from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QApplication,
    QMenu,
    QAction,
    QWidgetAction,
    QCheckBox,
    QStyle,
    QStyleOptionButton,
    QTextEdit,
    QPushButton,
    QHBoxLayout,
    QScrollBar,
)
from PyQt5.QtCore import QEvent, QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QPen, QPixmap, QColor, QFontMetrics, QTextCursor
import win32con
import win32gui
from i18n import manager as i18n_manager, tr


class AutoCopyCheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.fillRect(rect, QColor("#f5f5f5"))
        painter.setPen(QColor("#888888"))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self.isChecked():
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#202020"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            points = [QPointF(rect.x() + rect.width() * x, rect.y() + rect.height() * y)
                      for x, y in ((0.22, 0.50), (0.43, 0.72), (0.79, 0.27))]
            painter.drawPolyline(*points)


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
        self._screen_text_mode = False
        self._text_layout_cache_key = None
        self._text_layout_cache = None
        self._paint_cache_key = None
        self._paint_cache = None
        self._long_text_mode = False
        self._scroll_offset = 0
        self._paint_frozen = False
        self._line_images = OrderedDict()
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

    def set_screen_text_mode(self, enabled: bool):
        self._screen_text_mode = bool(enabled)
        self.update()

    def set_long_text_mode(self, enabled: bool):
        self._long_text_mode = bool(enabled)
        self.update()

    def set_scroll_offset(self, value: int):
        value = max(0, int(value))
        if value == self._scroll_offset:
            return
        self._scroll_offset = value
        self.update()

    def set_paint_frozen(self, frozen: bool):
        self._paint_frozen = bool(frozen)

    def _get_text_layout(self):
        text = self.text()
        font_normal = self.font()
        available_width = self.width() - self._outline_width * 4
        cache_key = (
            text,
            available_width,
            font_normal.toString(),
            self._outline_width,
        )
        if cache_key == self._text_layout_cache_key:
            return self._text_layout_cache

        text = text.replace("<b>", "").replace("</b>", "")
        text = text.replace("<div style='font-weight: 900;'>", "").replace("</div>", "")

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

        fm_normal = QFontMetrics(font_normal)
        lines = []
        current_line = []
        current_line_width = 0
        char_widths = {}
        for char, italic in char_list:
            if char == "\n":
                lines.append(current_line)
                current_line = []
                current_line_width = 0
                continue

            if char not in char_widths:
                char_widths[char] = fm_normal.horizontalAdvance(char)
            char_w = char_widths[char]
            if current_line_width + char_w > available_width and current_line:
                lines.append(current_line)
                current_line = [(char, italic)]
                current_line_width = char_w
            else:
                current_line.append((char, italic))
                current_line_width += char_w

        if current_line:
            lines.append(current_line)

        # Measuring height must not construct outlines for the whole document.
        # Rasterize only the lines intersecting the viewport.
        needed_height = int(
            fm_normal.ascent() + self._outline_width * 3
            + len(lines) * fm_normal.height()
        )
        self._text_layout_cache_key = cache_key
        self._text_layout_cache = (lines, needed_height)
        return self._text_layout_cache

    def _get_line_image(self, line):
        # Cache pixels, not vector outlines: stroking hundreds of Chinese glyphs
        # on every wheel event is substantially more expensive than compositing.
        key = (tuple(line), self.font().toString(), self._text_color.rgba(),
               self._outline_color.rgba(), self._outline_width, self.devicePixelRatioF())
        if key in self._line_images:
            self._line_images.move_to_end(key)
            return self._line_images[key]
        fm = self.fontMetrics()
        chunks = []
        for char, italic in line:
            if chunks and chunks[-1][1] == italic:
                chunks[-1][0] += char
            else:
                chunks.append([char, italic])
        margin = self._outline_width * 2
        width = sum(fm.horizontalAdvance(text) for text, _ in chunks)
        dpr = self.devicePixelRatioF()
        pixmap = QPixmap(int((width + margin * 2 + fm.height()) * dpr),
                         int((fm.height() + margin * 2) * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setFont(self.font())
        painter.setRenderHint(QPainter.TextAntialiasing)
        x = margin
        for text, italic in chunks:
            painter.save()
            painter.translate(x, fm.ascent() + self._outline_width)
            if italic:
                painter.shear(-0.25, 0)
            painter.setPen(self._outline_color)
            radius = self._outline_width
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if 0 < dx * dx + dy * dy <= radius * radius:
                        painter.drawText(dx, dy, text)
            painter.setPen(self._text_color)
            painter.drawText(0, 0, text)
            painter.restore()
            x += fm.horizontalAdvance(text)
        painter.end()
        self._line_images[key] = pixmap
        if len(self._line_images) > 256:
            self._line_images.popitem(last=False)
        return pixmap

    def _get_painted_text(self, path):
        cache_key = (
            self._text_layout_cache_key,
            self.width(),
            self.height(),
            self._scroll_offset,
            self.devicePixelRatioF(),
            self._screen_text_mode,
            self._long_text_mode,
            self._text_color.rgba(),
            self._outline_color.rgba(),
        )
        if self._paint_frozen and self._paint_cache is not None:
            return self._paint_cache
        if cache_key == self._paint_cache_key:
            return self._paint_cache

        # Long text can be tens of thousands of pixels tall. Rasterizing that
        # entire off-screen surface makes width changes visibly stall after the
        # mouse is released. Cache only the visible viewport instead.
        dpr = self.devicePixelRatioF()
        pixmap = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        content_height = self._text_layout_cache[1] if self._text_layout_cache else self.height()
        source_y = min(self._scroll_offset, max(0, content_height - self.height()))
        line_height = self.fontMetrics().height()
        first = max(0, source_y // line_height - 1)
        last = min(len(path), (source_y + self.height()) // line_height + 2)
        for index in range(first, last):
            painter.drawPixmap(0, index * line_height - source_y,
                               self._get_line_image(path[index]))
        painter.end()

        self._paint_cache_key = cache_key
        self._paint_cache = pixmap
        return pixmap

    def paintEvent(self, event):
        if not self.text():
            return
        if self._paint_frozen and self._text_layout_cache is not None:
            path, _ = self._text_layout_cache
        else:
            path, _ = self._get_text_layout()

        pixmap = self._get_painted_text(path)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()


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
    LONG_TEXT_THRESHOLD_RATIO = 0.35
    LONG_TEXT_HEIGHT_RATIO = 0.30
    LONG_TEXT_MIN_HEIGHT = 120
    LONG_TEXT_PANEL_PADDING = 8
    READ_OUTER_MARGIN = 4
    READ_RESIZE_HANDLE_SIZE = 10
    READ_RESIZE_CORNER_SIZE = 18
    TOPMOST_ENFORCE_INTERVAL_MS = 250

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._auto_copy_original = None
        self._auto_copy_translation = None
        self._drag_pos = None
        self._is_resizing = False
        self._resize_edges = set()
        self._resize_start_pos = None
        self._resize_start_size = None
        self._resize_start_geometry = None
        self._long_text_mode = False
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

        # Some games enter fullscreen after this window is first shown, which
        # can move their render window above an existing TOPMOST window. Keep
        # reasserting the native Z-order while "force topmost" is enabled.
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(self.TOPMOST_ENFORCE_INTERVAL_MS)
        self._topmost_timer.timeout.connect(self._enforce_topmost)

        self._height_adjust_timer = QTimer(self)
        self._height_adjust_timer.setSingleShot(True)
        self._height_adjust_timer.timeout.connect(self._adjust_height)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._showing_waiting_text = True
        self.read_container = QWidget(self)
        self.read_container.setObjectName("readContainer")
        self.read_container.setCursor(Qt.OpenHandCursor)
        read_layout = QHBoxLayout(self.read_container)
        read_layout.setContentsMargins(0, 0, 0, 0)
        read_layout.setSpacing(0)
        self._read_layout = read_layout

        self.label = OutlinedLabel(tr("overlay.waiting"), self.read_container)
        self.label.set_font_family(config.get("font_family", "Microsoft YaHei"))
        self.label.set_font_size(config.get("font_size", 22))
        self.label.set_font_bold(config.get("font_bold", True))
        self.label.set_text_color(config.get("font_color", "#FFFFFF"))
        self.label.setCursor(Qt.OpenHandCursor)
        read_layout.addWidget(self.label, 1)

        self.text_scrollbar = QScrollBar(Qt.Vertical, self.read_container)
        self.text_scrollbar.setCursor(Qt.ArrowCursor)
        self.text_scrollbar.setFixedWidth(16)
        self.text_scrollbar.setStyleSheet(
            """
            QScrollBar:vertical {
                background: rgba(8, 10, 16, 190);
                width: 16px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(210, 220, 235, 150);
                border-radius: 5px;
                min-height: 28px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(235, 240, 250, 195);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )
        self.text_scrollbar.valueChanged.connect(self.label.set_scroll_offset)
        self.text_scrollbar.hide()
        read_layout.addWidget(self.text_scrollbar)
        layout.addWidget(self.read_container)

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
        self.edit_text.setPlaceholderText(tr("overlay.edit_placeholder"))
        editor_layout.addWidget(self.edit_text, 1)

        self.editor_footer = QWidget(self.editor_container)
        self.editor_footer.setObjectName("editorFooter")
        self.editor_footer.setCursor(Qt.OpenHandCursor)
        self.editor_footer.setToolTip(tr("overlay.drag_footer"))
        btn_row = QHBoxLayout(self.editor_footer)
        btn_row.setContentsMargins(0, 0, 34, 0)
        btn_row.setSpacing(6)
        btn_row.addStretch()

        self.btn_cancel_edit = QPushButton(tr("common.cancel"))
        self.btn_cancel_edit.setObjectName("editCancelButton")
        self.btn_cancel_edit.setCursor(Qt.PointingHandCursor)
        self.btn_cancel_edit.clicked.connect(self.cancel_edit)
        btn_row.addWidget(self.btn_cancel_edit)

        self.btn_save_edit = QPushButton(tr("common.save"))
        self.btn_save_edit.setObjectName("editSaveButton")
        self.btn_save_edit.setCursor(Qt.PointingHandCursor)
        self.btn_save_edit.clicked.connect(self._emit_save)
        btn_row.addWidget(self.btn_save_edit)

        self.resize_handle = QLabel("◢", self.editor_container)
        self.resize_handle.setObjectName("resizeHandle")
        self.resize_handle.setAlignment(Qt.AlignCenter)
        self.resize_handle.setCursor(Qt.SizeFDiagCursor)
        self.resize_handle.setToolTip(tr("overlay.resize_tip"))
        self.resize_handle.setFixedSize(26, 26)

        editor_layout.addWidget(self.editor_footer)

        self.read_container.installEventFilter(self)
        self.editor_container.installEventFilter(self)
        self.editor_footer.installEventFilter(self)
        self.resize_handle.installEventFilter(self)
        self._apply_editor_fonts()
        self._position_resize_handle()

        self.editor_container.hide()
        layout.addWidget(self.editor_container)

        self._read_resize_handles = {}
        handle_cursors = {
            "left": Qt.SizeHorCursor,
            "right": Qt.SizeHorCursor,
            "top": Qt.SizeVerCursor,
            "bottom": Qt.SizeVerCursor,
            "top_left": Qt.SizeFDiagCursor,
            "top_right": Qt.SizeBDiagCursor,
            "bottom_left": Qt.SizeBDiagCursor,
            "bottom_right": Qt.SizeFDiagCursor,
        }
        for name, cursor in handle_cursors.items():
            handle = QWidget(self)
            handle.setCursor(cursor)
            handle.setMouseTracking(True)
            handle.installEventFilter(self)
            handle.hide()
            self._read_resize_handles[name] = handle

        self.setGeometry(
            config.get("overlay_x", 560),
            config.get("overlay_y", 800),
            config.get("overlay_width", 800),
            80,
        )

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        i18n_manager().language_changed.connect(self.retranslate_ui)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_topmost_timer()
        QTimer.singleShot(0, self._enforce_topmost)
        self.visibility_changed.emit(True)

    def retranslate_ui(self, *_):
        self.edit_text.setPlaceholderText(tr("overlay.edit_placeholder"))
        self.editor_footer.setToolTip(tr("overlay.drag_footer"))
        self.btn_cancel_edit.setText(tr("common.cancel"))
        self.btn_save_edit.setText(tr("common.save"))
        self.resize_handle.setToolTip(tr("overlay.resize_tip"))
        if self._showing_waiting_text:
            self.label.setText(tr("overlay.waiting"))

    def hideEvent(self, event):
        self._topmost_timer.stop()
        self._clear_pointer_interaction()
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def _save_config(self):
        from config import save_config

        save_config(self.config)
        self.config_updated.emit(self.config)

    def _screen_limits(self) -> tuple[int, int]:
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
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

    def _position_read_resize_handles(self):
        if not hasattr(self, "_read_resize_handles"):
            return
        visible = self._long_text_mode and not self.editor_container.isVisible()
        if not visible:
            for handle in self._read_resize_handles.values():
                handle.hide()
            return

        edge = self.READ_RESIZE_HANDLE_SIZE
        corner = self.READ_RESIZE_CORNER_SIZE
        width = self.width()
        height = self.height()
        geometries = {
            "left": (0, corner, edge, max(1, height - corner * 2)),
            "right": (max(0, width - edge), corner, edge, max(1, height - corner * 2)),
            "top": (corner, 0, max(1, width - corner * 2), edge),
            "bottom": (corner, max(0, height - edge), max(1, width - corner * 2), edge),
            "top_left": (0, 0, corner, corner),
            "top_right": (max(0, width - corner), 0, corner, corner),
            "bottom_left": (0, max(0, height - corner), corner, corner),
            "bottom_right": (max(0, width - corner), max(0, height - corner), corner, corner),
        }
        for name, handle in self._read_resize_handles.items():
            handle.setGeometry(*geometries[name])
            handle.show()
            handle.raise_()

    def resizeEvent(self, event):
        self._position_read_resize_handles()
        self._position_resize_handle()
        super().resizeEvent(event)

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
        if not self._editing_target:
            self.config["overlay_edit_width"] = self.width()
        self._editing_target = dict(target)
        base_text = str(target.get("translation") or "")
        self._set_edit_dirty_state(False, base_text=base_text)
        self._set_edit_text_programmatically(base_text)
        self.read_container.hide()
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
        self.read_container.show()
        self.label.show()
        if self._pending_text is not None:
            self.text_scrollbar.setValue(0)
            self.label.set_scroll_offset(0)
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

    def _apply_read_window_size(self, width: int, height: int):
        """Refresh nested layout constraints before shrinking the top-level window."""
        self._read_layout.invalidate()
        root_layout = self.layout()
        root_layout.invalidate()
        self._read_layout.activate()
        root_layout.activate()
        self.resize(width, height)
        root_layout.activate()
        self._read_layout.activate()

    def _restore_edit_window(self):
        width = self.config.get("overlay_edit_width", self.width())
        height = self.config.get("overlay_edit_height", 300)
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
                if self._long_text_mode:
                    self.config["overlay_long_height"] = self.read_container.height()
        self._save_config()

    def _set_read_drag_cursor(self, cursor):
        """Update the draggable body without coupling it to edge hovering."""
        self.setCursor(cursor)
        self.read_container.setCursor(cursor)
        self.label.setCursor(cursor)

    def _clear_pointer_interaction(self):
        """End any drag/resize whose release event may have been lost."""
        self._drag_pos = None
        self._is_resizing = False
        self._resize_edges = set()
        self._resize_start_pos = None
        self._resize_start_size = None
        self._resize_start_geometry = None
        self.label.set_paint_frozen(False)
        self._set_read_drag_cursor(Qt.OpenHandCursor)
        self.editor_footer.setCursor(Qt.OpenHandCursor)

    def eventFilter(self, watched, event):
        if watched in getattr(self, "_read_resize_handles", {}).values():
            return self._handle_read_resize_event(watched, event)
        if watched is self.read_container and event.type() == QEvent.Wheel:
            return self._scroll_long_text_with_wheel(event)
        if watched is self.editor_container and event.type() == QEvent.Resize:
            self._position_resize_handle()
            return False
        if watched is self.editor_footer:
            return self._handle_editor_footer_event(event)
        if watched is self.resize_handle:
            return self._handle_editor_resize_event(event)
        return super().eventFilter(watched, event)

    def _scroll_long_text_with_wheel(self, event) -> bool:
        if not self._long_text_mode or self.text_scrollbar.maximum() <= 0:
            return False

        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            delta = -pixel_delta
        else:
            angle_delta = event.angleDelta().y()
            if not angle_delta:
                return False
            lines = angle_delta / 120.0
            delta = int(-lines * self.text_scrollbar.singleStep() * 3)

        self.text_scrollbar.setValue(self.text_scrollbar.value() + delta)
        event.accept()
        return True

    def wheelEvent(self, event):
        if self._scroll_long_text_with_wheel(event):
            return
        super().wheelEvent(event)

    def _handle_read_resize_event(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            name = next(
                key for key, handle in self._read_resize_handles.items() if handle is watched
            )
            self._height_adjust_timer.stop()
            self._resize_edges = set(name.split("_"))
            self._is_resizing = True
            self._resize_start_pos = event.globalPos()
            self._resize_start_geometry = self.geometry()
            self.label.set_paint_frozen(False)
            return True
        if event.type() == QEvent.MouseMove and self._is_resizing:
            if event.buttons() & Qt.LeftButton:
                self._resize_read_window(event.globalPos())
            else:
                self._clear_pointer_interaction()
                self._adjust_height()
                self._persist_window_geometry(include_size=True)
            return True
        if event.type() == QEvent.MouseButtonRelease and self._is_resizing:
            self._clear_pointer_interaction()
            self._adjust_height()
            self._persist_window_geometry(include_size=True)
            return True
        return False

    def _handle_editor_footer_event(self, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self.editor_footer.setCursor(Qt.ClosedHandCursor)
            return True
        if event.type() == QEvent.MouseMove:
            if self._drag_pos is not None:
                if event.buttons() & Qt.LeftButton:
                    self.move(event.globalPos() - self._drag_pos)
                    self.config["overlay_x"] = self.x()
                    self.config["overlay_y"] = self.y()
                    return True
                self._clear_pointer_interaction()
                self._persist_window_geometry()
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
        if event.type() == QEvent.MouseMove and self._is_resizing:
            if event.buttons() & Qt.LeftButton:
                delta = event.globalPos() - self._resize_start_pos
                width, height = self._clamp_edit_size(
                    self._resize_start_size.width() + delta.x(),
                    self._resize_start_size.height() + delta.y(),
                )
                self.resize(width, height)
                self.config["overlay_edit_width"] = width
                self.config["overlay_edit_height"] = height
                return True
            self._clear_pointer_interaction()
            self._persist_window_geometry(include_size=True)
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

    def _sync_topmost_timer(self):
        should_run = self.isVisible() and self.config.get("force_topmost", False)
        if should_run:
            if not self._topmost_timer.isActive():
                self._topmost_timer.start()
        else:
            self._topmost_timer.stop()

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
        self._sync_topmost_timer()
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
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n(?:[ \t]*\n)+", "\n", text)
        if self._editing_target:
            self._pending_text = text
            return
        self._pending_text = None
        self._showing_waiting_text = False
        self.text_scrollbar.setValue(0)
        self.label.set_scroll_offset(0)
        self.label.setText(text)
        self._height_adjust_timer.stop()
        self._adjust_height()
        self._enforce_topmost()

    def set_screen_text_mode(self, enabled: bool):
        self.label.set_screen_text_mode(enabled)

    def _original_text_for_clipboard(self) -> str:
        dialogue_target = self._edit_context.get("dialogue") or {}
        choice_targets = self._edit_context.get("choices", [])
        original = str(dialogue_target.get("source") or "")
        choices = [str(target.get("source") or "") for target in choice_targets]

        first_is_caption = bool(
            original
            and choices
            and choices[0].strip() == original.strip()
        )
        lines = []
        if first_is_caption:
            lines.append(original)
        elif original:
            speaker = str(dialogue_target.get("speaker") or "")
            if speaker and self.config.get("show_character_name", True):
                original = f"【{speaker}】{original}"
            lines.append(original)

        choice_start_index = 1 if first_is_caption else 0
        for index in range(choice_start_index, len(choices)):
            lines.append(f"[{index + 1 - choice_start_index}] {choices[index]}")

        return "\n".join(lines)

    def auto_copy_display(self, text: str, translation_ready: bool):
        original = self._original_text_for_clipboard()
        if original != self._auto_copy_original:
            self._auto_copy_original = original
            self._auto_copy_translation = None
            if original and self.config.get("auto_copy_original", False):
                QApplication.clipboard().setText(original)
        if not translation_ready or not original or not text:
            return
        if text != self._auto_copy_translation:
            self._auto_copy_translation = text
            if self.config.get("auto_copy_translation", False):
                QApplication.clipboard().setText(text)

    def _set_auto_copy(self, key: str, checked: bool):
        self.config[key] = checked
        self._save_config()

    def _add_copy_menu_row(self, menu, title, key, copy_text, button_width):
        # Resolve inherited menu attributes explicitly so Qt's class-specific
        # button/checkbox defaults cannot replace them during style polishing.
        menu_font = menu.font()
        row_font = QFont(menu_font.family(), 10, menu_font.weight(), menu_font.italic())
        if menu_font.pixelSize() > 0:
            row_font.setPixelSize(menu_font.pixelSize())
        else:
            row_font.setPointSizeF(menu_font.pointSizeF())
        action = QWidgetAction(menu)
        action.setText(title)
        row = QWidget(menu)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 12, 0)
        button = QPushButton(title, row)
        button.setFont(row_font)
        button.setFixedWidth(button_width)
        button.setStyleSheet(
            "QPushButton { color: white; background: transparent; border: none; "
            "text-align: left; padding: 5px 20px; }"
            "QPushButton:hover, QPushButton:focus { background: #4a9eff; }"
        )
        checkbox = AutoCopyCheckBox(tr("overlay.auto_copy"), row)
        checkbox.setFont(row_font)
        checkbox.setStyleSheet("QCheckBox { color: white; spacing: 6px; }")
        checkbox.setChecked(self.config.get(key, False))
        checkbox.toggled.connect(lambda checked: self._set_auto_copy(key, checked))
        action.triggered.connect(lambda: QApplication.clipboard().setText(copy_text()))
        button.clicked.connect(action.trigger)
        button.clicked.connect(menu.close)
        layout.addWidget(button)
        layout.addWidget(checkbox)
        action.setDefaultWidget(row)
        menu.addAction(action)

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
        self._clear_pointer_interaction()
        primary_screen = QApplication.primaryScreen()
        if primary_screen:
            screen = primary_screen.geometry()
            screen_w = screen.width()
            screen_h = screen.height()
            width = self.config.get("overlay_width", 800)
            x = (screen_w - width) // 2
            y = int(screen_h * 0.70)
        else:
            x, y = 560, 800

        self.move(x, y)
        self._persist_window_geometry()

    def _adjust_height(self):
        if self.editor_container.isVisible():
            self._restore_edit_window()
            return
        width = self._clamp_overlay_width(self.config.get("overlay_width", self.width()))
        self.config["overlay_width"] = width
        self.resize(width, self.height())

        outer_margins = self.READ_OUTER_MARGIN * 2
        scrollbar_width = self.text_scrollbar.width()
        self._read_layout.setContentsMargins(0, 0, 0, 0)
        self.label.resize(max(1, width - outer_margins), self.label.height())
        _, content_height = self.label._get_text_layout()
        _, screen_height = self._screen_limits()
        # Once this content needs a panel, resizing must not remove it.
        text = self.label.text()
        same_text = text == getattr(self, "_panel_text", None)
        long_text = (same_text and self._long_text_mode) or (
            content_height > int(screen_height * self.LONG_TEXT_THRESHOLD_RATIO)
        )
        self._panel_text = text

        self._long_text_mode = long_text
        self.label.set_long_text_mode(long_text)
        if long_text:
            padding = self.LONG_TEXT_PANEL_PADDING
            self.read_container.setStyleSheet(
                "QWidget#readContainer {"
                " background-color: rgba(8, 10, 16, 190);"
                " border: 1px solid rgba(115, 128, 150, 120);"
                " border-radius: 8px;"
                "}"
            )
            self._read_layout.setContentsMargins(padding, padding, padding, padding)
            self.text_scrollbar.show()
            self.label.resize(
                max(1, width - outer_margins - scrollbar_width - padding * 2),
                self.label.height(),
            )
            _, content_height = self.label._get_text_layout()
            configured_height = int(self.config.get("overlay_long_height", 0) or 0)
            panel_height = configured_height or int(screen_height * self.LONG_TEXT_HEIGHT_RATIO)
            panel_height = max(
                self.LONG_TEXT_MIN_HEIGHT,
                min(panel_height, screen_height - 24),
            )
            visible_height = max(1, panel_height - padding * 2)
            self.label.setFixedHeight(visible_height)
            self.text_scrollbar.setPageStep(visible_height)
            self.text_scrollbar.setRange(0, max(0, content_height - visible_height))
            self.text_scrollbar.setSingleStep(max(20, self.label.fontMetrics().height()))
            self._apply_read_window_size(width, panel_height + outer_margins)
        else:
            self.read_container.setStyleSheet(
                "QWidget#readContainer { background: transparent; border: none; }"
            )
            self.text_scrollbar.hide()
            self.text_scrollbar.setRange(0, 0)
            self.label.set_scroll_offset(0)
            self.label.setFixedHeight(content_height)
            self._apply_read_window_size(
                width,
                max(content_height + outer_margins, 40),
            )
        self._position_read_resize_handles()

    def _resize_edges_at(self, pos) -> set[str]:
        edges = set()
        if pos.x() <= self.RESIZE_HOTZONE:
            edges.add("left")
        elif pos.x() >= self.width() - self.RESIZE_HOTZONE:
            edges.add("right")
        if self._long_text_mode:
            if pos.y() <= self.RESIZE_HOTZONE:
                edges.add("top")
            elif pos.y() >= self.height() - self.RESIZE_HOTZONE:
                edges.add("bottom")
        return edges

    @staticmethod
    def _cursor_for_edges(edges: set[str]):
        if ("left" in edges and "top" in edges) or ("right" in edges and "bottom" in edges):
            return Qt.SizeFDiagCursor
        if ("right" in edges and "top" in edges) or ("left" in edges and "bottom" in edges):
            return Qt.SizeBDiagCursor
        if "left" in edges or "right" in edges:
            return Qt.SizeHorCursor
        if "top" in edges or "bottom" in edges:
            return Qt.SizeVerCursor
        return Qt.OpenHandCursor

    def _resize_read_window(self, global_pos):
        start = self._resize_start_geometry
        if start is None:
            return
        delta = global_pos - self._resize_start_pos
        left = start.left()
        top = start.top()
        right = start.right() + 1
        bottom = start.bottom() + 1

        if "left" in self._resize_edges:
            left += delta.x()
        if "right" in self._resize_edges:
            right += delta.x()
        if "top" in self._resize_edges:
            top += delta.y()
        if "bottom" in self._resize_edges:
            bottom += delta.y()

        min_width = self.READ_MIN_WIDTH
        if right - left < min_width:
            if "left" in self._resize_edges:
                left = right - min_width
            else:
                right = left + min_width

        if self._long_text_mode:
            outer_margins = self.READ_OUTER_MARGIN * 2
            min_window_height = self.LONG_TEXT_MIN_HEIGHT + outer_margins
            if bottom - top < min_window_height:
                if "top" in self._resize_edges:
                    top = bottom - min_window_height
                else:
                    bottom = top + min_window_height

        width = self._clamp_overlay_width(right - left)
        if "left" in self._resize_edges:
            left = right - width
        height = bottom - top
        self.setGeometry(left, top, width, height)
        self.config["overlay_width"] = width

        if self._long_text_mode:
            outer_margins = self.READ_OUTER_MARGIN * 2
            padding = self.LONG_TEXT_PANEL_PADDING
            panel_height = max(self.LONG_TEXT_MIN_HEIGHT, height - outer_margins)
            visible_height = max(1, panel_height - padding * 2)
            self.label.setFixedHeight(visible_height)
            self.config["overlay_long_height"] = panel_height
            self.layout().activate()
            self._read_layout.activate()
            _, content_height = self.label._get_text_layout()
            self.text_scrollbar.setPageStep(visible_height)
            self.text_scrollbar.setRange(0, max(0, content_height - visible_height))

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
            self._height_adjust_timer.stop()
            self._resize_edges = self._resize_edges_at(event.pos())
            if self._resize_edges:
                self._is_resizing = True
                self._resize_start_pos = event.globalPos()
                self._resize_start_geometry = self.geometry()
                self.label.set_paint_frozen(False)
                self.setCursor(self._cursor_for_edges(self._resize_edges))
            else:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                self._set_read_drag_cursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.editor_container.isVisible():
            return super().mouseDoubleClickEvent(event)
        if event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)
        if self._resize_edges_at(event.pos()):
            return super().mouseDoubleClickEvent(event)

        # 双击正文时直接进入当前对白的编辑模式，不影响右侧拖拽调宽热区。
        target = self._edit_context.get("dialogue")
        if not target:
            return super().mouseDoubleClickEvent(event)

        self._drag_pos = None
        self._is_resizing = False
        self._set_read_drag_cursor(Qt.OpenHandCursor)
        self.start_edit(target)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.editor_container.isVisible():
            return super().mouseMoveEvent(event)
        if (self._drag_pos is not None or self._is_resizing) and not event.buttons() & Qt.LeftButton:
            was_resizing = self._is_resizing
            self._clear_pointer_interaction()
            if was_resizing:
                self._adjust_height()
            self._persist_window_geometry(include_size=was_resizing)
            event.accept()
            return
        if self._is_resizing:
            self._resize_read_window(event.globalPos())
        elif self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            self.config["overlay_x"] = self.x()
            self.config["overlay_y"] = self.y()
        else:
            # Resize handles and the outer window use resize cursors. The
            # read_container keeps its own OpenHandCursor, so entering the body
            # immediately restores the drag affordance without a click.
            self.setCursor(self._cursor_for_edges(self._resize_edges_at(event.pos())))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.editor_container.isVisible():
            return super().mouseReleaseEvent(event)
        changed = self._drag_pos is not None or self._is_resizing
        was_resizing = self._is_resizing
        self._clear_pointer_interaction()
        if changed:
            if was_resizing:
                self._adjust_height()
            self._persist_window_geometry(include_size=was_resizing)
        event.accept()

    def _show_context_menu(self, pos):
        self._clear_pointer_interaction()
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
            cancel_action = menu.addAction(tr("overlay.cancel_edit"))
            cancel_action.triggered.connect(self.cancel_edit)
            self._exec_context_menu(menu, pos)
            return

        # Windows resolves the system menu font during polishing, which may
        # differ from the application font returned by a newly created QMenu.
        menu.ensurePolished()
        copy_rows = (
            (tr("overlay.copy_translation"), "auto_copy_translation", self.label.text),
            (tr("overlay.copy_original"), "auto_copy_original", self._original_text_for_clipboard),
        )
        button_width = max(menu.fontMetrics().horizontalAdvance(title) for title, _, _ in copy_rows) + 40
        for title, key, copy_text in copy_rows:
            self._add_copy_menu_row(menu, title, key, copy_text, button_width)

        if self._edit_context.get("dialogue"):
            menu.addSeparator()
            edit_dialogue_action = menu.addAction(tr("overlay.edit_dialogue"))
            edit_dialogue_action.triggered.connect(
                lambda checked=False, target=self._edit_context["dialogue"]: self.start_edit(target)
            )

        choice_targets = self._edit_context.get("choices", [])
        if choice_targets:
            choice_menu = menu.addMenu(tr("overlay.edit_choices"))
            for target in choice_targets:
                title = str(target.get("menu_label") or tr("overlay.choice")).strip()
                action = choice_menu.addAction(title)
                action.triggered.connect(
                    lambda checked=False, item=target: self.start_edit(item)
                )

        menu.addSeparator()
        workbench_action = menu.addAction(tr("overlay.show_workbench"))
        workbench_action.triggered.connect(self.show_workbench_requested.emit)

        size_menu = menu.addMenu(tr("overlay.font_size"))
        for size in [16, 18, 20, 22, 24, 28, 32, 36, 40]:
            act = size_menu.addAction(f"{size}px" + (" ✓" if size == self.config.get("font_size") else ""))
            act.triggered.connect(lambda checked=False, value=size: self.set_font_size(value))

        family_menu = menu.addMenu(tr("overlay.font"))
        families = [
            ("Microsoft YaHei", "Microsoft YaHei"),
            ("DengXian", "DengXian"),
            ("SimHei", "SimHei"),
            ("SimSun", "SimSun"),
            ("KaiTi", "KaiTi"),
            ("FangSong", "FangSong"),
        ]
        current_family = self.config.get("font_family", "Microsoft YaHei")
        for name, family in families:
            act = family_menu.addAction(name + (" ✓" if family == current_family else ""))
            act.triggered.connect(lambda checked=False, value=family: self.set_font_family(value))

        family_menu.addSeparator()
        bold_action = family_menu.addAction(tr("overlay.bold"))
        bold_action.setCheckable(True)
        bold_action.setChecked(self.config.get("font_bold", True))
        bold_action.triggered.connect(self.set_font_bold)

        color_menu = menu.addMenu(tr("overlay.font_color"))
        colors = [
            (tr("color.white"), "#FFFFFF"),
            (tr("color.gray"), "#AAAAAA"),
            (tr("color.red"), "#FF5555"),
            (tr("color.green"), "#32CD32"),
            (tr("color.yellow"), "#FFD700"),
            (tr("color.blue"), "#55A0FF"),
            (tr("color.pink"), "#FF80DF"),
        ]
        current_color = self.config.get("font_color", "#FFFFFF")
        for name, hex_val in colors:
            act = color_menu.addAction(name + (" ✓" if hex_val == current_color else ""))
            act.triggered.connect(lambda checked=False, value=hex_val: self.set_text_color(value))

        width_menu = menu.addMenu(tr("overlay.text_width"))
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

        show_name_action = QAction(tr("overlay.show_speaker"), self)
        show_name_action.setCheckable(True)
        show_name_action.setChecked(self.config.get("show_character_name", True))
        show_name_action.triggered.connect(self._toggle_show_name)
        menu.addAction(show_name_action)

        force_topmost_action = QAction(tr("overlay.force_topmost"), self)
        force_topmost_action.setCheckable(True)
        force_topmost_action.setChecked(self.config.get("force_topmost", True))
        force_topmost_action.triggered.connect(self._toggle_force_topmost)
        menu.addAction(force_topmost_action)

        menu.addSeparator()
        quit_action = menu.addAction(tr("overlay.hide"))
        quit_action.triggered.connect(self.hide)

        self._exec_context_menu(menu, pos)

    def _exec_context_menu(self, menu: QMenu, pos):
        # QMenu runs a nested event loop, so the regular topmost timer keeps
        # firing while the menu is open. Reasserting the overlay's Z-order at
        # that point places the overlay itself above its popup menu.
        self._topmost_timer.stop()
        try:
            menu.exec_(self.mapToGlobal(pos))
        finally:
            self._sync_topmost_timer()
            self._enforce_topmost()

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
        self._sync_topmost_timer()
        self._enforce_topmost()
        self._save_config()
