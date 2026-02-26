# -*- coding: utf-8 -*-
"""翻译弹窗 - 无背景、白字黑描边、可拖拽、置顶、可调字号和宽度"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication, QMenu, QAction
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QFont, QPainter, QPainterPath, QColor, QFontMetrics
)
import win32gui
import win32con


class OutlinedLabel(QLabel):
    """带黑色描边的白色文字 Label"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._outline_width = 2
        self._font_size = 22
        self._text_color = QColor(255, 255, 255)
        self._outline_color = QColor(0, 0, 0)
        self.setFont(QFont("Microsoft YaHei", self._font_size, QFont.Bold))
        # QLabel 本身也支持富文本，不过我们要手动接管绘制
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents) # 允许事件穿透到父窗口以便拖拽
        self.setStyleSheet("background: transparent;")

    def set_font_size(self, size: int):
        self._font_size = size
        self.setFont(QFont("Microsoft YaHei", size, QFont.Bold))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        text = self.text()
        if not text:
            return

        # 简单清理一下可能的其他 html 变种
        text = text.replace("<b>", "").replace("</b>", "")
        text = text.replace("<div style='font-weight: 900;'>", "").replace("</div>", "")

        import re
        tokens = re.split(r'(</i>|<i>)', text)
        char_list = []
        is_italic = False
        for token in tokens:
            if token == '<i>':
                is_italic = True
            elif token == '</i>':
                is_italic = False
            elif token:
                for char in token:
                    char_list.append((char, is_italic))

        font_normal = self.font()
        fm_normal = QFontMetrics(font_normal)

        available_width = self.width() - self._outline_width * 4

        # 处理自动换行
        lines = []
        current_line = []
        current_line_width = 0

        for char, italic in char_list:
            if char == '\n':
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

        # 构建 Path
        path = QPainterPath()
        y_offset = fm_normal.ascent() + self._outline_width

        from PyQt5.QtGui import QTransform
        
        for line in lines:
            x_offset = self._outline_width * 2
            
            # 合并相同样式的连续字符，避免每个字 addText 导致路径碎片化
            merged_chunks = []
            for char, italic in line:
                if not merged_chunks:
                    merged_chunks.append([char, italic])
                else:
                    if merged_chunks[-1][1] == italic:
                        merged_chunks[-1][0] += char
                    else:
                        merged_chunks.append([char, italic])
            
            for text_chunk, italic in merged_chunks:
                f = font_normal
                fm = fm_normal
                
                sub_path = QPainterPath()
                sub_path.addText(0, 0, f, text_chunk)
                
                if italic:
                    # 强行倾斜原本真正的粗体，防止通过 setItalic 会丢失字重
                    # -0.25 向右上方倾斜，模拟约 14 度的完美斜体
                    transform = QTransform().shear(-0.25, 0.0)
                    sub_path = transform.map(sub_path)
                    
                path.addPath(sub_path.translated(x_offset, y_offset))
                x_offset += fm.horizontalAdvance(text_chunk)
                
            y_offset += fm_normal.height()

        from PyQt5.QtGui import QPen
        # 画描边（黑色粗边）
        painter.setPen(QPen(self._outline_color, self._outline_width * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        # 画填充（白色）
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._text_color)
        painter.drawPath(path)

        painter.end()

        # 自动调整高度
        needed_height = int(y_offset + self._outline_width * 2)
        if hasattr(self, "_last_needed_height") and self._last_needed_height == needed_height:
            pass
        else:
            self._last_needed_height = needed_height
            self.setFixedHeight(needed_height)


class TranslationOverlay(QWidget):
    # 当悬浮窗尺寸或其他配置发生改变时（内部触发），向外发送包含新配置的信号
    config_updated = pyqtSignal(dict)
    
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

    def _enforce_topmost(self):
        """使用 Win32 API 狠狠地把窗口拉到最顶层（仅当开启强力置顶时）"""
        if not self.isVisible() or not self.config.get("force_topmost", False):
            return
        try:
            hwnd = int(self.winId())
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            )
        except Exception as e:
            print(f"[Overlay] Failed to enforce topmost: {e}")

    def update_config(self, new_config: dict):
        """当主界面保存设置时被调用"""
        self.config = new_config
        self.set_font_size(self.config.get("font_size", 22))
        self._set_width(self.config.get("overlay_width", 800))
        self._enforce_topmost()

    def set_text(self, text: str):
        self.label.setText(text)
        self._enforce_topmost()  # 每次更新文本时尝试拉回最顶层
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
        # 获取当前屏幕宽度
        screen = QApplication.desktop().screenGeometry(self)
        screen_width = screen.width()
        
        for pct in [30, 40, 50, 60, 80, 100]:
            target_w = int(screen_width * pct / 100)
            # 判断当前宽度是否匹配该百分比(容差 10px)
            is_checked = abs(self.width() - target_w) < 10
            act = width_menu.addAction(f"{pct}%" + (" ✓" if is_checked else ""))
            act.setData(target_w)
            act.triggered.connect(lambda checked, ww=target_w: self._set_width(ww))

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
        quit_action = menu.addAction("关闭弹窗")
        quit_action.triggered.connect(self.hide)

        menu.exec_(self.mapToGlobal(pos))

    def _set_width(self, width: int):
        """设置宽度并触发重绘"""
        self.setFixedWidth(width)
        # 根据当前文本重新计算高度
        self._adjust_height()
        # 更新配置并保存
        self.config["overlay_width"] = width
        from config import save_config
        save_config(self.config)
        self.config_updated.emit(self.config)

    def _toggle_show_name(self, checked: bool):
        self.config["show_character_name"] = checked
        from config import save_config
        save_config(self.config)
        self.config_updated.emit(self.config)

    def _toggle_force_topmost(self, checked: bool):
        self.config["force_topmost"] = checked
        self._enforce_topmost()
        from config import save_config
        save_config(self.config)
        self.config_updated.emit(self.config)
