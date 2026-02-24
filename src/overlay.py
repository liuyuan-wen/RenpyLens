# -*- coding: utf-8 -*-
"""翻译弹窗 - 无背景、白字黑描边、可拖拽、置顶、可调字号和宽度"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication, QMenu, QAction
from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtGui import QFont, QPainter, QPainterPath, QColor, QCursor, QFontMetrics


class OutlinedLabel(QLabel):
    """带黑色描边的白色文字 Label"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._outline_width = 2
        self._font_size = 22
        self._text_color = QColor(255, 255, 255)
        self._outline_color = QColor(0, 0, 0)
        self.setFont(QFont("Microsoft YaHei", self._font_size, QFont.Bold))
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

    def set_font_size(self, size: int):
        self._font_size = size
        self.setFont(QFont("Microsoft YaHei", size, QFont.Bold))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        font = self.font()
        painter.setFont(font)

        text = self.text()
        if not text:
            return

        # 使用实际控件宽度作为换行基准（而不是 contentsRect）
        available_width = self.width() - self._outline_width * 4

        # 使用 QPainterPath 绘制描边文字
        path = QPainterPath()

        # 处理自动换行
        fm = QFontMetrics(font)
        lines = []
        current_line = ""
        for char in text:
            if char == '\n':
                lines.append(current_line)
                current_line = ""
                continue
            test = current_line + char
            if fm.horizontalAdvance(test) > available_width:
                if current_line:
                    lines.append(current_line)
                current_line = char
            else:
                current_line = test
        if current_line:
            lines.append(current_line)

        y_offset = fm.ascent() + self._outline_width
        for line in lines:
            path.addText(
                self._outline_width * 2,
                y_offset,
                font,
                line,
            )
            y_offset += fm.height()

        # 画描边（黑色粗边）
        from PyQt5.QtGui import QPen
        painter.setPen(QPen(self._outline_color, self._outline_width * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 画填充（白色）
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._text_color)
        painter.drawPath(path)

        painter.end()

        # 自动调整高度（允许缩小）
        needed_height = int(y_offset + self._outline_width * 2)
        self.setFixedHeight(needed_height)


class TranslationOverlay(QWidget):
    """翻译弹窗主窗口 - 透明背景、置顶、可拖拽"""

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._drag_pos = None
        self._is_resizing = False

        # 窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不在任务栏显示
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # 不抢夺焦点

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.label = OutlinedLabel("等待游戏文本...", self)
        self.label.set_font_size(config.get("font_size", 22))
        layout.addWidget(self.label)

        # 初始位置和大小
        self.setGeometry(
            config.get("overlay_x", 100),
            config.get("overlay_y", 100),
            config.get("overlay_width", 800),
            80,
        )

        # 右键菜单
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_text(self, text: str):
        self.label.setText(text)
        # 延迟调整高度，等 paintEvent 先执行
        QTimer.singleShot(20, self._adjust_height)

    def _adjust_height(self):
        needed = self.label.height() + 12
        self.resize(self.width(), max(needed, 40))

    def set_font_size(self, size: int):
        self.config["font_size"] = size
        self.label.set_font_size(size)
        self._adjust_height()

    # --- 拖拽 ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 检查是否在右边缘（调整宽度区域）
            if event.pos().x() > self.width() - 15:
                self._is_resizing = True
            else:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            new_width = event.globalPos().x() - self.x()
            if new_width > 200:
                self.resize(new_width, self.height())
                self.config["overlay_width"] = new_width
        elif self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            self.config["overlay_x"] = self.x()
            self.config["overlay_y"] = self.y()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._is_resizing = False
        event.accept()

    def enterEvent(self, event):
        # 靠近右边缘时改变光标
        pass

    def _update_cursor(self, pos):
        if pos.x() > self.width() - 15:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    # --- 右键菜单 ---
    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #555;
                padding: 5px;
            }
            QMenu::item:selected {
                background-color: #4a9eff;
            }
        """)

        # 复制文本
        copy_action = menu.addAction("复制文本")
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(self.label.text()))

        # 字号选项
        size_menu = menu.addMenu("字体大小")
        for s in [16, 18, 20, 22, 24, 28, 32, 36, 40]:
            act = size_menu.addAction(f"{s}px" + (" ✓" if s == self.config.get("font_size") else ""))
            act.setData(s)
            act.triggered.connect(lambda checked, sz=s: self.set_font_size(sz))

        # 宽度选项
        width_menu = menu.addMenu("文本框宽度")
        for w in [400, 600, 800, 1000, 1200, 1600]:
            act = width_menu.addAction(f"{w}px" + (" ✓" if w == self.width() else ""))
            act.setData(w)
            act.triggered.connect(lambda checked, ww=w: self._set_width(ww))

        menu.addSeparator()

        quit_action = menu.addAction("关闭弹窗")
        quit_action.triggered.connect(self.hide)

        menu.exec_(self.mapToGlobal(pos))

    def _set_width(self, width: int):
        """设置宽度并触发重绘"""
        self.config["overlay_width"] = width
        self.resize(width, self.height())
        # 强制重绘 label 以使用新宽度
        self.label.resize(width - 8, self.label.height())
        self.label.update()
        QTimer.singleShot(20, self._adjust_height)
