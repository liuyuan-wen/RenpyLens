# -*- coding: utf-8 -*-
"""
RenpyLens - Ren'Py 游戏实时翻译弹窗工具
主入口：拖入游戏 EXE → 自动注入 Hook → 启动游戏 → 实时翻译弹窗
"""

import copy
import json
import sys
import os
import re
import socket
import subprocess
import threading
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QMessageBox, QComboBox, QLineEdit, QTextEdit, QFileDialog,
    QStyledItemDelegate, QDialog, QFrame
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QObject
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QIcon, QColor, QPalette, QTextCursor, QPixmap

from config import load_config, save_config
from hwid_utils import get_hwid, register_trial_key, fetch_trial_key_expiry
from hook_server import HookServer
from translator import create_translator, KeyExpiredError
from cache import (
    ENTRY_TYPE_CHOICE,
    ENTRY_TYPE_DIALOGUE,
    TranslationCache,
    normalize_speaker_name,
)
from overlay import TranslationOverlay
from injector import inject_hook, remove_hook, launch_game, is_renpy_game
from settings_dialog import SettingsDialog
from workbench import TranslationWorkbench
from updater import (
    fetch_latest_release,
    is_newer_version,
    download_release_asset,
    launch_windows_updater_script,
)


# 尝试从 ../assets 或 bundling 路径查找 hook script
if getattr(sys, 'frozen', False):
    HOOK_SCRIPT = os.path.join(sys._MEIPASS, "_translator_hook.rpy")
else:
    HOOK_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "_translator_hook.rpy")


class LogStream(QObject):
    """将 print() 输出重定向到 QTextEdit 的流对象"""
    text_written = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._buffer = ""

    def write(self, text):
        if text:
            self.text_written.emit(str(text))

    def flush(self):
        pass


class MainWindow(QWidget):
    """主窗口 - 拖入游戏 EXE 的界面"""

    translation_ready = pyqtSignal(str)  # 翻译结果信号
    _trial_key_signal = pyqtSignal(object)   # 试用 Key 申请结果信号
    _trial_expiry_signal = pyqtSignal(object)  # API 到期时间刷新结果
    _key_expired_signal = pyqtSignal()     # Key 过期信号
    _status_signal = pyqtSignal(str)       # 状态栏更新信号 (用于非 UI 线程更新)
    _update_check_signal = pyqtSignal(object)
    _update_download_signal = pyqtSignal(object)
    _bulk_ui_signal = pyqtSignal(object)
    SUPPORT_QQ_GROUP = "1058127921"

    def __init__(self):
        super().__init__()
        self.config = load_config()

        version = self.config.get("version", "v1.2.0")
        self.setWindowTitle(f"RenpyLens {version} - Ren'Py 实时翻译")
        self.resize(800, 10)
        self.setAcceptDrops(True)

        # 1. 基础状态量初始化 (必须在 _setup_ui 之前，防止信号触发导致 AttributeError)
        self.translator = None
        self.cache = TranslationCache()
        self._prefetch_running = False
        self._latest_prefetch_items = []
        self._is_pinned = self.config.get("window_pinned", False)
        self._inflight_texts = set()
        self._inflight_lock = threading.Lock()
        self._text_generation = 0
        self._current_game_exe = None
        self._game_process = None
        self._hook_installed = False
        self._translator_lock = threading.RLock() # 保护翻译器实例的切换与访问
        self._update_checking = False
        self._update_checking = False
        self._workbench_limit = 100
        self._update_downloading = False
        self._bulk_job_lock = threading.RLock()
        self._bulk_job = self._new_bulk_job_state()
        self._hook_ready_event = threading.Event()
        self._hook_session_ready = False
        self._workbench_refresh_pending_source = ""

        # 2. UI 组件初始化
        self._setup_ui()
        self._setup_log_redirect()

        # 3. 核心服务启动
        self._setup_services()

        self.translation_ready.connect(self._on_translation_ready)
        self._trial_expiry_signal.connect(self._on_trial_expiry_result)
        self._key_expired_signal.connect(self._on_key_expired)
        self._status_signal.connect(self.status_label.setText)
        self._update_check_signal.connect(self._on_update_check_result)
        self._update_download_signal.connect(self._on_update_download_result)
        self._bulk_ui_signal.connect(self._on_bulk_ui_event)
        self._key_expired_shown = False  # 防止重复弹窗

        # 游戏进程监控定时器
        self.game_timer = QTimer(self)
        self.game_timer.timeout.connect(self._check_game_status)
        self._workbench_refresh_timer = QTimer(self)
        self._workbench_refresh_timer.setSingleShot(True)
        self._workbench_refresh_timer.timeout.connect(self._flush_workbench_refresh)
        self._bulk_reset_timer = QTimer(self)
        self._bulk_reset_timer.setSingleShot(True)
        self._bulk_reset_timer.timeout.connect(self._reset_bulk_job_if_final)
        self._bulk_reset_job_id = ""

        # 启动时应用置顶状态
        if self._is_pinned:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            
        self._center_window()
        QTimer.singleShot(1500, self._auto_check_updates)

    def _new_bulk_job_state(self) -> dict:
        return {
            "job_id": "",
            "state": "idle",
            "total_texts": 0,
            "covered_count": 0,
            "scan_entries": {},
            "pending_entries": [],
            "cancel_requested": False,
            "last_request_time": 0.0,
            "error": "",
            "stage_message": "",
            "result_message": "",
        }

    def _reset_hook_session_state(self):
        self._hook_session_ready = False
        self._hook_ready_event.clear()

    def _rebuild_translator(self, clear_cache: bool = False):
        engine = self.config.get("translation_engine", "builtin")
        with self._translator_lock:
            old_translator = self.translator
            if old_translator:
                try:
                    old_translator.close()
                except Exception:
                    pass
            self.translator = create_translator(engine, self.config)
        if clear_cache:
            self.cache.clear()
            self.btn_clear_cache.setEnabled(False)
            self._refresh_workbench_entries()
        else:
            self.btn_clear_cache.setEnabled(not self.cache.is_empty())

    def _set_translation_controls_enabled(self, enabled: bool):
        self.engine_combo.setEnabled(enabled)
        self.model_combo.setEnabled(enabled)
        if enabled:
            self._update_url_visibility()
            is_builtin = self.config.get("translation_engine", "builtin") == "builtin"
            self.btn_refresh_expiry.setEnabled(
                is_builtin and bool(self.config.get("builtin_api_key", "").strip())
            )
        else:
            self.btn_refresh_expiry.setEnabled(False)

    def _center_window(self):
        self.adjustSize()
        screen = QApplication.desktop().availableGeometry()
        size = self.size()
        self.move((screen.width() - size.width()) // 2,
                  (screen.height() - size.height()) // 2)

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a2e;
                color: #eee;
                font-family: "Microsoft YaHei", "Segoe UI";
                font-size: 20px;
            }
            QLabel#title {
                font-size: 36px;
                font-weight: bold;
                color: #e94560;
            }
            QLabel#drop_zone {
                font-size: 24px;
                color: #aaa;
                border: 2px dashed #555;
                border-radius: 12px;
                padding: 5px 20px;
                background-color: #16213e;
            }
            QLabel#drop_zone:hover {
                border-color: #4a9eff;
                color: #ccc;
            }
            QLabel#status {
                font-size: 18px;
                color: #4a9eff;
            }
            QPushButton {
                background-color: #e94560;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff6b81;
            }
            QPushButton:disabled {
                background-color: #444;
                color: #888;
            }
            QToolTip {
                font-size: 18px;
                color: #eee;
                background-color: #2a2a3e;
                border: 1px solid #555;
                padding: 4px 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 0, 20, 14)    # (Left, Top, Right, Bottom)

        # 工具栏（设置按钮在左，标题居中，置顶按钮在右）
        toolbar = QHBoxLayout()
        _toolbar_btn_style = """
            QPushButton {
                background-color: transparent; color: #888;
                border: 1px solid #444; border-radius: 4px;
                padding: 8px 18px; font-size: 22px; font-weight: normal;
            }
            QPushButton:hover { color: #ccc; border-color: #666; }
            QPushButton:checked { color: #4a9eff; border-color: #4a9eff; }
        """
        self.btn_settings = QPushButton("⚙️ 设置")
        self.btn_settings.setStyleSheet(_toolbar_btn_style)
        self.btn_settings.clicked.connect(self._on_settings)
        toolbar.addWidget(self.btn_settings)
        toolbar.addStretch()        
        #title = QLabel("""<span style="font-size: 48px;">🎮</span> RenpyLens""")
        # 尝试从打包的 assets 或者源码的 ../assets 加载图标
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
        # 使用原生 Qt 布局，彻底解决富文本 HTML 头像文字对齐排版不准的问题
        icon_path = os.path.join(base_path, "icon.png").replace("\\", "/")
        
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(10) # 图片和文字之间的距离
        title_layout.setAlignment(Qt.AlignCenter)
        
        icon_label = QLabel()
        pixmap = QPixmap(icon_path).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        icon_label.setPixmap(pixmap)
        icon_label.setStyleSheet("margin-top: 10px;") 
        title_layout.addWidget(icon_label)
        
        text_label = QLabel("RenpyLens")
        text_label.setObjectName("title") 
        title_layout.addWidget(text_label)

        toolbar.addWidget(title_container)
        toolbar.addStretch()
        self.btn_pin = QPushButton("📌 置顶")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(self._is_pinned)
        self.btn_pin.setStyleSheet(_toolbar_btn_style)
        self.btn_pin.clicked.connect(self._toggle_pin)
        toolbar.addWidget(self.btn_pin)
        layout.addLayout(toolbar)

        # 拖放/点击区域
        self.drop_label = QLabel("""<span style="font-size: 48px;">📂</span><br>将游戏 .exe 拖放或点击此处选择""")
        self.drop_label.setObjectName("drop_zone")
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.setMinimumHeight(160)
        self.drop_label.setCursor(Qt.PointingHandCursor)
        self.drop_label.setTextFormat(Qt.RichText)
        self.drop_label.mousePressEvent = self._on_drop_zone_clicked
        self.drop_label.setWordWrap(True)
        layout.addWidget(self.drop_label)

        # ״̬
        self.status_label = QLabel("就绪 - 等待拖入游戏")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        # 按钮行：开始游戏 | 装载 Hook | 卸载 Hook
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.btn_start_game = QPushButton("▶ 装载 Hook 并开始游戏")
        self.btn_start_game.setEnabled(False)
        self.btn_start_game.clicked.connect(self._on_start_game)
        btn_layout.addWidget(self.btn_start_game, 2)
        self.btn_start_game.setFixedHeight(60)
        self.btn_start_game.setStyleSheet("QPushButton { font-size: 26px; }")
        self.btn_start_game.setToolTip("自动注入翻译 Hook 到游戏并启动，开始实时翻译")

        self.btn_uninstall = QPushButton("📤 卸载 Hook")
        self.btn_uninstall.setEnabled(False)
        self.btn_uninstall.clicked.connect(self._on_uninstall)
        btn_layout.addWidget(self.btn_uninstall, 1)
        self.btn_uninstall.setFixedHeight(60)
        self.btn_uninstall.setStyleSheet("QPushButton { font-size: 20px; }")
        self.btn_uninstall.setToolTip("从游戏目录中移除翻译 Hook 脚本")

        self.btn_clear_cache = QPushButton("🗑️ 清除缓存")
        self.btn_clear_cache.setEnabled(False)
        self.btn_clear_cache.clicked.connect(self._on_clear_cache)
        btn_layout.addWidget(self.btn_clear_cache, 1)
        self.btn_clear_cache.setFixedHeight(60)
        self.btn_clear_cache.setStyleSheet("QPushButton { font-size: 20px; }")
        self.btn_clear_cache.setToolTip("清除当前游戏的翻译缓存，下次将重新翻译所有文本")
        layout.addLayout(btn_layout)

        # 翻译引擎选择行
        engine_layout = QHBoxLayout()
        engine_label = QLabel("翻译引擎:")
        engine_label.setStyleSheet("font-size: 20px; color: #aaa;")
        engine_layout.addWidget(engine_label)
        self.engine_combo = QComboBox()
        self.engine_combo.setEditable(True)
        self.engine_combo.lineEdit().setReadOnly(True)
        self.engine_combo.addItem("内置通道", "builtin")
        self.engine_combo.addItem("OpenAI", "openai")
        self.engine_combo.addItem("Gemini", "gemini")
        self.engine_combo.addItem("Anthropic Claude", "anthropic")
        self.engine_combo.addItem("DeepSeek", "deepseek")
        self.engine_combo.addItem("硅基流动", "siliconflow")
        self.engine_combo.addItem("月之暗面 (Kimi)", "moonshot")
        self.engine_combo.addItem("xAI (Grok)", "xai")
        self.engine_combo.addItem("阿里通义", "alibaba")
        self.engine_combo.addItem("火山引擎", "volcengine")
        self.engine_combo.addItem("智谱AI", "zhipu")
        self.engine_combo.addItem("Ollama", "ollama")
        self.engine_combo.addItem("自定义", "custom")
        self.engine_combo.setStyleSheet("""
            QComboBox {
                background-color: #16213e; color: #eee;
                border: 1px solid #555; border-radius: 4px;
                padding: 10px 14px; font-size: 18px; min-width: 200px;
            }
            QComboBox QLineEdit {
                background-color: #16213e; color: #eee;
                border: none; padding: 0px; font-size: 18px;
                selection-background-color: #4a9eff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 34px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e; color: #eee;
                selection-background-color: #4a9eff; font-size: 18px;
                outline: 0px;
                border: 1px solid #555;
            }
        """)
        self.engine_combo.setItemDelegate(QStyledItemDelegate())
        self.engine_combo.setFixedHeight(48)
        engine_layout.addWidget(self.engine_combo)
        engine_layout.addStretch()
        layout.addLayout(engine_layout)

        # 模型选择行
        model_layout = QHBoxLayout()
        model_label = QLabel("模型:")
        model_label.setStyleSheet("font-size: 20px; color: #aaa;")
        model_layout.addWidget(model_label)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # 可手动输入任意模型名
        self.model_combo.setStyleSheet("""
            QComboBox {
                background-color: #16213e; color: #eee;
                border: 1px solid #555; border-radius: 4px;
                padding: 10px 14px; font-size: 18px; min-width: 250px;
            }
            QComboBox QLineEdit {
                background-color: #16213e; color: #eee;
                border: none; padding: 0px; font-size: 18px;
                selection-background-color: #4a9eff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 34px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e; color: #eee;
                selection-background-color: #4a9eff; font-size: 18px;
                outline: 0px;
                border: 1px solid #555;
            }
        """)
        self._update_model_combo()  # 填充模型列表
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_combo.setFixedHeight(48)
        model_layout.addWidget(self.model_combo)
        self.model_hint = QLabel("可手动修改")
        self.model_hint.setStyleSheet("font-size: 18px; color: #666;")
        model_layout.addWidget(self.model_hint)
        model_layout.addStretch()
        layout.addLayout(model_layout)

        # ── 引擎设置区域（固定高度容器，防止切换引擎时布局跳动）──
        self.engine_settings_container = QWidget()
        _es_layout = QVBoxLayout(self.engine_settings_container)
        _es_layout.setContentsMargins(0, 0, 0, 0)
        _es_layout.setSpacing(14)
        _es_layout.setAlignment(Qt.AlignTop)  # 行紧贴顶部，空白留底部

        # 节点选择行 (内置通道专属) — 包在 QWidget 中便于整行隐藏
        self.node_row = QWidget()
        self.node_layout = QHBoxLayout(self.node_row)
        self.node_layout.setContentsMargins(0, 0, 0, 0)
        node_label = QLabel("线路选择:")
        node_label.setStyleSheet("font-size: 20px; color: #aaa;")
        self.node_layout.addWidget(node_label)
        self.node_combo = QComboBox()
        self.node_combo.setEditable(True)
        self.node_combo.lineEdit().setReadOnly(True)
        
        builtin_nodes = self.config.get("builtin_nodes", [])
        for node in builtin_nodes:
            name = node.get("name", "未命名节点")
            url = node.get("url", "")
            self.node_combo.addItem(name, url)
            
        self.node_combo.setStyleSheet("""
            QComboBox {
                background-color: #16213e; color: #eee;
                border: 1px solid #555; border-radius: 4px;
                padding: 10px 14px; font-size: 18px; min-width: 200px;
            }
            QComboBox QLineEdit {
                background-color: #16213e; color: #eee;
                border: none; padding: 0px; font-size: 18px;
                selection-background-color: #4a9eff;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right; width: 34px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e; color: #eee;
                selection-background-color: #4a9eff; font-size: 18px;
                outline: 0px;
                border: 1px solid #555;
            }
        """)
        current_builtin_url = self.config.get("builtin_url", "https://frp-bar.com:50588/")
        idx = self.node_combo.findData(current_builtin_url)
        if idx >= 0:
            self.node_combo.setCurrentIndex(idx)
        else:
            self.node_combo.setCurrentIndex(0)
        self.node_combo.currentIndexChanged.connect(self._on_node_changed)
        self.node_combo.setItemDelegate(QStyledItemDelegate())
        self.node_combo.setFixedHeight(48)
        self.node_layout.addWidget(self.node_combo)

        self.node_layout.addStretch()
        _es_layout.addWidget(self.node_row)

        # 内置通道 API 操作行
        self.builtin_api_row = QWidget()
        self.builtin_api_layout = QHBoxLayout(self.builtin_api_row)
        self.builtin_api_layout.setContentsMargins(0, 0, 0, 0)
        self.builtin_api_layout.setSpacing(10)

        builtin_api_btn_style = """
            QPushButton {
                background-color: #16213e; color: #4a9eff;
                border: 1px solid #4a9eff; border-radius: 4px;
                font-size: 18px; padding: 8px 14px;
            }
            QPushButton:hover { background-color: #1a2744; color: #6bb5ff; border-color: #6bb5ff; }
            QPushButton:disabled { color: #555; border-color: #444; }
        """

        self.builtin_api_layout.addStretch() # 占位
        self.btn_trial_key = QPushButton("🔑 获取试用API")
        self.btn_trial_key.setCursor(Qt.PointingHandCursor)
        self.btn_trial_key.setStyleSheet(builtin_api_btn_style)
        self.btn_trial_key.clicked.connect(self._on_request_trial_key)
        self.builtin_api_layout.addWidget(self.btn_trial_key)

        self.api_status_label = QLabel()
        self.api_status_label.setStyleSheet("font-size: 18px; padding-left: 8px;")
        self.builtin_api_layout.addWidget(self.api_status_label)

        self.expiry_group = QWidget()
        self.expiry_group_layout = QHBoxLayout(self.expiry_group)
        self.expiry_group_layout.setContentsMargins(0, 0, 0, 0)
        self.expiry_group_layout.setSpacing(2)

        self.api_expiry_label = QLabel()
        self.api_expiry_label.setAlignment(Qt.AlignVCenter)
        self.api_expiry_label.setStyleSheet("font-size: 18px; color: #aaa;")
        self.expiry_group_layout.addWidget(self.api_expiry_label)

        self.btn_refresh_expiry = QPushButton("⟳")
        self.btn_refresh_expiry.setCursor(Qt.PointingHandCursor)
        self.btn_refresh_expiry.setToolTip("刷新到期时间")
        self.btn_refresh_expiry.setFixedSize(22, 22)
        self.btn_refresh_expiry.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #4a9eff;
                border: none;
                font-size: 20px;
                padding: 0px;
                font-weight: bold;
            }
            QPushButton:hover { color: #6bb5ff; }
            QPushButton:disabled { color: #555; }
        """)
        self.btn_refresh_expiry.clicked.connect(self._on_refresh_trial_expiry_clicked)
        self.expiry_group_layout.addWidget(self.btn_refresh_expiry)

        self.builtin_api_layout.addWidget(self.expiry_group)
        self.builtin_api_layout.setAlignment(self.expiry_group, Qt.AlignVCenter)

        self.builtin_api_layout.addStretch()
        _es_layout.addWidget(self.builtin_api_row)

        # API 地址行 — 包在 QWidget 中便于整行隐藏
        self.url_row = QWidget()
        self.url_layout = QHBoxLayout(self.url_row)
        self.url_layout.setContentsMargins(0, 0, 0, 0)
        url_label = QLabel("API 地址:")
        url_label.setStyleSheet("font-size: 20px; color: #aaa;")
        self.url_layout.addWidget(url_label)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("如 https://frp-bar.com:50588/v1")
        self.url_input.setStyleSheet("""
            QLineEdit {
                background-color: #16213e; color: #eee;
                border: 1px solid #555; border-radius: 4px;
                padding: 10px 14px; font-size: 18px; min-width: 400px;
            }
            QLineEdit:focus { border-color: #4a9eff; }
        """)
        self.url_input.editingFinished.connect(self._on_url_changed)
        self.url_layout.addWidget(self.url_input)
        self.url_layout.addStretch()
        _es_layout.addWidget(self.url_row)

        # API Key 行 — 包在 QWidget 中便于整行隐藏
        self.key_row = QWidget()
        self.key_layout = QHBoxLayout(self.key_row)
        self.key_layout.setContentsMargins(0, 0, 0, 0)
        key_label = QLabel("API 密钥:")
        key_label.setStyleSheet("font-size: 20px; color: #aaa;")
        self.key_layout.addWidget(key_label)
        
        self.key_container = QWidget()
        h_key = QHBoxLayout(self.key_container)
        h_key.setContentsMargins(0, 0, 0, 0)
        h_key.setSpacing(6)
        
        # key_input + toggle 包在子容器中，方便单独隐藏
        self.key_input_wrapper = QWidget()
        h_input = QHBoxLayout(self.key_input_wrapper)
        h_input.setContentsMargins(0, 0, 0, 0)
        h_input.setSpacing(6)
        
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setStyleSheet("""
            QLineEdit {
                background-color: #16213e; color: #eee;
                border: 1px solid #555; border-radius: 4px;
                padding: 10px 14px; font-size: 18px; min-width: 400px;
            }
            QLineEdit:focus { border-color: #4a9eff; }
        """)
        self.key_input.editingFinished.connect(self._on_key_changed)
        h_input.addWidget(self.key_input)
        
        self.btn_key_toggle = QPushButton("🙈")
        self.btn_key_toggle.setFixedSize(42, 42)
        self.btn_key_toggle.setToolTip("显示/隐藏密钥")
        self.btn_key_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_key_toggle.setStyleSheet("""
            QPushButton {
                background-color: #16213e; color: #888;
                border: 1px solid #555; border-radius: 4px;
                font-size: 18px; padding: 0;
            }
            QPushButton:hover { color: #4a9eff; border-color: #4a9eff; }
        """)
        def _toggle_echo():
            if self.key_input.echoMode() == QLineEdit.Password:
                self.key_input.setEchoMode(QLineEdit.Normal)
                self.btn_key_toggle.setText("👁")
            else:
                self.key_input.setEchoMode(QLineEdit.Password)
                self.btn_key_toggle.setText("🙈")
        self.btn_key_toggle.clicked.connect(_toggle_echo)
        h_input.addWidget(self.btn_key_toggle)
        
        h_key.addWidget(self.key_input_wrapper)
        self.key_layout.addWidget(self.key_container)
        self.key_layout.addStretch()
        _es_layout.addWidget(self.key_row)

        # 固定容器高度 = 2行控件(每行48px) + 1个间距(14px) = 110px
        self.engine_settings_container.setFixedHeight(110)
        layout.addWidget(self.engine_settings_container)

        # 连接引擎选择信号并设置初始选项 (放最后以确保其他 UI 已创建)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        current_engine = self.config.get("translation_engine", "builtin")
        idx = self.engine_combo.findData(current_engine)
        if idx >= 0:
            self.engine_combo.setCurrentIndex(idx)
            # 如果索引没变(都是0)，信号不会触发，手动补一次
            if idx == 0:
                self._on_engine_changed(0)
        else:
            self.engine_combo.setCurrentIndex(0)
            self._on_engine_changed(0)

        # 初始刷新一次地址栏 (仅当当前引擎是内置通道时才需要)
        if self.config.get("translation_engine", "builtin") == "builtin":
            self._on_node_changed()
        # 根据当前引擎决定是否显示 URL 行
        self._update_url_visibility()

        # 日志面板按钮行
        log_toggle_layout = QHBoxLayout()
        log_toggle_layout.setContentsMargins(0, 0, 0, 0)
        log_toggle_layout.setSpacing(8)

        # 共通按钮样式
        _footer_btn_style = """
            QPushButton {
                background-color: transparent; color: #888;
                border: 1px solid #444; border-radius: 4px;
                padding: 6px 16px; font-size: 18px; font-weight: normal;
            }
            QPushButton:hover { color: #ccc; border-color: #666; }
            QPushButton:disabled { color: #444; border-color: #333; }
        """

        # 1. 显示浮窗
        self.btn_overlay_toggle = QPushButton("🪟 显示浮窗")
        self.btn_overlay_toggle.setFixedWidth(140)
        self.btn_overlay_toggle.setStyleSheet(_footer_btn_style)
        self.btn_overlay_toggle.clicked.connect(self._toggle_overlay_visibility)
        log_toggle_layout.addWidget(self.btn_overlay_toggle)

        # 2. 显示工作台
        self.btn_workbench_toggle = QPushButton("🗂️ 显示工作台")
        self.btn_workbench_toggle.setFixedWidth(150)
        self.btn_workbench_toggle.setEnabled(False)
        self.btn_workbench_toggle.setStyleSheet(_footer_btn_style)
        self.btn_workbench_toggle.clicked.connect(self._toggle_workbench_visibility)
        log_toggle_layout.addWidget(self.btn_workbench_toggle)

        # 3. 日志展开/收起
        self.btn_log_toggle = QPushButton("📋 日志 ▲")
        self.btn_log_toggle.setFixedWidth(130)
        self.btn_log_toggle.setStyleSheet(_footer_btn_style)
        self.btn_log_toggle.clicked.connect(self._toggle_log)
        log_toggle_layout.addWidget(self.btn_log_toggle)

        # 中间弹簧，将左侧三个按钮留在左边，右侧内容推向最右
        log_toggle_layout.addStretch()

        # 4. QQ群号（靠右对齐）
        self.qq_group_label = QLabel("QQ群: 1058127921")
        self.qq_group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.qq_group_label.setStyleSheet("color: #666; font-size: 20px; margin-right: 5px;")
        log_toggle_layout.addWidget(self.qq_group_label)

        layout.addLayout(log_toggle_layout)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        self.log_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #8b949e;
                border: 1px solid #333;
                border-radius: 4px;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 16px;
                padding: 8px;
            }
            QTextEdit QScrollBar:vertical {
                background: #0d1117;
                width: 10px;
                border-radius: 5px;
                margin: 2px;
            }
            QTextEdit QScrollBar::handle:vertical {
                background: #444;
                border-radius: 4px;
                min-height: 30px;
            }
            QTextEdit QScrollBar::handle:vertical:hover {
                background: #666;
            }
            QTextEdit QScrollBar::add-line:vertical,
            QTextEdit QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.log_text.setVisible(False)
        layout.addWidget(self.log_text)

        # 发消息测试
        # dialog = TestServerDialog(self.config, self)
        # dialog.exec_()
        
    def _on_overlay_config_changed(self, new_config: dict):
        """当用户在悬浮窗右键菜单修改配置时触发"""
        self.config = new_config
        
        # 立即重新渲染当前句
        who = self._last_displayed_data.get("who", "")
        what = self._last_displayed_data.get("what", "")
        trans = self._last_displayed_data.get("translation", "")
        italic = self._last_displayed_data.get("italic", False)
        choices = self._last_displayed_data.get("choices", [])
        choice_translations = self._last_displayed_data.get("choice_translations", [])
        
        if trans or choice_translations:
            display = self._format_display(
                who, what, trans, italic, choices=choices, choice_translations=choice_translations
            )
            self._display_overlay_text(display)

    def _on_workbench_config_changed(self, new_config: dict):
        self.config = new_config
        save_config(self.config)

    def _display_overlay_text(self, text: str):
        self.overlay.set_text(text)
        self._refresh_overlay_edit_context()

    def _build_overlay_edit_context(self):
        dialogue_target = None
        choice_targets = []

        who = self._normalize_speaker(self._last_displayed_data.get("who", ""))
        what = self._last_displayed_data.get("what", "")
        choices = list(self._last_displayed_data.get("choices", []))
        choice_translations = list(self._last_displayed_data.get("choice_translations", []))

        if what:
            entry = self.cache.get_entry(what) or {}
            dialogue_target = {
                "source": what,
                "translation": (
                    entry.get("translation", "")
                    if entry
                    else self._last_displayed_data.get("translation", "")
                ),
                "entry_type": ENTRY_TYPE_DIALOGUE,
                "speaker": self._normalize_speaker(entry.get("speaker", "") or who),
            }

        for index, choice in enumerate(choices):
            entry = self.cache.get_entry(choice) or {}
            translation = entry.get("translation", "") if entry else ""
            if not entry and index < len(choice_translations):
                translation = choice_translations[index]
            choice_targets.append(
                {
                    "source": choice,
                    "translation": translation or "",
                    "entry_type": ENTRY_TYPE_CHOICE,
                    "choice_index": index,
                    "speaker": "",
                    "menu_label": f"选项 [{index + 1}]",
                }
            )

        return dialogue_target, choice_targets

    def _refresh_overlay_edit_context(self):
        if not hasattr(self, "overlay") or not self.overlay:
            return
        dialogue_target, choice_targets = self._build_overlay_edit_context()
        self.overlay.set_edit_context(dialogue_target, choice_targets)

    def _refresh_current_display_from_cache(self):
        who = self._normalize_speaker(self._last_displayed_data.get("who", ""))
        what = self._last_displayed_data.get("what", "")
        italic = self._last_displayed_data.get("italic", False)
        choices = list(self._last_displayed_data.get("choices", []))
        dialogue_entry = self.cache.get_entry(what) if what else None
        render_who = self._normalize_speaker(
            dialogue_entry.get("speaker", "") if dialogue_entry is not None else who
        ) or who
        current_translation = (
            dialogue_entry.get("translation", "")
            if dialogue_entry is not None
            else self._last_displayed_data.get("translation", "")
        )
        choice_translations = []
        previous_choice_translations = list(self._last_displayed_data.get("choice_translations", []))
        for index, choice in enumerate(choices):
            entry = self.cache.get_entry(choice)
            translation = entry.get("translation", "") if entry is not None else ""
            if entry is None and index < len(previous_choice_translations):
                translation = previous_choice_translations[index]
            choice_translations.append(translation or "")

        display = self._format_display(
            render_who,
            what,
            current_translation,
            italic,
            choices=choices,
            choice_translations=choice_translations,
        )
        self._display_overlay_text(display)

    def _refresh_workbench_entries(self, selected_source: str = ""):
        if not hasattr(self, "workbench") or not self.workbench:
            return
        entries = self.cache.list_recent_entries(self._workbench_limit)
        self.workbench.set_entries(entries, selected_source=selected_source)

    def _normalize_speaker(self, speaker) -> str:
        return normalize_speaker_name(speaker)

    def _save_manual_translation_entry(self, payload: dict, refresh_workbench: bool):
        source = str(payload.get("source") or "").strip()
        if not source:
            return None
        entry_type = payload.get("entry_type", ENTRY_TYPE_DIALOGUE)
        speaker = self._normalize_speaker(payload.get("speaker", ""))
        translation = str(payload.get("translation") or "").strip()
        saved_entry = self.cache.save_manual_translation(
            source,
            translation,
            entry_type=entry_type,
            speaker=speaker,
        )
        if not saved_entry:
            return None
        self.btn_clear_cache.setEnabled(not self.cache.is_empty())
        self._refresh_current_display_from_cache()
        if refresh_workbench:
            self._refresh_workbench_entries(selected_source=source)
        return saved_entry

    def _save_manual_translation(self, payload: dict):
        self._save_manual_translation_entry(payload, refresh_workbench=True)

    def _autosave_manual_translation(self, payload: dict):
        self._save_manual_translation_entry(payload, refresh_workbench=False)

    def _update_workbench_toggle_button(self, *_):
        visible = hasattr(self, "workbench") and self.workbench and self.workbench.isVisible()
        self.btn_workbench_toggle.setText("🗂️ 隐藏工作台" if visible else "🗂️ 显示工作台")

    def _show_workbench(self, focus_source: str = ""):
        if not hasattr(self, "workbench") or not self.workbench:
            return
        self._refresh_workbench_entries(selected_source=focus_source)
        self.workbench.show()
        self.workbench.raise_()
        if focus_source:
            self.workbench.focus_entry(focus_source)
        self._update_workbench_toggle_button()

    def _hide_workbench(self):
        if hasattr(self, "workbench") and self.workbench and self.workbench.isVisible():
            if not self.workbench.hide_with_autosave(parent=self.workbench):
                return
        self._update_workbench_toggle_button()

    def _toggle_workbench_visibility(self):
        if not hasattr(self, "workbench") or not self.workbench:
            return
        if self.workbench.isVisible():
            self._hide_workbench()
        else:
            self._show_workbench()

    def _on_settings(self):
        """打开设置对话框"""
        previous_config = copy.deepcopy(self.config)
        dlg = SettingsDialog(self.config, parent=self)
        if dlg.exec_() != SettingsDialog.Accepted or not dlg.changed:
            return

        save_config(self.config)

        engine = self.config.get("translation_engine", "builtin")
        semantic_keys = {
            "source_lang",
            "target_lang",
            "system_prompt",
            "batch_prompt",
            "temperature",
            "keep_original_names",
        }
        active_connection_keys = {f"{engine}_url", f"{engine}_api_key"}
        clear_cache = any(previous_config.get(key) != self.config.get(key) for key in semantic_keys)
        translator_needs_rebuild = clear_cache or any(
            previous_config.get(key) != self.config.get(key) for key in active_connection_keys
        )
        socket_port_changed = previous_config.get("socket_port") != self.config.get("socket_port")

        if translator_needs_rebuild:
            self._rebuild_translator(clear_cache=clear_cache)

        self.node_combo.blockSignals(True)
        self.node_combo.clear()
        for node in self.config.get("builtin_nodes", []):
            self.node_combo.addItem(node.get("name", "未命名"), node.get("url", ""))
        idx = self.node_combo.findData(self.config.get("builtin_url", ""))
        if idx >= 0:
            self.node_combo.setCurrentIndex(idx)
        self.node_combo.blockSignals(False)

        if engine == "builtin":
            self.url_input.setText(self.config.get("builtin_url", ""))
            self.key_input.setText(self.config.get("builtin_api_key", ""))
            self._update_api_status_label()
            self._update_api_expiry_label()
        else:
            self.url_input.setText(self.config.get(f"{engine}_url", ""))
            self.key_input.setText(self.config.get(f"{engine}_api_key", ""))

        if socket_port_changed:
            self._restart_hook_server()
            self._hook_installed = False
            self.status_label.setText("✅ 设置已保存，Socket 端口已更新，下次会自动重新注入 Hook")
        else:
            self.status_label.setText("✅ 设置已保存")

        if hasattr(self, "overlay") and self.overlay:
            self.overlay.update_config(self.config)
        if hasattr(self, "workbench") and self.workbench:
            self.workbench.update_config(self.config)
            self._refresh_workbench_entries()
        self._set_translation_controls_enabled(
            not self._is_game_process_running() and not self._is_bulk_job_active()
        )
        print(f"[Main] Settings updated and saved")

    def _toggle_pin(self):
        """切换窗口置顶"""
        self._is_pinned = self.btn_pin.isChecked()
        self.config["window_pinned"] = self._is_pinned
        if self._is_pinned:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()  # setWindowFlags 会隐藏窗口，需要重新 show
        save_config(self.config)

    def _on_url_changed(self):
        """用户修改了 API 地址"""
        url = self.url_input.text().strip()
        if not url:
            return

        engine = self.config.get("translation_engine", "builtin")
        if engine == "builtin":
            if url == self.config.get("builtin_url", ""):
                return
            self.config["builtin_url"] = url
            node_name = self.node_combo.currentText() or url
            self.status_label.setText(f"🚀 内置通道: {node_name}")
            
            # 同步节点下拉框状态
            idx = self.node_combo.findData(url)
            self.node_combo.blockSignals(True)
            if idx >= 0:
                self.node_combo.setCurrentIndex(idx)
            self.node_combo.blockSignals(False)
        else:
            # 所有其它引擎统一用前缀处理
            config_key = f"{engine}_url"
            if url == self.config.get(config_key, ""):
                return
            self.config[config_key] = url
            engine_name = self.engine_combo.currentText()
            self.status_label.setText(f"🌐 {engine_name} API 地址: {url}")

        save_config(self.config)
        # 重建翻译器，但不要因为连接参数变更清缓存
        self._rebuild_translator(clear_cache=False)
        print(f"[Main] API URL updated: {url}")

    def _on_key_changed(self):
        """用户修改了 API Key"""
        key = self.key_input.text().strip()
        engine = self.config.get("translation_engine", "builtin")
        # 统一使用 {engine}_api_key 前缀保存
        config_key = f"{engine}_api_key"
        self.config[config_key] = key

        save_config(self.config)
        # 重建翻译器，但不要因为 Key 变更清缓存
        self._rebuild_translator(clear_cache=False)
        if engine == "builtin":
            self._update_api_status_label()
            self._update_api_expiry_label()
            self.btn_refresh_expiry.setEnabled(bool(key))
        print(f"[Main] API Key updated (saved)")

    def _on_request_trial_key(self):
        """用户点击'获取试用API'按钮，向服务器申请 Key 并填入文本框"""
        self.btn_trial_key.setEnabled(False)
        self.btn_trial_key.setText("⏳ 申请中...")
        # 通过信号回到主线程
        self._trial_key_signal.connect(self._on_trial_key_result)

        def _request():
            # 从配置中读取获取试用 Key 的 API 地址，若无则使用默认值
            trial_url = self.config.get("trial_key_url", "https://frp-bar.com:58385/get_trial_key")
            result = register_trial_key(get_hwid(), trial_url)
            self._trial_key_signal.emit(result or {})

        threading.Thread(target=_request, daemon=True).start()

    def _on_trial_key_result(self, result):
        """处理试用 Key 申请结果（主线程回调）"""
        self._trial_key_signal.disconnect(self._on_trial_key_result)
        self.btn_trial_key.setEnabled(True)
        self.btn_trial_key.setText("🔑 获取试用API")

        result = result or {}
        key = str(result.get("key", "") or "").strip()
        expiry_text = str(result.get("expires", "") or "").strip()

        if key:
            # 同步写入 config 并保存
            self.config["builtin_api_key"] = key
            self.config["builtin_api_expiry"] = expiry_text
            save_config(self.config)
            # 同步更新 UI 文本框
            self.key_input.setText(key)
            # 重建翻译器以使用新 Key
            engine = self.config.get("translation_engine", "builtin")
            self.translator = create_translator(engine, self.config)
            self.status_label.setText("✅ 试用 Key 已获取并填入")
            self._update_api_expiry_label()
            self.btn_refresh_expiry.setEnabled(True)
            print(f"[Main] Trial Key obtained and auto-filled")
        else:
            self.status_label.setText(f"❌ 获取试用 Key 失败，请检查网络。{self._support_tip()}")
            QMessageBox.warning(
                self,
                "获取试用 Key 失败",
                f"请检查网络后重试。\n\n{self._support_tip()}",
            )
        # 更新 API 状态标识
        self._update_api_status_label()
        if not key:
            self._update_api_expiry_label()

    def _update_api_status_label(self):
        """更新内置通道的 API 状态指示器"""
        key = self.config.get("builtin_api_key", "")
        if key:
            self.api_status_label.setText("✅ API已就绪")
            self.api_status_label.setStyleSheet("font-size: 18px; padding-left: 8px; color: #4caf50;")
        else:
            self.api_status_label.setText("❌ 未获取API")
            self.api_status_label.setStyleSheet("font-size: 18px; padding-left: 8px; color: #ff5252;")

    def _update_api_expiry_label(self, text: str | None = None, loading: bool = False):
        """更新内置通道 API 到期时间显示"""
        if loading:
            display_text = "刷新中..."
            color = "#4a9eff"
        else:
            cached = self.config.get("builtin_api_expiry", "").strip()
            display_text = (text if text is not None else cached).strip()
            if not display_text:
                display_text = "未获取"
                color = "#888"
            elif display_text in ("获取失败", "待接入"):
                color = "#ffb74d"
            else:
                color = "#ddd"
        self.api_expiry_label.setText(f"API到期时间：{display_text}")
        self.api_expiry_label.setStyleSheet(f"font-size: 18px; color: {color}; padding-left: 12px;")

    def _set_expiry_refresh_loading(self, loading: bool):
        self.btn_refresh_expiry.setEnabled(not loading)
        self.btn_refresh_expiry.setText("⟳")

    def _on_refresh_trial_expiry_clicked(self):
        self._refresh_trial_expiry()

    def _refresh_trial_expiry(self):
        """刷新内置通道 API 到期时间"""
        if not self.config.get("builtin_api_key", "").strip():
            self._update_api_expiry_label()
            return

        self._set_expiry_refresh_loading(True)
        self._update_api_expiry_label(loading=True)

        def _request():
            expiry_text = self._request_trial_expiry_text()
            self._trial_expiry_signal.emit(expiry_text)

        threading.Thread(target=_request, daemon=True).start()

    def _request_trial_expiry_text(self):
        """查询真实 API 到期时间"""
        trial_url = self.config.get("trial_key_url", "https://frp-bar.com:58385/get_trial_key")
        api_key = self.config.get("builtin_api_key", "").strip()
        if not api_key:
            return ""
        return fetch_trial_key_expiry(get_hwid(), api_key, trial_url)

    def _on_trial_expiry_result(self, expiry_text):
        self._set_expiry_refresh_loading(False)
        expiry_text = str(expiry_text or "").strip()
        if expiry_text:
            self.config["builtin_api_expiry"] = expiry_text
            save_config(self.config)
            self._update_api_expiry_label(expiry_text)
            print(f"[Main] Trial API expiry refreshed: {expiry_text}")
        else:
            self._update_api_expiry_label("获取失败")
            self.status_label.setText(f"❌ API 到期时间获取失败。{self._support_tip()}")

    def _update_url_visibility(self):
        """控制 API 地址、API 密钥、线路选择框的可见性"""
        engine = self.config.get("translation_engine", "builtin")
        is_builtin = engine == "builtin"
        
        # Node 行: 仅内置通道显示
        self.node_row.setVisible(is_builtin)
        self.builtin_api_row.setVisible(is_builtin)
        
        # URL 行: 内置通道隐藏（用线路选择代替），其余引擎显示
        self.url_row.setVisible(not is_builtin)
        
        # Key 行: ollama 和内置通道隐藏，其它引擎显示
        show_key = engine not in ("ollama", "builtin")
        self.key_row.setVisible(show_key)
        
        # 可手动修改 提示: 内置通道隐藏
        self.model_hint.setVisible(not is_builtin)
        
        # 更新内置通道的 API 状态指示
        if is_builtin:
            self._update_api_status_label()
            self._update_api_expiry_label()
            self.btn_refresh_expiry.setEnabled(bool(self.config.get("builtin_api_key", "").strip()))

    def _on_node_changed(self):
        """修改内置通道节点下拉框时，自动填入API地址并保存配置"""
        url = self.node_combo.currentData()
        if url:
            self.url_input.setText(url)
            self._on_url_changed()


    def _toggle_log(self):
        """切换日志面板显示/隐藏，同时调整窗口大小以保持其他组件布局不变"""
        spacing = self.layout().spacing()
        if self.log_text.isVisible():
            # 记住日志框高度，供展开时恢复
            self._log_saved_height = self.log_text.height()
            target_h = self.height() - self._log_saved_height - spacing
            self.log_text.setVisible(False)
            QApplication.processEvents()
            self.resize(self.width(), target_h)
            self.btn_log_toggle.setText("📋 日志 ▲")
        else:
            # 用保存的高度恢复窗口尺寸
            saved_h = getattr(self, '_log_saved_height', 200)
            target_h = self.height() + saved_h + spacing
            self.log_text.setVisible(True)
            self.resize(self.width(), target_h)
            self.btn_log_toggle.setText("📋 日志 ▼")

    def _update_overlay_toggle_button(self, *_):
        visible = hasattr(self, "overlay") and self.overlay and self.overlay.isVisible()
        self.btn_overlay_toggle.setText("🪟 隐藏浮窗" if visible else "🪟 显示浮窗")

    def _toggle_overlay_visibility(self):
        if not hasattr(self, "overlay") or not self.overlay:
            return
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.reset_to_default_position()
            self.overlay.show()

    def _append_log(self, text: str):
        """向日志面板追加文本"""
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()
        # 限制日志行数，防止内存膨胀
        doc = self.log_text.document()
        if doc.blockCount() > 500:
            cursor = QTextCursor(doc.begin())
            cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, doc.blockCount() - 400)
            cursor.removeSelectedText()

    def _support_tip(self) -> str:
        return f"如需协助，请加入官方交流QQ群：{self.SUPPORT_QQ_GROUP}"

    def _auto_check_updates(self):
        self._start_update_check()

    def _restore_status_after_update_check(self):
        text = self.status_label.text()
        if "自动检查更新" in text or "启动后自动检查更新" in text:
            self.status_label.setText("就绪 - 等待拖入游戏")

    def _start_update_check(self):
        if self._update_checking:
            return
        self._update_checking = True
        self.status_label.setText("⏳ 启动后自动检查更新中...")

        def _worker():
            repo = self.config.get("github_repo", "liuyuan-wen/RenpyLens")
            release, err = fetch_latest_release(repo)
            payload = {
                "error": err,
                "repo": repo,
                "current": self.config.get("version", "v0.0.0"),
                "release": release,
            }
            self._update_check_signal.emit(payload)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_check_result(self, payload: dict):
        self._update_checking = False

        payload = payload or {}
        err = str(payload.get("error") or "").strip()
        release = payload.get("release")
        current_version = str(payload.get("current", "v0.0.0"))

        if err:
            err_upper = err.upper()
            if "TIMEOUT:" in err_upper or "NETWORK:" in err_upper:
                # QMessageBox.information(
                #     self,
                #     "自动检查更新",
                #     "当前网络无法稳定访问 GitHub，已跳过自动更新检查。\n\n"
                #     f"{self._support_tip()}",
                # )
                self.status_label.setText(f"⚠️ 自动更新检查已跳过。{self._support_tip()}")
            else:
                self.status_label.setText(f"⚠️ 自动更新检查失败。{self._support_tip()}")
            return

        if not release:
            self._restore_status_after_update_check()
            return

        latest_tag = str(release.tag_name or "").strip()
        if not latest_tag or not is_newer_version(latest_tag, current_version):
            self._restore_status_after_update_check()
            return

        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self,
                "发现新版本",
                f"检测到新版本：{latest_tag}\n当前版本：{current_version}\n\n"
                "当前为源码运行模式，不执行自动替换。\n"
                f"{self._support_tip()}",
            )
            return

        if not release.asset_url:
            QMessageBox.information(
                self,
                "发现新版本",
                f"检测到新版本：{latest_tag}\n当前版本：{current_version}\n\n"
                "未找到可自动更新的 EXE 资源。\n"
                f"{self._support_tip()}",
            )
            return

        reply = QMessageBox.question(
            self,
            "发现新版本",
            f"检测到新版本：{latest_tag}\n当前版本：{current_version}\n\n"
            "点击“是”将自动从 GitHub 下载并更新（完成后自动重启）。\n"
            "若下载不便，也可直接加入 QQ 群获取最新版本：1058127921",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self.status_label.setText(f"ℹ️ 已跳过本次自动更新。{self._support_tip()}")
            return

        self._start_update_download(release)

    def _start_update_download(self, release):
        if self._update_downloading:
            self.status_label.setText("⏳ 更新包正在下载，请稍候...")
            return

        self._update_downloading = True
        self.status_label.setText("⏳ 正在下载更新包...")

        def _worker():
            file_path, err = download_release_asset(release.asset_url, release.asset_name or "RenpyLens_update.exe")
            self._update_download_signal.emit(
                {
                    "release": release,
                    "error": err,
                    "file_path": file_path,
                }
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_download_result(self, payload: dict):
        self._update_downloading = False

        payload = payload or {}
        err = payload.get("error")
        file_path = payload.get("file_path")

        if err or not file_path:
            QMessageBox.warning(
                self,
                "更新下载失败",
                f"{err or '下载文件无效'}\n\n{self._support_tip()}",
            )
            self.status_label.setText(f"❌ 更新下载失败。{self._support_tip()}")
            return

        target_exe = os.path.abspath(sys.executable)
        ok, launch_err = launch_windows_updater_script(
            new_exe_path=file_path,
            target_exe_path=target_exe,
            current_pid=os.getpid(),
        )
        if not ok:
            QMessageBox.warning(
                self,
                "更新启动失败",
                f"{launch_err or '无法启动更新程序'}\n\n{self._support_tip()}",
            )
            self.status_label.setText(f"❌ 更新启动失败。{self._support_tip()}")
            return

        QMessageBox.information(self, "准备更新", "更新包已下载，程序将退出并自动完成更新后重启。")
        QApplication.quit()

    def _setup_log_redirect(self):
        """将 stdout/stderr 重定向到日志面板"""
        self._log_stream = LogStream()
        self._log_stream.text_written.connect(self._append_log)
        sys.stdout = self._log_stream
        sys.stderr = self._log_stream

    def _setup_services(self):
        # 翻译器（根据配置选择引擎）
        engine = self.config.get("translation_engine", "builtin")
        if not self.translator:
            self.translator = create_translator(engine, self.config)
        print(f"[Main] Translation engine: {engine}")

        # Socket 服务器
        self._start_hook_server()
        # 翻译弹窗
        self.overlay = TranslationOverlay(self.config)
        self.overlay.config_updated.connect(self._on_overlay_config_changed)
        self.overlay.edit_saved.connect(self._save_manual_translation)
        self.overlay.autosave_requested.connect(self._autosave_manual_translation)
        self.overlay.show_workbench_requested.connect(self._show_workbench)
        self.overlay.visibility_changed.connect(self._update_overlay_toggle_button)
        self._update_overlay_toggle_button()

        self.workbench = TranslationWorkbench(self.config)
        self.workbench.config_updated.connect(self._on_workbench_config_changed)
        self.workbench.visibility_changed.connect(self._update_workbench_toggle_button)
        self.workbench.save_requested.connect(self._save_manual_translation)
        self.workbench.autosave_requested.connect(self._autosave_manual_translation)
        self.workbench.bulk_translate_requested.connect(self._on_workbench_bulk_translate_requested)
        self.workbench.bulk_cancel_requested.connect(self._on_workbench_bulk_cancel_requested)
        self.workbench.set_game_title(self.game_title if self._current_game_exe else "")
        self._update_workbench_toggle_button()
        
        # 保存最后一句显示的原文和翻译
        self._last_displayed_data = {
            "who": "",
            "what": "",
            "translation": "",
            "italic": False,
            "choices": [],
            "choice_translations": [],
        }

    # --- 拖放 ---
    def _connect_hook_server_signals(self, server: HookServer):
        server.text_received.connect(self._on_text_received)
        server.prefetch_received.connect(self._on_prefetch_received)
        server.message_received.connect(self._on_hook_message_received)

    def _start_hook_server(self):
        self.server = HookServer(port=self.config["socket_port"])
        self._connect_hook_server_signals(self.server)
        self.server.start()
        print(f"[Main] Socket server started, port: {self.config['socket_port']}")

    def _restart_hook_server(self):
        if hasattr(self, "server") and self.server:
            try:
                self.server.stop()
            except Exception:
                pass
        self._reset_hook_session_state()
        self._start_hook_server()

    def _flush_workbench_refresh(self):
        selected_source = self._workbench_refresh_pending_source
        self._workbench_refresh_pending_source = ""
        self._refresh_workbench_entries(selected_source=selected_source)

    def _schedule_workbench_refresh(self, selected_source: str = "", delay_ms: int = 180):
        if selected_source:
            self._workbench_refresh_pending_source = selected_source
        self._workbench_refresh_timer.start(max(1, int(delay_ms)))

    def _is_game_process_running(self) -> bool:
        return bool(self._game_process and self._game_process.poll() is None)

    def _is_bulk_job_active(self) -> bool:
        with self._bulk_job_lock:
            return self._bulk_job.get("state") in {
                "preparing",
                "scanning",
                "translating",
                "cancelling",
            }

    def _is_source_covered(self, source: str) -> bool:
        entry = self.cache.get_entry(source)
        if not entry:
            return False
        return bool(str(entry.get("translation") or "").strip() or entry.get("is_manual"))

    def _clean_translation_result(self, text: str) -> str:
        clean_text = str(text or "")
        for _ in range(3):
            new_text = re.sub(r"\{[^{}]*\}", "", clean_text)
            if new_text == clean_text:
                break
            clean_text = new_text
        clean_text = re.sub(
            r"\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?",
            "",
            clean_text,
            flags=re.IGNORECASE,
        ).strip()
        return clean_text

    def _send_hook_control_command(self, command: str, payload: dict | None = None) -> tuple[bool, str | None]:
        message = {"command": command}
        if payload:
            message.update(payload)
        try:
            sock = socket.create_connection(
                ("127.0.0.1", int(self.config.get("socket_port", 19876)) + 1),
                timeout=2.0,
            )
            try:
                raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
                sock.sendall(raw)
            finally:
                sock.close()
            return True, None
        except Exception as e:
            return False, str(e)

    def _update_bulk_workbench_state(self):
        if not hasattr(self, "workbench") or not self.workbench:
            return

        with self._bulk_job_lock:
            job = dict(self._bulk_job)

        state = job.get("state", "idle")
        if state == "idle":
            self.workbench.set_bulk_idle()
            return
        if state in {"preparing", "scanning", "cancelling"}:
            self.workbench.set_bulk_preparing(job.get("stage_message") or "0%")
            return
        if state == "translating":
            self.workbench.set_bulk_progress(
                int(job.get("covered_count") or 0),
                int(job.get("total_texts") or 0),
                job.get("stage_message") or "正在批量翻译...",
            )
            return

        result_message = job.get("result_message") or ""
        if state == "completed":
            self.workbench.set_bulk_result(result_message, level="success", auto_reset_ms=5000)
        elif state == "cancelled":
            self.workbench.set_bulk_result(result_message, level="warning", auto_reset_ms=5000)
        elif state == "failed":
            self.workbench.set_bulk_result(result_message, level="error", auto_reset_ms=5000)

    def _on_bulk_ui_event(self, payload):
        payload = payload or {}
        action = payload.get("action")
        if action == "sync":
            self._update_bulk_workbench_state()
        elif action == "refresh_workbench":
            self._schedule_workbench_refresh(payload.get("selected_source", ""))
        elif action == "start_game":
            if not self._hook_session_ready and not self._is_game_process_running():
                self._on_start_game()
        elif action == "schedule_reset":
            self._bulk_reset_job_id = str(payload.get("job_id") or "")
            self._bulk_reset_timer.start(5000)

    def _reset_bulk_job_if_final(self):
        with self._bulk_job_lock:
            if self._bulk_job.get("job_id") != self._bulk_reset_job_id:
                return
            if self._bulk_job.get("state") not in {"completed", "cancelled", "failed"}:
                return
            self._bulk_job = self._new_bulk_job_state()
        self._update_bulk_workbench_state()

    def _finish_bulk_job(self, job_id: str, state: str, message: str):
        final_message = str(message or "").strip()
        with self._bulk_job_lock:
            if self._bulk_job.get("job_id") != job_id:
                return
            self._bulk_job["state"] = state
            self._bulk_job["stage_message"] = ""
            self._bulk_job["result_message"] = final_message
            self._bulk_job["error"] = final_message if state == "failed" else ""

        self._bulk_ui_signal.emit({"action": "sync"})
        self._bulk_ui_signal.emit({"action": "refresh_workbench"})
        if state == "completed":
            self._status_signal.emit(final_message or "✅ 全游戏翻译完成")
        elif state == "cancelled":
            self._status_signal.emit(final_message or "⚠️ 已取消全游戏翻译")
        elif state == "failed":
            self._status_signal.emit(final_message or "❌ 全游戏翻译失败")
        if not self._is_game_process_running():
            self._set_translation_controls_enabled(True)
            self.btn_start_game.setEnabled(bool(self._current_game_exe))
        self._bulk_ui_signal.emit({"action": "schedule_reset", "job_id": job_id})

    def _format_bulk_scan_error_detail(self, detail: str) -> str:
        clean_detail = str(detail or "").strip()
        lowered = clean_detail.lower()
        if (
            "script map is unavailable" in lowered
            or "script is unavailable" in lowered
            or "all_stmts is unavailable" in lowered
        ):
            return "Ren'Py 脚本尚未完成加载，请等待游戏进入主菜单或第一段对话后再试。"
        return clean_detail or "未知错误"

    def _build_bulk_translate_message(self) -> str:
        parts = [
            "🧩 自动打开游戏，读取所有文本，分批进行大模型翻译。",
            "💾 翻译结果会保存到配置目录中的 <code>translation_cache.db</code>。",
        ]
        return "<br><br>".join(parts)

    def _show_bulk_translate_confirm_dialog(self) -> bool:
        owner = self
        if hasattr(self, "workbench") and self.workbench and self.workbench.isVisible():
            owner = self.workbench

        dialog = QDialog(owner)
        dialog.setWindowTitle("🚀 一键翻译全游戏")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumWidth(620)
        dialog.setStyleSheet(
            """
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b1220, stop:1 #111827);
                color: #edf2ff;
            }
            QFrame#bulk_warning {
                background-color: rgba(255, 190, 60, 0.12);
                border: 1px solid rgba(255, 190, 60, 0.45);
                border-radius: 14px;
            }
            QLabel#bulk_body {
                font-size: 19px;
                color: #d8e2f4;
            }
            QPushButton#bulk_confirm_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2b7cff, stop:1 #1f6feb);
                color: white;
                border: 1px solid #4d8cff;
                border-radius: 10px;
                padding: 11px 20px;
                font-size: 20px;
                font-weight: bold;
                min-width: 172px;
            }
            QPushButton#bulk_confirm_btn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3988ff, stop:1 #2a78f0);
            }
            QPushButton#bulk_confirm_btn:pressed {
                background: #185bd6;
            }
            QPushButton#bulk_cancel_btn {
                background-color: transparent;
                color: #cdd6eb;
                border: 1px solid #495673;
                border-radius: 10px;
                padding: 11px 20px;
                font-size: 20px;
                font-weight: bold;
                min-width: 110px;
            }
            QPushButton#bulk_cancel_btn:hover {
                color: #ffffff;
                border-color: #7482a2;
                background-color: rgba(255, 255, 255, 0.04);
            }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(12)

        body = QLabel(self._build_bulk_translate_message())
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setStyleSheet("background: transparent; font-size: 19px; color: #d8e2f4;")
        layout.addWidget(body)

        if self.config.get("translation_engine", "builtin") == "builtin":
            warning = QFrame()
            warning.setObjectName("bulk_warning")
            warning_layout = QVBoxLayout(warning)
            warning_layout.setContentsMargins(14, 12, 14, 12)
            warning_layout.setSpacing(6)

            warning_title = QLabel("⚠ 内置通道提醒")
            warning_title.setStyleSheet("font-size: 17px; font-weight: bold; color: #ffd27a;")
            warning_text = QLabel(
                "如果使用的是内置通道，请先联系 QQ 群群主获取打开 TPM/RPM 上限的 API Key，"
                "否则全量翻译过程中可能触发限额并报错。"
            )
            warning_text.setWordWrap(True)
            warning_text.setStyleSheet("font-size: 17px; color: #f0c987;")
            warning_layout.addWidget(warning_title)
            warning_layout.addWidget(warning_text)
            layout.addWidget(warning)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("bulk_cancel_btn")
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(dialog.reject)
        confirm_button = QPushButton("确认，开始翻译")
        confirm_button.setObjectName("bulk_confirm_btn")
        confirm_button.setCursor(Qt.PointingHandCursor)
        confirm_button.clicked.connect(dialog.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(confirm_button)
        layout.addLayout(buttons)
        dialog.exec_()
        return dialog.result() == QDialog.Accepted

    def _on_workbench_bulk_translate_requested(self):
        if not self._current_game_exe:
            QMessageBox.warning(self, "未选择游戏", "请先选择一个 Ren'Py 游戏后再使用“一键翻译全游戏”。")
            return
        if self._is_bulk_job_active():
            QMessageBox.information(self, "任务进行中", "一键翻译全游戏任务已经在进行中。")
            return

        if not self._show_bulk_translate_confirm_dialog():
            return

        job_id = str(int(time.time() * 1000))
        with self._bulk_job_lock:
            self._bulk_job = self._new_bulk_job_state()
            self._bulk_job.update(
                {
                    "job_id": job_id,
                    "state": "preparing",
                    "stage_message": "0% · 正在准备任务...",
                }
            )
        self.btn_start_game.setEnabled(False)
        self._bulk_ui_signal.emit({"action": "sync"})
        threading.Thread(target=self._bootstrap_bulk_job, args=(job_id,), daemon=True).start()

    def _on_workbench_bulk_cancel_requested(self):
        with self._bulk_job_lock:
            if self._bulk_job.get("state") not in {"preparing", "scanning", "translating"}:
                return
            self._bulk_job["cancel_requested"] = True
            self._bulk_job["state"] = "cancelling"
            self._bulk_job["stage_message"] = "0% · 正在取消..."
            job_id = str(self._bulk_job.get("job_id") or "")
        self._bulk_ui_signal.emit({"action": "sync"})
        if job_id:
            ok, err = self._send_hook_control_command("cancel_scan", {"job_id": job_id})
            if not ok:
                print(f"[Bulk] Failed to send cancel command: {err}")

    def _bootstrap_bulk_job(self, job_id: str):
        if not self._current_game_exe:
            self._finish_bulk_job(job_id, "failed", "❌ 未选择游戏，无法开始全量翻译。")
            return

        with self._bulk_job_lock:
            if self._bulk_job.get("job_id") != job_id:
                return
            self._bulk_job["stage_message"] = "0% · 正在注入最新 Hook..."
        self._bulk_ui_signal.emit({"action": "sync"})

        ok, msg = inject_hook(self._current_game_exe, HOOK_SCRIPT, self.config.get("socket_port", 19876))
        if not ok:
            self._finish_bulk_job(job_id, "failed", f"❌ 注入 Hook 失败：{msg}")
            return

        self._hook_installed = True
        if not self._hook_session_ready:
            if self._is_game_process_running():
                self._finish_bulk_job(
                    job_id,
                    "failed",
                    "❌ 当前游戏实例未加载新版 Hook。请通过 RenpyLens 重新注入并重启游戏后再试。",
                )
                return

            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != job_id:
                    return
                self._bulk_job["stage_message"] = "0% · 正在启动游戏..."
            self._bulk_ui_signal.emit({"action": "sync"})
            self._bulk_ui_signal.emit({"action": "start_game"})

            deadline = time.time() + 20.0
            while time.time() < deadline:
                with self._bulk_job_lock:
                    if self._bulk_job.get("job_id") != job_id:
                        return
                    if self._bulk_job.get("cancel_requested"):
                        self._finish_bulk_job(job_id, "cancelled", "⚠️ 已取消全游戏翻译。")
                        return
                if self._hook_ready_event.wait(0.2):
                    break
            else:
                self._finish_bulk_job(
                    job_id,
                    "failed",
                    "❌ 等待 Hook 就绪超时。请通过 RenpyLens 重新注入并重启游戏后再试。",
                )
                return

        with self._bulk_job_lock:
            if self._bulk_job.get("job_id") != job_id:
                return
            self._bulk_job["state"] = "scanning"
            self._bulk_job["stage_message"] = "0% · 正在扫描脚本..."
            self._bulk_job["scan_entries"] = {}
            self._bulk_job["pending_entries"] = []
            self._bulk_job["total_texts"] = 0
            self._bulk_job["covered_count"] = 0
        self._bulk_ui_signal.emit({"action": "sync"})

        ok, err = self._send_hook_control_command("scan_all", {"job_id": job_id})
        if not ok:
            self._finish_bulk_job(
                job_id,
                "failed",
                f"❌ 无法连接新版 Hook 控制通道（{err}）。请通过 RenpyLens 重新注入并重启游戏后再试。",
            )

    def _on_hook_message_received(self, message: dict):
        msg_type = str((message or {}).get("type") or "").strip()
        if not msg_type:
            return

        if msg_type == "hook_ready":
            self._hook_session_ready = True
            self._hook_ready_event.set()
            print(f"[Hook] Hook ready on control port {message.get('control_port', '')}")
            return

        job_id = str((message or {}).get("job_id") or "").strip()
        with self._bulk_job_lock:
            active_job_id = str(self._bulk_job.get("job_id") or "")
            active_state = str(self._bulk_job.get("state") or "")

        if not active_job_id or (job_id and job_id != active_job_id):
            return
        if active_state not in {"preparing", "scanning", "translating", "cancelling"}:
            return

        if msg_type == "bulk_scan_started":
            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != active_job_id:
                    return
                self._bulk_job["state"] = "scanning"
                self._bulk_job["stage_message"] = "0% · 正在扫描脚本..."
            self._bulk_ui_signal.emit({"action": "sync"})
            return

        if msg_type == "bulk_scan_chunk":
            items = message.get("items", [])
            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != active_job_id:
                    return
                scan_entries = self._bulk_job.get("scan_entries", {})
                for item in items or []:
                    source = str((item or {}).get("source") or "").strip()
                    if not source or source in scan_entries:
                        continue
                    scan_entries[source] = {
                        "source": source,
                        "entry_type": str((item or {}).get("entry_type") or ENTRY_TYPE_DIALOGUE),
                        "speaker": self._normalize_speaker((item or {}).get("speaker", "")),
                    }
                self._bulk_job["stage_message"] = (
                    f"0% · 正在扫描脚本... 已发现 {len(scan_entries)} 条"
                )
            self._bulk_ui_signal.emit({"action": "sync"})
            return

        if msg_type == "bulk_scan_cancelled":
            self._finish_bulk_job(active_job_id, "cancelled", "⚠️ 已取消全游戏翻译。")
            return

        if msg_type == "bulk_scan_error":
            detail = self._format_bulk_scan_error_detail(message.get("message") or "")
            self._finish_bulk_job(
                active_job_id,
                "failed",
                f"❌ 扫描脚本失败：{detail or '未知错误'}",
            )
            return

        if msg_type == "bulk_scan_finished":
            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != active_job_id:
                    return
                entries = list(self._bulk_job.get("scan_entries", {}).values())
                total_texts = int(message.get("total") or 0) or len(entries)
                covered_count = sum(1 for entry in entries if self._is_source_covered(entry["source"]))
                pending_entries = [entry for entry in entries if not self._is_source_covered(entry["source"])]
                cancel_requested = bool(self._bulk_job.get("cancel_requested"))
                self._bulk_job["total_texts"] = total_texts
                self._bulk_job["covered_count"] = covered_count
                self._bulk_job["pending_entries"] = pending_entries
                if cancel_requested:
                    self._bulk_job["state"] = "cancelling"
                    self._bulk_job["stage_message"] = "0% · 正在取消..."
                else:
                    self._bulk_job["state"] = "translating"
                    self._bulk_job["stage_message"] = "正在批量翻译..."
            self._bulk_ui_signal.emit({"action": "sync"})

            if cancel_requested:
                self._finish_bulk_job(active_job_id, "cancelled", "🛑 已取消全游戏翻译。")
                return
            if total_texts <= 0:
                self._finish_bulk_job(active_job_id, "completed", "ℹ️ 没有扫描到可翻译文本。")
                return
            if not pending_entries:
                self._finish_bulk_job(
                    active_job_id,
                    "completed",
                    f"✅ 全游戏翻译完成：{covered_count}/{total_texts}",
                )
                return

            threading.Thread(target=self._bulk_translate_worker, args=(active_job_id,), daemon=True).start()

    def _bulk_wait_for_slot(self, job_id: str) -> bool:
        rpm = max(1, int(self.config.get("bulk_translate_rpm", 60)))
        interval = 60.0 / float(rpm)
        while True:
            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != job_id:
                    return False
                if self._bulk_job.get("cancel_requested"):
                    return False
                last_request_time = float(self._bulk_job.get("last_request_time") or 0.0)
            remaining = interval - (time.monotonic() - last_request_time)
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))

        with self._bulk_job_lock:
            if self._bulk_job.get("job_id") != job_id:
                return False
            if self._bulk_job.get("cancel_requested"):
                return False
            self._bulk_job["last_request_time"] = time.monotonic()
        return True

    def _bulk_translate_worker(self, job_id: str):
        try:
            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != job_id:
                    return
                pending_entries = list(self._bulk_job.get("pending_entries", []))
                total_texts = int(self._bulk_job.get("total_texts") or 0)

            batch_size = max(1, int(self.config.get("bulk_translate_batch_size", 5)))
            for start in range(0, len(pending_entries), batch_size):
                with self._bulk_job_lock:
                    if self._bulk_job.get("job_id") != job_id:
                        return
                    if self._bulk_job.get("cancel_requested"):
                        self._finish_bulk_job(job_id, "cancelled", "⚠️ 已取消全游戏翻译。")
                        return
                    self._bulk_job["state"] = "translating"
                    self._bulk_job["stage_message"] = "正在批量翻译..."
                self._bulk_ui_signal.emit({"action": "sync"})

                batch_entries = pending_entries[start : start + batch_size]
                uncovered_entries = [
                    entry for entry in batch_entries if not self._is_source_covered(entry["source"])
                ]
                if uncovered_entries:
                    if not self._bulk_wait_for_slot(job_id):
                        self._finish_bulk_job(job_id, "cancelled", "⚠️ 已取消全游戏翻译。")
                        return
                    texts = [entry["source"] for entry in uncovered_entries]
                    with self._translator_lock:
                        if not self.translator:
                            raise RuntimeError("Translator is not ready.")
                        results = self.translator.translate_batch(
                            texts,
                            source_lang=self.config["source_lang"],
                            target_lang=self.config["target_lang"],
                            game_title=self.game_title,
                        )

                    batch_payload = []
                    for entry, translation in zip(uncovered_entries, results):
                        clean_translation = self._clean_translation_result(translation)
                        if clean_translation and not clean_translation.startswith("[翻译失败"):
                            batch_payload.append(
                                {
                                    "source": entry["source"],
                                    "translation": clean_translation,
                                    "entry_type": entry.get("entry_type", ENTRY_TYPE_DIALOGUE),
                                    "speaker": entry.get("speaker", ""),
                                }
                            )
                    if batch_payload:
                        self.cache.save_machine_translations_if_absent(batch_payload)

                covered_in_batch = sum(
                    1 for entry in batch_entries if self._is_source_covered(entry["source"])
                )
                with self._bulk_job_lock:
                    if self._bulk_job.get("job_id") != job_id:
                        return
                    self._bulk_job["covered_count"] = min(
                        total_texts,
                        int(self._bulk_job.get("covered_count") or 0) + covered_in_batch,
                    )
                self._bulk_ui_signal.emit({"action": "sync"})
                self._bulk_ui_signal.emit({"action": "refresh_workbench"})

            with self._bulk_job_lock:
                if self._bulk_job.get("job_id") != job_id:
                    return
                covered_count = int(self._bulk_job.get("covered_count") or 0)
                total_texts = int(self._bulk_job.get("total_texts") or 0)
                cancel_requested = bool(self._bulk_job.get("cancel_requested"))

            if cancel_requested:
                self._finish_bulk_job(job_id, "cancelled", "⚠️ 已取消全游戏翻译。")
            elif covered_count >= total_texts:
                self._finish_bulk_job(
                    job_id,
                    "completed",
                    f"✅ 全游戏翻译完成：{covered_count}/{total_texts}",
                )
            else:
                self._finish_bulk_job(
                    job_id,
                    "failed",
                    f"❌ 全游戏翻译未完成：{covered_count}/{total_texts}",
                )
        except KeyExpiredError as e:
            self._key_expired_signal.emit()
            self._finish_bulk_job(job_id, "failed", f"❌ 全游戏翻译失败：{e}")
        except Exception as e:
            self._finish_bulk_job(job_id, "failed", f"❌ 全游戏翻译失败：{e}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".exe"):
                    event.acceptProposedAction()
                    self.drop_label.setStyleSheet(
                        "#drop_zone { border-color: #e94560; background-color: #1a2744; }"
                    )
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_label.setStyleSheet("")

    def dropEvent(self, event: QDropEvent):
        self.drop_label.setStyleSheet("")
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith(".exe"):
                self._select_game(file_path)
                return

    def _on_drop_zone_clicked(self, event):
        """点击拖放区域 → 打开文件浏览器选择 .exe"""
        last_dir = self.config.get("last_game_dir", "")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Ren'Py 游戏 EXE",
            last_dir,
            "可执行文件 (*.exe);;所有文件 (*)",
        )
        if file_path:
            # 保存上次打开的目录
            self.config["last_game_dir"] = os.path.dirname(file_path)
            save_config(self.config)
            self._select_game(file_path)

    def _select_game(self, exe_path: str):
        """选中游戏 EXE（仅记录路径，不注入不启动）"""
        if not is_renpy_game(exe_path):
            QMessageBox.warning(self, "不是 Ren'Py 游戏",
                                f"未检测到 Ren'Py 游戏结构：\n{exe_path}\n\n"
                                "请确认该 .exe 是一个 Ren'Py 游戏。\n\n"
                                f"{self._support_tip()}")
            self.status_label.setText(f"检测失败 - 不是 Ren'Py 游戏。{self._support_tip()}")
            return

        self._current_game_exe = exe_path
        self._reset_hook_session_state()
        name = os.path.basename(exe_path)
        self.drop_label.setText(f'<span style="font-size: 48px;">🎮</span><br>{name}')
        self.status_label.setText(f"已选择: {name}")
        self.cache.set_game(exe_path)
        if hasattr(self, "workbench") and self.workbench:
            self.workbench.set_game_title(self.game_title)
        self.btn_clear_cache.setEnabled(not self.cache.is_empty())
        self.btn_workbench_toggle.setEnabled(True)
        self.btn_start_game.setEnabled(True)
        self.btn_uninstall.setEnabled(False)
        self._hook_installed = False
        self._refresh_workbench_entries()
        print(f"[Main] Game selected: {exe_path}")

    def _on_clear_cache(self):
        """清除当前游戏的翻译缓存"""
        self.cache.clear()
        self.btn_clear_cache.setEnabled(False)
        self._refresh_workbench_entries()
        game_name = os.path.basename(self._current_game_exe) if self._current_game_exe else ""
        self.status_label.setText(f"🗑️ {game_name} 缓存已清除")
        print(f"[Main] Translation cache cleared for {game_name}")

    def _on_install_hook(self):
        """装载 Hook：仅注入，不启动游戏"""
        if not self._current_game_exe:
            return
        exe_path = self._current_game_exe
        self.status_label.setText(f"正在注入 Hook...")

        ok, msg = inject_hook(exe_path, HOOK_SCRIPT, self.config.get("socket_port", 19876))
        if not ok:
            QMessageBox.critical(self, "注入失败", f"{msg}\n\n{self._support_tip()}")
            self.status_label.setText(f"注入失败: {msg}。{self._support_tip()}")
            return

        name = os.path.basename(exe_path)
        self.drop_label.setText(f'<span style="font-size: 48px;">🎮</span><br>{name}<br><span style="color:#4a9eff;">Hook 已装载</span>')
        self.status_label.setText(f"✅ Hook 已注入: {name}")

        self.btn_uninstall.setEnabled(True)
        self.btn_start_game.setEnabled(True)
        self._hook_installed = True
        print(f"[Main] Hook injected: {exe_path}")

    def _on_start_game(self):
        """开始游戏：若未装载 Hook 则自动装载，然后启动游戏"""
        if not self._current_game_exe:
            return
        # 若尚未装载 Hook，自动装载
        if not self._hook_installed:
            self._on_install_hook()
            if not self._hook_installed:
                return  # 装载失败，中止
        
        # 启动游戏时，禁用引擎切换，防止竞态冲突
        self._set_translation_controls_enabled(False)
        self._reset_hook_session_state()
        
        exe_path = self._current_game_exe
        self.status_label.setText("正在启动游戏...")

        self._game_process = launch_game(exe_path)
        if self._game_process:
            has_warmup = False
            with self._translator_lock:
                if self.translator and hasattr(self.translator, 'warmup'):
                    has_warmup = True
            
            if has_warmup:
                self.status_label.setText("🎮 游戏已启动 - 正在预加载翻译模型...")
                threading.Thread(target=self._warmup_model, daemon=True).start()
            else:
                self.status_label.setText("🎮 游戏已启动 - 等待游戏内对话...")
            self.overlay.show()
            self._update_overlay_toggle_button()
            self.showMinimized()
            self.game_timer.start(1000)
            self.btn_start_game.setEnabled(False)
        else:
            self.status_label.setText(f"⚠️ 游戏启动失败，请手动启动游戏 EXE。{self._support_tip()}")
            QMessageBox.warning(
                self,
                "游戏启动失败",
                f"请手动启动游戏 EXE。\n\n{self._support_tip()}",
            )
            self.overlay.show()
            self._set_translation_controls_enabled(True)

    def _check_game_status(self):
        """定时检查游戏进程是否结束"""
        if self._game_process and self._game_process.poll() is not None:
            # 游戏已退出
            self.game_timer.stop()
            self.overlay.hide()
            self.showNormal()  # 恢复主窗口
            if self._current_game_exe:
                name = os.path.basename(self._current_game_exe)
                self.drop_label.setText(f'<span style="font-size: 48px;">🎮</span><br>{name}<br><span style="color:#4a9eff;">Hook 已装载</span>')
                self.btn_start_game.setEnabled(True)
            self._game_process = None
            self._reset_hook_session_state()

            keep_translator_alive = False
            bulk_job_id = ""
            bulk_state = "idle"
            bulk_cancel_requested = False
            with self._bulk_job_lock:
                bulk_job_id = str(self._bulk_job.get("job_id") or "")
                bulk_state = str(self._bulk_job.get("state") or "idle")
                bulk_cancel_requested = bool(self._bulk_job.get("cancel_requested"))

            if bulk_state in {"preparing", "scanning", "cancelling"} and bulk_job_id:
                final_state = "cancelled" if bulk_cancel_requested or bulk_state == "cancelling" else "failed"
                final_message = (
                    "🛑 已取消全游戏翻译。"
                    if final_state == "cancelled"
                    else "❌ 游戏在扫描完成前退出，全游戏翻译已终止。"
                )
                self.status_label.setText(final_message)
                self._finish_bulk_job(bulk_job_id, final_state, final_message)
            elif bulk_state == "translating" and bulk_job_id:
                keep_translator_alive = True
                self.status_label.setText("游戏已退出，正在继续完成一键翻译全游戏任务...")
                self.btn_start_game.setEnabled(False)
                if hasattr(self, "workbench") and self.workbench:
                    self.workbench.show()
                    self.workbench.raise_()
            else:
                self._hide_workbench()
                self.status_label.setText("游戏已退出")

            if not keep_translator_alive:
                self._set_translation_controls_enabled(True)
                # 关闭翻译器连接池，释放 TCP 连接（下次注入时 warmup 会重建）
                with self._translator_lock:
                    if self.translator:
                        self.translator.close()
            # 同步清除缓存按钮状态
            self.btn_clear_cache.setEnabled(not self.cache.is_empty())
            # 重置 Key 过期弹窗标志（下次游戏可再弹）
            self._key_expired_shown = False
            # 保持 btn_uninstall 启用（允许用户手动清理残余文件）
            print("[Main] Game process exited, UI restored and connections released")

    def _warmup_model(self):
        """后台预加载 Ollama 模型"""
        try:
            with self._translator_lock:
                if self.translator:
                    self.translator.warmup()
            self.translation_ready.emit("✅ 模型已就绪")
        except Exception as e:
            print(f"[Warmup] Failed: {e}")


    def _on_engine_changed(self, index):
        """切换翻译引擎"""
        engine = self.engine_combo.itemData(index)
        self.config["translation_engine"] = engine

        # URL 行可见性与内容
        self._update_url_visibility()
        if engine == "ollama":
            self.url_input.setText(self.config.get("ollama_url", "http://localhost:11435"))
            self.url_input.setPlaceholderText("如 http://localhost:11434")
        elif engine == "builtin":
            self.url_input.setText(self.config.get("builtin_url", "http://localhost:8000"))
            self.url_input.setPlaceholderText("如 http://localhost:8000")
            self.key_input.setText(self.config.get("builtin_api_key", ""))
            self.key_input.setPlaceholderText("可选，认证密钥")
            self._update_api_status_label()
            self._update_api_expiry_label()
        else:
            # 对于其他受支持的引擎 (openai, deepseek, anthropic, zhipu 等)，使用统一的前缀处理
            self.url_input.setText(self.config.get(f"{engine}_url", ""))
            self.url_input.setPlaceholderText(f"如 https://api.{engine}.com")
            self.key_input.setText(self.config.get(f"{engine}_api_key", ""))
            self.key_input.setPlaceholderText(f"{engine.capitalize()} API Key")


        # 更新模型下拉框
        self._update_model_combo()

        # 异步切换翻译器，避免阻塞 UI
        old_translator = self.translator
        engine_name = self.engine_combo.currentText()
        model_name = self.model_combo.currentText()
        self.status_label.setText(f"⏳ 正在切换 {engine_name}...")

        def _switch_thread():
            # 获取当前快照，防止切换期间 index 变化
            with self._translator_lock:
                if old_translator:
                    old_translator.close()
                
                # 创建新翻译器 (现在是延迟初始化的，其实很快，但在线程里更稳)
                new_translator = create_translator(engine, self.config)
                
                # 清理缓存 (磁盘 IO)
                self.cache.clear()
                
                # 保存配置 (磁盘 IO)
                save_config(self.config)
                
                # 更新实例
                self.translator = new_translator
            
            print(f"[Main] Async engine switch complete: {engine}, model: {model_name}")
            self._status_signal.emit(f"✅ {engine_name} / {model_name}")

        threading.Thread(target=_switch_thread, daemon=True).start()

    def _update_model_combo(self):
        """根据当前引擎更新模型下拉框"""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        engine = self.config.get("translation_engine", "builtin")

        if engine == "builtin":
            # 内置通道: 可编辑，显示友好名称，内部映射到真实模型名
            self.model_combo.setEditable(True)
            self._builtin_model_map = {"模型1": "Qwen3-8B-FP8"}
            current_real = self.config.get("builtin_model", "Qwen3-8B-FP8")
            
            # 兼容旧配置：如果保存成了显示名，映射回并重写
            if current_real in self._builtin_model_map:
                current_real = self._builtin_model_map[current_real]
                self.config["builtin_model"] = current_real
                save_config(self.config)

            # 找到当前模型对应的友好名称
            current_display = current_real
            for display, real in self._builtin_model_map.items():
                if real == current_real:
                    current_display = display
                    break
            for display_name in self._builtin_model_map:
                self.model_combo.addItem(display_name)
            self.model_combo.setCurrentText(current_display)
        else:
            # 其他所有通道
            self.model_combo.setEditable(True)
            current = self.config.get(f"{engine}_model", "")
            
            default_models = {
                "ollama": self.config.get("ollama_available_models", ["gemma3:4b", "qwen2.5:7b"]),
                "openai": ["gpt-4o-mini", "gpt-4o", "o1-mini", "o3-mini"],
                "anthropic": ["claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022"],
                "deepseek": ["deepseek-chat", "deepseek-reasoner"],
                "siliconflow": ["Pro/deepseek-ai/DeepSeek-V3", "Pro/deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-7B-Instruct"],
                "moonshot": ["moonshot-v1-8k", "moonshot-v1-32k"],
                "xai": ["grok-2-latest", "grok-2-vision-latest"],
                "alibaba": ["qwen-plus", "qwen-max", "qwen-turbo"],
                "volcengine": ["ep-xxxx", "doubao-pro-32k", "doubao-lite-32k"],
                "zhipu": ["glm-4.7-flash", "glm-4.7-plus"],
                "gemini": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-exp"],
                "custom": ["custom-model"]
            }
            
            common_models = default_models.get(engine, [])
            
            # 如果当前模型不在默认列表里，插到第一个
            if current and current not in common_models:
                common_models.insert(0, current)
            elif not current and common_models:
                current = common_models[0]
                
            self.model_combo.addItems(common_models)
            self.model_combo.setCurrentText(current)
            
        # 设置可编辑样式
        le = self.model_combo.lineEdit()
        if le:
            le.setStyleSheet("""
                background-color: #16213e; color: #eee;
                border: none; padding: 0px; font-size: 18px;
                selection-background-color: #4a9eff;
            """)

        self.model_combo.blockSignals(False)

    def _on_model_changed(self, model_name: str):
        """模型选择/输入变化"""
        if not model_name.strip():
            return
        engine = self.config.get("translation_engine", "builtin")
        if engine == "builtin":
            # 将友好名称映射回真实模型名
            model_map = getattr(self, '_builtin_model_map', {})
            real_name = model_map.get(model_name, model_name)
            self.config["builtin_model"] = real_name
            model_name = real_name  # 用于后续日志
        else:
            self.config[f"{engine}_model"] = model_name

        self._rebuild_translator(clear_cache=True)
        self.status_label.setText(f"✅ 模型已切换: {model_name}")
        print(f"[Main] Model switched: {model_name}")
        save_config(self.config)

    def _on_uninstall(self):
        if self._current_game_exe:
            ok, msg = remove_hook(self._current_game_exe)
            if ok:
                self.status_label.setText(f"已卸载: {msg}")
                self.btn_uninstall.setEnabled(False)
                self.btn_start_game.setEnabled(True)

                self._hook_installed = False
                self._reset_hook_session_state()
                self.drop_label.setText(f'<span style="font-size: 48px;">🎮</span><br>{os.path.basename(self._current_game_exe)}')
                self.overlay.hide()
                self._update_overlay_toggle_button()
            else:
                QMessageBox.warning(self, "卸载失败", f"{msg}\n\n{self._support_tip()}")

    @property
    def game_title(self) -> str:
        """从当前游戏 EXE 路径提取游戏名称（去除 .exe 扩展名）"""
        if not self._current_game_exe:
            return "Unknown Game"
        name = os.path.basename(self._current_game_exe)
        if name.lower().endswith(".exe"):
            return name[:-4]
        return name

    # --- 文本处理 ---
    def _on_text_received(
        self,
        who: str,
        what: str,
        italic: bool = False,
        choices: list = None,
        menu_active: bool = False,
    ):
        """收到游戏内当前显示的文本"""
        # 递增 generation，使旧的翻译请求过时
        self._text_generation += 1
        self._process_text(who, what, italic, choices or [], menu_active)

    def _process_text(
        self,
        who: str,
        what: str,
        italic: bool = False,
        choices: list = None,
        menu_active: bool = False,
    ):
        """实际处理文本：查缓存 / 触发翻译 / 触发预取（含菜单选项）"""
        import time as _time
        what = what or ""
        who = self._normalize_speaker(who or "")
        choices = [c for c in (choices or []) if c]
        visible_choices = choices if menu_active else []
        timing_enabled = self.config.get("enable_timing_log", False)
        t_start = _time.perf_counter()
        gen = self._text_generation
        print(
            f"[Main] Processing text (gen={gen}): who={who}, what={what[:80]}, "
            f"choices={len(choices)}, menu_active={menu_active}"
        )

        if what:
            self.cache.mark_seen(what, entry_type=ENTRY_TYPE_DIALOGUE, speaker=who)
        for choice in visible_choices:
            self.cache.mark_seen(choice, entry_type=ENTRY_TYPE_CHOICE, speaker="")
        self._refresh_workbench_entries()

        # 1) 处理当前句与菜单选项缓存
        current_cached = self.cache.get(what) if what else ""
        choice_translation_map = {}
        unresolved_choices = []
        for choice in choices:
            translated = self.cache.get(choice)
            if translated:
                choice_translation_map[choice] = translated
            else:
                choice_translation_map[choice] = ""
                unresolved_choices.append(choice)

        has_current = bool(what)
        need_async = (has_current and not current_cached) or bool(unresolved_choices)
        visible_choice_translations = [choice_translation_map.get(choice, "") for choice in visible_choices]

        if not need_async:
            display = self._format_display(
                who,
                what,
                current_cached if has_current else "",
                italic,
                choices=visible_choices,
                choice_translations=visible_choice_translations,
            )
            self.translation_ready.emit(display)
            if timing_enabled:
                hit_ms = (_time.perf_counter() - t_start) * 1000
                print(f"[Timing] Cache hit: {hit_ms:.1f}ms (text received -> displayed)")
        else:
            # 未缓存 → 统一走批量翻译路径（当前句 + 菜单选项 + 预取项合并为一批）
            display_choices = [
                choice_translation_map.get(choice, "") or "选项翻译中..."
                for choice in visible_choices
            ]
            display_current = current_cached if current_cached else ("翻译中..." if has_current else "")
            self._display_overlay_text(
                self._format_display(
                    who,
                    what,
                    display_current,
                    italic,
                    choices=visible_choices,
                    choice_translations=display_choices,
                )
            )
            threading.Thread(
                target=self._translate_batch_with_current,
                args=(who, what, choices, menu_active, gen, italic), daemon=True
            ).start()

        # 2) 无论缓存命中与否，都检查预取缓冲区是否充裕
        #    inflight 的句子会被视为已就绪而跳过
        if not self._is_bulk_job_active():
            self._ensure_prefetch_buffer(gen)

    def _translate_batch_with_current(
        self,
        who: str,
        what: str,
        choices: list,
        menu_active: bool,
        gen: int,
        italic: bool = False,
    ):
        """将当前句、菜单选项与预取项合并为一个批量翻译请求"""
        import time as _time
        who = self._normalize_speaker(who)
        choices = [c for c in (choices or []) if c]
        visible_choices = choices if menu_active else []
        prefetch_speaker_map = {
            str(item.get("what") or "").strip(): self._normalize_speaker(item.get("who", ""))
            for item in self._latest_prefetch_items
            if str(item.get("what") or "").strip()
        }
        timing_enabled = self.config.get("enable_timing_log", False)
        t_pipeline_start = _time.perf_counter()
        batch_texts = []

        # 用户已翻页 → 跳过翻译（不浪费 API 调用）
        if self._text_generation != gen:
            print(f"[Batch] ⏭ Skipping outdated translation (gen={gen}→{self._text_generation}): {what[:40]}")
            return

        # 当前页面必须先保证当前句与菜单选项（required）可得
        required_texts = []
        seen_required = set()
        if what and self.cache.get(what) is None:
            required_texts.append(what)
            seen_required.add(what)
        for choice in choices:
            if choice in seen_required:
                continue
            if self.cache.get(choice) is None:
                required_texts.append(choice)
                seen_required.add(choice)

        if not required_texts:
            if self._text_generation == gen:
                current_result = self.cache.get(what) if what else ""
                choice_results = [self.cache.get(choice) or "" for choice in visible_choices]
                display = self._format_display(
                    who, what, current_result, italic, choices=visible_choices, choice_translations=choice_results
                )
                self.translation_ready.emit(display)
            return

        # 如果当前句和菜单选项都被其他线程翻译中，则等待缓存就绪而非重复翻译
        with self._inflight_lock:
            required_inflight = [t for t in required_texts if t in self._inflight_texts]
        if required_inflight and len(required_inflight) == len(required_texts):
            print("[Batch] Current/choice texts already being translated, waiting for cache...")
            for _ in range(100):  # 最多等 10 秒
                _time.sleep(0.1)
                # 如果用户已翻页，放弃等待
                if self._text_generation != gen:
                    print(f"[Batch] ⏭ User turned page (gen={gen}→{self._text_generation}), abandoning wait")
                    return
                
                # 同时检查缓存是否就绪，以及原来的翻译线程是否还在运行 (inflight)
                ready = True
                still_inflight = False
                with self._inflight_lock:
                    for text in required_texts:
                        if self.cache.get(text) is None:
                            ready = False
                            if text in self._inflight_texts:
                                still_inflight = True
                            break
                
                if ready:
                    current_result = self.cache.get(what) if what else ""
                    choice_results = [self.cache.get(choice) or "" for choice in visible_choices]
                    display = self._format_display(
                        who, what, current_result, italic, choices=visible_choices, choice_translations=choice_results
                    )
                    self.translation_ready.emit(display)
                    print("[Batch] Wait successful, cache hit for current/choices")
                    return
                
                if not still_inflight:
                    print("[Batch] Waiting target no longer inflight, proceeding to translate locally")
                    break
            # 超时仍未就绪 → 继续走翻译流程

        t_build_start = _time.perf_counter()
        prefetch_count = 0 if self._is_bulk_job_active() else self.config.get("prefetch_count", 5)
        prefetch_added = 0
        # 构建批量列表：当前句 + 菜单选项（优先）+ 预取项中未缓存且未在翻译中的
        seen = set()
        with self._inflight_lock:
            for text in required_texts:
                if text and text not in seen \
                        and self.cache.get(text) is None \
                        and text not in self._inflight_texts:
                    batch_texts.append(text)
                    seen.add(text)

            for item in self._latest_prefetch_items:
                if prefetch_added >= prefetch_count:
                    break
                text = item.get("what", "")
                if text and text not in seen \
                        and self.cache.get(text) is None \
                        and text not in self._inflight_texts:
                    batch_texts.append(text)
                    seen.add(text)
                    prefetch_added += 1
            # 标记 inflight（在锁内完成，防止其他线程同时标记）
            for t in batch_texts:
                self._inflight_texts.add(t)
        t_build_end = _time.perf_counter()

        # 若 required 都在 inflight，当前线程只需等待其结果，不再重复请求
        missing_required = [t for t in required_texts if self.cache.get(t) is None]
        if missing_required and not any(t in batch_texts for t in required_texts):
            print("[Batch] Required texts still inflight, waiting without duplicate API call...")
            should_retry_locally = False
            for _ in range(50):  # 最多等 5 秒
                _time.sleep(0.1)
                if self._text_generation != gen:
                    print(f"[Batch] ⏭ User turned page while waiting (gen={gen}→{self._text_generation})")
                    return
                
                ready = True
                still_inflight = False
                with self._inflight_lock:
                    for t in required_texts:
                        if self.cache.get(t) is None:
                            ready = False
                            if t in self._inflight_texts:
                                still_inflight = True
                            break
                
                if ready:
                    current_result = self.cache.get(what) if what else ""
                    choice_results = [self.cache.get(choice) or "" for choice in visible_choices]
                    display = self._format_display(
                        who, what, current_result, italic, choices=visible_choices, choice_translations=choice_results
                    )
                    self.translation_ready.emit(display)
                    return
                
                if not still_inflight:
                    print("[Batch] Required items no longer inflight elsewhere, giving up waiting")
                    should_retry_locally = True
                    break
            if not should_retry_locally:
                return
            print("[Batch] Required items still missing after wait, retrying translation locally")

        if not batch_texts:
            return

        print(f"[Batch] Batch translating {len(batch_texts)} items (current+choices+prefetch, gen={gen})")

        # 防抖：万事俱备，等待一小段时间，如果用户翻页了就跳过 API 调用
        debounce_ms = self.config.get("debounce_ms", 200)
        if debounce_ms > 0:
            _time.sleep(debounce_ms / 1000.0)
            if self._text_generation != gen:
                print(f"[Batch] ⏭ Debounce skip (gen={gen}→{self._text_generation}): {what[:40]}")
                with self._inflight_lock:
                    for t in batch_texts:
                        self._inflight_texts.discard(t)
                return

        try:
            t_api_start = _time.perf_counter()
            with self._translator_lock:
                if not self.translator:
                    return
                results = self.translator.translate_batch(
                    batch_texts,
                    source_lang=self.config["source_lang"],
                    target_lang=self.config["target_lang"],
                    game_title=self.game_title,
                )
            t_api_end = _time.perf_counter()
            result_map = {text: translation for text, translation in zip(batch_texts, results)}

            t_parse_start = _time.perf_counter()
            for text, translation in zip(batch_texts, results):
                clean_translation = translation
                if isinstance(clean_translation, str):
                    for _ in range(3):
                        new_t = re.sub(r'\{[^{}]*\}', '', clean_translation)
                        if new_t == clean_translation:
                            break
                        clean_translation = new_t
                    clean_translation = re.sub(
                        r'\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?',
                        '',
                        clean_translation,
                        flags=re.IGNORECASE,
                    ).strip()
                if not clean_translation.startswith("[翻译失败"):
                    entry_type = ENTRY_TYPE_CHOICE if text in visible_choices else ENTRY_TYPE_DIALOGUE
                    speaker_for_text = who if text == what else prefetch_speaker_map.get(text, "")
                    self.cache.save_machine_translation_if_absent(
                        text,
                        clean_translation,
                        entry_type=entry_type,
                        speaker=speaker_for_text,
                    )
                    print(f"[Batch] ✅ {text[:30]} -> {clean_translation[:30]}")
                else:
                    print(f"[Batch] ❌ {text[:30]} -> {clean_translation[:30]}")
            t_parse_end = _time.perf_counter()

            # 只有仍是最新文本时才显示到弹窗
            if self._text_generation == gen:
                current_result = ""
                if what:
                    current_result = self.cache.get(what) or result_map.get(what, "[翻译失败]")
                choice_results = [
                    self.cache.get(choice) or result_map.get(choice, "[翻译失败]")
                    for choice in visible_choices
                ]
                display = self._format_display(
                    who,
                    what,
                    current_result,
                    italic,
                    choices=visible_choices,
                    choice_translations=choice_results,
                )
                self.translation_ready.emit(display)
            else:
                print(f"[Batch] Translation done but user turned page, result cached only (gen={gen}→{self._text_generation})")

            t_pipeline_end = _time.perf_counter()
            if timing_enabled:
                build_ms = (t_build_end - t_build_start) * 1000
                debounce_actual_ms = (t_api_start - t_build_end) * 1000  # 含防抖等待
                api_ms = (t_api_end - t_api_start) * 1000
                parse_ms = (t_parse_end - t_parse_start) * 1000
                total_ms = (t_pipeline_end - t_pipeline_start) * 1000
                # 从 translator 获取更细粒度的 API 计时
                api_timing = {}
                with self._translator_lock:
                    if self.translator:
                        api_timing = getattr(self.translator, 'last_timing', {})
                api_detail = ""
                if api_timing:
                    pt = api_timing.get('prompt_tokens', 0)
                    ct = api_timing.get('completion_tokens', 0)
                    api_detail = f" (prompt_tok={pt}, comp_tok={ct})"
                print(f"\n{'='*60}")
                print(f"[Timing] Translation latency breakdown ({len(batch_texts)} items):")
                print(f"  📦 Prompt build: {build_ms:.1f}ms")
                print(f"  ⏳ Debounce wait: {debounce_actual_ms:.1f}ms")
                print(f"  🌐 API call:     {api_ms:.0f}ms (network+server){api_detail}")
                print(f"  📝 Result parse: {parse_ms:.1f}ms")
                print(f"  ⏱️  Total:        {total_ms:.0f}ms")
                print(f"{'='*60}\n")
        except KeyExpiredError as e:
            if self._text_generation == gen:
                current_result = f"[{e}]" if what else ""
                choice_results = [self.cache.get(choice) or f"[{e}]" for choice in visible_choices]
                display = self._format_display(
                    who,
                    what,
                    current_result,
                    italic,
                    choices=visible_choices,
                    choice_translations=choice_results,
                )
                self.translation_ready.emit(display)
            self._key_expired_signal.emit()
        except Exception as e:
            if self._text_generation == gen:
                current_result = f"[翻译失败: {e}]" if what else ""
                choice_results = [self.cache.get(choice) or f"[翻译失败: {e}]" for choice in visible_choices]
                display = self._format_display(
                    who,
                    what,
                    current_result,
                    italic,
                    choices=visible_choices,
                    choice_translations=choice_results,
                )
                self.translation_ready.emit(display)
            self._status_signal.emit(f"❌ 翻译失败，请检查网络或配置。{self._support_tip()}")
        finally:
            with self._inflight_lock:
                for t in batch_texts:
                    self._inflight_texts.discard(t)

    def _ensure_prefetch_buffer(self, gen: int):
        """检查后续缓存是否满 prefetch_count 条，不满则从未翻译处开始翻译"""
        if self._is_bulk_job_active():
            return
        items = self._latest_prefetch_items
        if not items:
            return

        prefetch_count = self.config.get("prefetch_count", 5)

        check_range = items[:prefetch_count]
        first_uncached_idx = -1

        with self._inflight_lock:
            for i, item in enumerate(check_range):
                text = item.get("what", "")
                if text:
                    is_cached = self.cache.get(text) is not None
                    is_inflight = text in self._inflight_texts
                    if not is_cached and not is_inflight:
                        first_uncached_idx = i
                        break

        if first_uncached_idx == -1:
            return

        batch_to_translate = items[first_uncached_idx : first_uncached_idx + prefetch_count]
        texts_to_translate = [item.get("what", "") for item in batch_to_translate]

        if not texts_to_translate:
            return

        # 标记整个 batch 为 inflight
        with self._inflight_lock:
            for t in texts_to_translate:
                self._inflight_texts.add(t)

        print(f"[Prefetch] Cache insufficient (item {first_uncached_idx+1} not ready), triggering batch translation of {len(texts_to_translate)} items (gen={gen})")
        threading.Thread(
            target=self._prefetch_batch_async, args=(batch_to_translate, gen), daemon=True
        ).start()



    def _prefetch_batch_async(self, items: list[dict], gen: int):
        """后台批量翻译：合并到一个prompt，模型有上下文对照"""
        import time as _time
        prefetch_items = []
        for item in items or []:
            if not isinstance(item, dict):
                item = {"what": item, "who": "", "italic": False}
            text = str(item.get("what", "") or "").strip()
            if not text:
                continue
            prefetch_items.append(
                {
                    "what": text,
                    "who": self._normalize_speaker(item.get("who", "")),
                    "italic": bool(item.get("italic", False)),
                }
            )
        texts = [item["what"] for item in prefetch_items]
        if not texts:
            return
        if self._is_bulk_job_active():
            with self._inflight_lock:
                for t in texts:
                    self._inflight_texts.discard(t)
            return
        timing_enabled = self.config.get("enable_timing_log", False)
        t_pipeline_start = _time.perf_counter()

        # 防抖：等待一小段时间，如果用户翻页了就跳过 API 调用
        debounce_ms = self.config.get("debounce_ms", 100)
        if debounce_ms > 0:
            _time.sleep(debounce_ms / 1000.0)

        # 开始 API 调用前检查：如果用户已翻页，跳过
        if self._text_generation != gen:
            print(f"[Prefetch] ⏭ Debounce skipping outdated prefetch (gen={gen}→{self._text_generation})")
            with self._inflight_lock:
                for t in texts:
                    self._inflight_texts.discard(t)
            return
        if self._is_bulk_job_active():
            with self._inflight_lock:
                for t in texts:
                    self._inflight_texts.discard(t)
            return

        self._prefetch_running = True
        try:
            t_api_start = _time.perf_counter()
            with self._translator_lock:
                if not self.translator:
                    return
                results = self.translator.translate_batch(
                    texts,
                    source_lang=self.config["source_lang"],
                    target_lang=self.config["target_lang"],
                    game_title=self.game_title,
                )
            t_api_end = _time.perf_counter()

            for item, translation in zip(prefetch_items, results):
                text = item["what"]
                clean_translation = translation
                if isinstance(clean_translation, str):
                    for _ in range(3):
                        new_t = re.sub(r'\{[^{}]*\}', '', clean_translation)
                        if new_t == clean_translation:
                            break
                        clean_translation = new_t
                    clean_translation = re.sub(
                        r'\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?',
                        '',
                        clean_translation,
                        flags=re.IGNORECASE,
                    ).strip()
                if not clean_translation.startswith("[翻译失败"):
                    self.cache.save_machine_translation_if_absent(
                        text,
                        clean_translation,
                        entry_type=ENTRY_TYPE_DIALOGUE,
                        speaker=item.get("who", ""),
                    )
                    print(f"[Prefetch] ✅ {text[:30]} -> {clean_translation[:30]}")
                else:
                    print(f"[Prefetch] ❌ {text[:30]} -> {clean_translation[:30]}")

            if timing_enabled:
                t_end = _time.perf_counter()
                api_ms = (t_api_end - t_api_start) * 1000
                total_ms = (t_end - t_pipeline_start) * 1000
                debounce_actual_ms = (t_api_start - t_pipeline_start) * 1000
                with self._translator_lock:
                    api_timing = getattr(self.translator, 'last_timing', {}) if self.translator else {}
                api_detail = ""
                if api_timing:
                    pt = api_timing.get('prompt_tokens', 0)
                    ct = api_timing.get('completion_tokens', 0)
                    api_detail = f" (prompt_tok={pt}, comp_tok={ct})"
                print(f"\n{'─'*60}")
                print(f"[Timing][Prefetch] Prefetch latency breakdown ({len(texts)} items):")
                print(f"  ⏳ Debounce wait: {debounce_actual_ms:.1f}ms")
                print(f"  🌐 API call:     {api_ms:.0f}ms (network+server){api_detail}")
                print(f"  ⏱️  Total:        {total_ms:.0f}ms")
                print(f"{'─'*60}\n")
        except KeyExpiredError:
            self._key_expired_signal.emit()
        except Exception as e:
            print(f"[Prefetch] Batch translation failed: {e}")
            self._status_signal.emit(f"❌ 预取翻译失败，请检查网络或配置。{self._support_tip()}")
        finally:
            self._prefetch_running = False
            with self._inflight_lock:
                for t in texts:
                    self._inflight_texts.discard(t)

    def _on_prefetch_received(self, items: list):
        """存储预取列表（hook每次发来最新的后续对话）"""
        normalized_items = []
        for item in items or []:
            if not isinstance(item, dict):
                item = {"what": item, "who": "", "italic": False}
            text = str(item.get("what", "") or "").strip()
            if not text:
                continue
            normalized_items.append(
                {
                    "who": self._normalize_speaker(item.get("who", "")),
                    "what": text,
                    "italic": bool(item.get("italic", False)),
                }
            )
        self._latest_prefetch_items = normalized_items
        print(f"[Prefetch] Received {len(normalized_items)} upcoming dialogues")

    def _on_translation_ready(self, display_text: str):
        self._display_overlay_text(display_text)
        # 如果是状态信息（带勾选信号），或者主窗口还在显示“预加载”状态，则同步更新状态栏
        if display_text.startswith("✅"):
            self.status_label.setText(display_text)
        elif "预加载" in self.status_label.text():
            self.status_label.setText("✅ 正在游玩 - 等待对话...")

    def _on_key_expired(self):
        """试用 Key 过期弹窗（仅弹一次）"""
        if self._key_expired_shown:
            return
        self._key_expired_shown = True
        QMessageBox.warning(
            self,
            "RenpyLens",
            "您的内置通道试用 API Key 已到期。\n\n"
            f"{self._support_tip()}"
        )

    def _format_display(
        self,
        who: str,
        original: str,
        translation: str,
        italic: bool = False,
        choices: list = None,
        choice_translations: list = None,
    ) -> str:
        who = self._normalize_speaker(who)
        choices = choices or []
        choice_translations = choice_translations or []

        # 记录最后一次要被渲染的数据，以便设置变更时可以瞬间重绘
        self._last_displayed_data["who"] = who
        self._last_displayed_data["what"] = original
        self._last_displayed_data["translation"] = translation
        self._last_displayed_data["italic"] = italic
        self._last_displayed_data["choices"] = list(choices)
        self._last_displayed_data["choice_translations"] = list(choice_translations)

        def _clean_line(text: str) -> str:
            # 最终清理 LLM 编号前缀: "1. ", "1) ", "1- ", "- " 等
            text = str(text or "")
            # 清理可能漏出的 Ren'Py 标签（含部分不完整标签）
            for _ in range(3):
                new_text = re.sub(r'\{[^{}]*\}', '', text)
                if new_text == text:
                    break
                text = new_text
            text = re.sub(
                r'\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?',
                '',
                text,
                flags=re.IGNORECASE,
            ).strip()
            # 增强型清理：支持各种括号编号 [1] (1) 【1】 1. 等
            text = re.sub(r'^\s*[\[(（【]?\d+[\])）】]?\s*[.)\-:、\s]\s*', '', text)
            text = re.sub(r'^\s*[\-\*]\s+', '', text)
            return text.strip()

        # 记录处理逻辑：
        # 1. 检测第一个对话项或选项项是否为菜单说明（Caption）
        # 很多游戏会将说明文字作为 choices[0] 发送，或放在 original (what) 中
        first_is_caption = False
        if choices and original and choices[0].strip() == original.strip():
            first_is_caption = True

        lines = []

        # 2. 提取说明文本 (Caption) 并显示
        if first_is_caption:
            # 如果 choices[0] 就是说明，则直接显示翻译后的它，且不带编号
            trans = choice_translations[0] if choice_translations else ""
            clean_c = _clean_line(trans)
            if clean_c:
                lines.append(clean_c)
        else:
            # 否则，如果 original (what) 有翻译结果，将其作为说明/对话显示
            d_line = _clean_line(translation)
            if d_line:
                if italic:
                    d_line = f"<i>{d_line}</i>"
                if who and self.config.get("show_character_name", True):
                    d_line = f"【{who}】{d_line}"
                lines.append(d_line)

        # 3. 处理后续（真正的）选择项，从 [1] 开始编号
        choice_start_idx = 1 if first_is_caption else 0
        for i in range(choice_start_idx, len(choices)):
            trans = choice_translations[i] if i < len(choice_translations) else ""
            clean_trans = _clean_line(trans)
            if clean_trans:
                idx_num = i + 1 - choice_start_idx
                lines.append(f"[{idx_num}] {clean_trans}")

        return "\n".join(lines)

    # --- 关闭 ---
    def closeEvent(self, event):
        if hasattr(self, "workbench") and self.workbench:
            if not self.workbench.confirm_discard_or_save(parent=self):
                event.ignore()
                return
        # 保存配置
        save_config(self.config)
        # 清理 hook
        if self._current_game_exe:
            remove_hook(self._current_game_exe)
        # 停止服务器
        self.server.stop()
        if hasattr(self, "workbench") and self.workbench:
            self.workbench.blockSignals(True)
            self.workbench.hide()
            self.workbench.deleteLater()
        self.overlay.close()
        event.accept()


def kill_port_process(port: int):
    """杀死占用指定端口的旧进程"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        my_pid = os.getpid()
        pids_to_kill = set()
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if len(parts) >= 5:
                    pid = int(parts[-1])
                    if pid != my_pid and pid != 0:
                        pids_to_kill.add(pid)
        for pid in pids_to_kill:
            print(f"[Cleanup] Killing old process occupying port {port}, PID={pid}")
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=5)
    except Exception as e:
        print(f"[Cleanup] Port cleanup failed (can be ignored): {e}")


def main():
    # 先清理可能残留的旧进程
    kill_port_process(19876)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置任务栏和窗口图标
    try:
        import ctypes
        # 设置 AppUserModelID，让 Windows 将其视为独立应用而非 Python 脚本，以正确显示任务栏图标
        myappid = 'renpylens.translator.app.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    # 兼容 PyInstaller 运行时的 _MEIPASS 路径
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets")
        
    # 尝试加载 icon.ico 或 icon.png
    icon_ico = os.path.join(base_path, "icon.ico")
    icon_png = os.path.join(base_path, "icon.png")
    if os.path.exists(icon_ico):
        app.setWindowIcon(QIcon(icon_ico))
    elif os.path.exists(icon_png):
        app.setWindowIcon(QIcon(icon_png))

    # 暗色主题
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(26, 26, 46))
    palette.setColor(QPalette.WindowText, QColor(238, 238, 238))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
