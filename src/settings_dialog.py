# -*- coding: utf-8 -*-
"""设置对话框 - 将所有配置项以 GUI 方式呈现"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QFormLayout, QGroupBox, QTextEdit, QDoubleSpinBox,
    QScrollArea
)
from PyQt5.QtCore import Qt

# 通用暗色样式
_DARK_STYLE = """
    QDialog {
        background-color: #1a1a2e;
        color: #eee;
        font-family: "Microsoft YaHei", "Segoe UI";
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
    QComboBox {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 12px; font-size: 18px; min-width: 180px;
    }
    QComboBox QAbstractItemView {
        background-color: #1a1a2e; color: #eee;
        selection-background-color: #4a9eff;
    }
    QSpinBox, QDoubleSpinBox {
        background-color: #16213e; color: #eee;
        border: 1px solid #444; border-radius: 4px;
        padding: 8px 12px; font-size: 18px; min-width: 120px;
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
    QTextEdit QScrollBar:vertical {
        background: #1a1a2e;
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
"""

# 常用语言列表
_LANGUAGES = [
    "简体中文", "繁体中文", "English", "日本語", "한국어",
    "Français", "Deutsch", "Español", "Português",
    "Русский", "Italiano", "ไทย", "Tiếng Việt",
    "Bahasa Indonesia", "العربية", "Türkçe",
]
    # "Chinese", "English", "Japanese", "Korean",
    # "French", "German", "Spanish", "Portuguese",
    # "Russian", "Italian", "Thai", "Vietnamese",
    # "Indonesian", "Arabic", "Turkish",


class SettingsDialog(QDialog):
    """配置对话框 - 3 标签页"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        self._changed = False  # 标记是否有修改

        self.setWindowTitle("⚙️ 设置")
        self.setMinimumSize(560, 520)
        self.setStyleSheet(_DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 标签页
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_translation_tab(), "🔤 翻译")
        self.tabs.addTab(self._create_apikey_tab(), "🔑 API 设置")
        self.tabs.addTab(self._create_advanced_tab(), "🔧 高级")
        self.tabs.addTab(self._create_about_tab(), "ℹ️ 关于")
        layout.addWidget(self.tabs)

        # 按钮行
        btn_layout = QHBoxLayout()
        
        self.btn_open_config = QPushButton("📂 打开配置目录")
        self.btn_open_config.setObjectName("open_config_btn")
        self.btn_open_config.setToolTip("打开 config.json 和缓存所在的本地文件夹")
        self.btn_open_config.clicked.connect(self._on_open_config_dir)
        btn_layout.addWidget(self.btn_open_config)
        
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("cancel_btn")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("💾 保存")
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

    # ── 标签页 1: 翻译设置 ──────────────────────────

    def _create_translation_tab(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(10)
        vbox.setContentsMargins(20, 20, 20, 20)

        # 提示（顶部，占满整行宽度）
        hint = QLabel("💡 源语言和目标语言会作为提示词的一部分，也可自行输入任意语言名，如 한국어、Français")
        hint.setStyleSheet("color: #666; font-size: 18px;")
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        # 语言设置表单
        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignRight)

        # 源语言
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItems(_LANGUAGES)
        self.source_lang_combo.setCurrentText(self.config.get("source_lang", "English"))
        self.source_lang_combo.setEditable(True)
        form.addRow("源语言:", self.source_lang_combo)

        # 目标语言
        self.target_lang_combo = QComboBox()
        self.target_lang_combo.addItems(_LANGUAGES)
        self.target_lang_combo.setCurrentText(self.config.get("target_lang", "Chinese"))
        self.target_lang_combo.setEditable(True)
        form.addRow("目标语言:", self.target_lang_combo)

        self.sys_prompt_input = QTextEdit()
        self.sys_prompt_input.setAcceptRichText(False)
        self.sys_prompt_input.setMaximumHeight(100)
        self.sys_prompt_input.setPlainText(self.config.get("system_prompt", "You are a professional game dialogue translator. Translate the user's message into {target_lang}. Keep it natural and concise for a visual novel. Output ONLY the translated text. No numbering, no quotes, no explanations."))
        self.sys_prompt_input.setToolTip("使用 {target_lang} 作为目标语言占位符")
        form.addRow("单句提示词:", self.sys_prompt_input)

        self.batch_prompt_input = QTextEdit()
        self.batch_prompt_input.setAcceptRichText(False)
        self.batch_prompt_input.setMaximumHeight(100)
        self.batch_prompt_input.setPlainText(self.config.get("batch_prompt", "You are a professional game dialogue translator. Translate ALL numbered dialogues into {target_lang}. Keep translations natural and concise. Output ONLY translations in the same numbered format [1]...[2]... No extra text."))
        self.batch_prompt_input.setToolTip("使用 {target_lang} 作为目标语言占位符")
        form.addRow("批量提示词:", self.batch_prompt_input)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(self.config.get("temperature", 0.3))
        form.addRow("温度 (T):", self.temp_spin)

        self.keep_names_check = QCheckBox("保留原文人名（不翻译角色名字）")
        self.keep_names_check.setChecked(self.config.get("keep_original_names", True))
        form.addRow("", self.keep_names_check)

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
        scroll.setStyleSheet("QScrollArea { background-color: transparent; }")
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 12, 20, 20)
        
        # Helper to create group box
        def create_api_group(title, key_prefix, default_url, key_placeholder):
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setSpacing(10)
            url_input = QLineEdit(self.config.get(f"{key_prefix}_url", default_url))
            url_input.setPlaceholderText(f"默认 {default_url}")
            form.addRow("API URL:", url_input)
            
            row, key_input = self._create_key_input(
                self.config.get(f"{key_prefix}_api_key", ""),
                key_placeholder
            )
            form.addRow("API Key:", row)
            return group, url_input, key_input

        # Gemini
        gemini_group, self.gemini_url_input, self.gemini_key_input = create_api_group(
            "Google Gemini", "gemini", "https://generativelanguage.googleapis.com", "在 aistudio.google.com 获取"
        )
        layout.addWidget(gemini_group)

        # 智谱AI
        zhipu_group, self.zhipu_url_input, self.zhipu_key_input = create_api_group(
            "智谱AI (GLM)", "zhipu", "https://open.bigmodel.cn", "在 open.bigmodel.cn 获取"
        )
        layout.addWidget(zhipu_group)
        
        # OpenAI
        openai_group, self.openai_url_input, self.openai_key_input = create_api_group(
            "OpenAI", "openai", "https://api.openai.com", "填写 API Key"
        )
        layout.addWidget(openai_group)

        # Anthropic
        anthropic_group, self.anthropic_url_input, self.anthropic_key_input = create_api_group(
            "Anthropic Claude", "anthropic", "https://api.anthropic.com", "填写 x-api-key"
        )
        layout.addWidget(anthropic_group)

        # DeepSeek
        deepseek_group, self.deepseek_url_input, self.deepseek_key_input = create_api_group(
            "DeepSeek", "deepseek", "https://api.deepseek.com", "填写 API Key"
        )
        layout.addWidget(deepseek_group)

        # 硅基流动
        siliconflow_group, self.siliconflow_url_input, self.siliconflow_key_input = create_api_group(
            "硅基流动 (SiliconFlow)", "siliconflow", "https://api.siliconflow.cn", "填写 API Key"
        )
        layout.addWidget(siliconflow_group)

        # 月之暗面 Kimi
        moonshot_group, self.moonshot_url_input, self.moonshot_key_input = create_api_group(
            "月之暗面 (Moonshot / Kimi)", "moonshot", "https://api.moonshot.cn", "填写 API Key"
        )
        layout.addWidget(moonshot_group)

        # xAI
        xai_group, self.xai_url_input, self.xai_key_input = create_api_group(
            "xAI (Grok)", "xai", "https://api.x.ai", "填写 API Key"
        )
        layout.addWidget(xai_group)

        # 阿里通义
        alibaba_group, self.alibaba_url_input, self.alibaba_key_input = create_api_group(
            "阿里百炼 (DashScope)", "alibaba", "https://dashscope.aliyuncs.com/compatible-mode", "填写 API Key"
        )
        layout.addWidget(alibaba_group)

        # 火山引擎
        volcengine_group, self.volcengine_url_input, self.volcengine_key_input = create_api_group(
            "火山引擎 (Volcengine)", "volcengine", "https://ark.cn-beijing.volces.com", "填写 API Key"
        )
        layout.addWidget(volcengine_group)

        # 自定义
        custom_group, self.custom_url_input, self.custom_key_input = create_api_group(
            "自定义 (兼容 OpenAI 格式)", "custom", "http://localhost:8000", "填写自定义 API Key"
        )
        layout.addWidget(custom_group)

        # 内置通道
        builtin_group = QGroupBox("内置通道")
        builtin_form = QFormLayout(builtin_group)
        builtin_form.setSpacing(10)
        # 每个节点单独一行 URL
        self._builtin_node_inputs = []  # [(name, QLineEdit)]
        builtin_nodes = self.config.get("builtin_nodes", [])
        for node in builtin_nodes:
            name = node.get("name", "未命名节点")
            url = node.get("url", "")
            url_input = QLineEdit(url)
            url_input.setPlaceholderText(f"{name} 的 API 地址")
            builtin_form.addRow(f"{name}:", url_input)
            self._builtin_node_inputs.append((name, url_input))
        builtin_row, self.builtin_key_input = self._create_key_input(
            self.config.get("builtin_api_key", ""),
            "填写服务端认证"
        )
        builtin_form.addRow("API Key:", builtin_row)
        
        # 添加试用 API 地址配置
        self.trial_url_input = QLineEdit(self.config.get("trial_key_url", "https://frp-bar.com:58385/get_trial_key"))
        self.trial_url_input.setPlaceholderText("获取试用 Key 的接口地址")
        builtin_form.addRow("试用 Key 接口:", self.trial_url_input)
        
        layout.addWidget(builtin_group)

        layout.addStretch()
        scroll.setWidget(content)
        main_vbox.addWidget(scroll)
        return tab

    def _create_key_input(self, value: str, placeholder: str) -> tuple:
        """创建密码模式的 API Key 输入框 + 可见的显示/隐藏按钮
        返回 (container_widget, line_edit)"""
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        edit = QLineEdit()
        edit.setText(value)
        edit.setPlaceholderText(placeholder)
        edit.setEchoMode(QLineEdit.Password)
        h.addWidget(edit)

        btn = QPushButton("🙈")
        btn.setFixedSize(36, 36)
        btn.setToolTip("显示/隐藏密钥")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #16213e; color: #888;
                border: 1px solid #444; border-radius: 4px;
                font-size: 16px; padding: 0;
            }
            QPushButton:hover { color: #4a9eff; border-color: #4a9eff; }
        """)
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
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 12, 20, 20)

        # 性能设置
        perf_group = QGroupBox("性能")
        perf_form = QFormLayout(perf_group)
        perf_form.setSpacing(10)

        self.prefetch_spin = QSpinBox()
        self.prefetch_spin.setRange(1, 20)
        self.prefetch_spin.setValue(self.config.get("prefetch_count", 5))
        self.prefetch_spin.setToolTip("提前翻译后续几句对话")
        perf_form.addRow("预取条数:", self.prefetch_spin)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(0, 5000)
        self.debounce_spin.setSingleStep(50)
        self.debounce_spin.setValue(self.config.get("debounce_ms", 200))
        self.debounce_spin.setSuffix(" ms")
        self.debounce_spin.setToolTip("快速翻页时等待多久后才发送翻译请求")
        perf_form.addRow("防抖延迟:", self.debounce_spin)

        self.socket_port_spin = QSpinBox()
        self.socket_port_spin.setRange(1024, 65535)
        self.socket_port_spin.setValue(self.config.get("socket_port", 19876))
        self.socket_port_spin.setToolTip("与游戏 Hook 通信的本地端口")
        perf_form.addRow("通信端口:", self.socket_port_spin)

        self.show_character_name_check = QCheckBox("显示说话人名称")
        self.show_character_name_check.setChecked(self.config.get("show_character_name", True))
        self.show_character_name_check.setToolTip("如果关闭，浮窗上将只显示翻译后的对话内容，不再带有【名字】前缀。")
        perf_form.addRow("", self.show_character_name_check)

        self.force_topmost_check = QCheckBox("强力置顶 (解决全屏被挡)")
        self.force_topmost_check.setChecked(self.config.get("force_topmost", True))
        self.force_topmost_check.setToolTip("强制将翻译浮窗拉到最顶层")
        perf_form.addRow("", self.force_topmost_check)

        layout.addWidget(perf_group)
        layout.addStretch()
        return tab

    # ── 标签页 4: 关于 ──────────────────────────
    def _create_about_tab(self):
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.setSpacing(16)
        
        version = self.config.get("version", "v1.1.0")
        
        info_label = QLabel(
            f'<div style="font-size: 20px; font-weight: bold; margin-bottom: 10px;">RenpyLens {version}</div>'
            '<div style="line-height: 1.5; color: #ddd; font-size: 18px;">'
            "一款专为 Ren'Py 引擎打造的 AI 悬浮翻译器。<br><br>"
            "<b>开发者：</b>wenliuyuan<br>"
            "<b>开源协议：</b>GPLv3<br>"
            '<b>开源项目：</b><a href="https://github.com/liuyuan-wen/RenpyLens" style="color: #4a9eff; text-decoration: none;">https://github.com/liuyuan-wen/RenpyLens</a><br>'
            "<b>联系微信：</b>renpytrans<br><br>"
            '<span style="color: #aaa; font-size: 18px;">有任何问题，欢迎联系，欢迎提issue。</span><br>'
            '<span style="color: #aaa; font-size: 18px;">免责声明：本软件代码开源，翻译由 AI 大语言模型驱动，结果仅供参考。</span>'
            '</div>'
        )
        info_label.setOpenExternalLinks(True)
        info_label.setWordWrap(True)
        
        vbox.addWidget(info_label)
        vbox.addStretch()
        return tab

    # ── 保存 ──────────────────────────────────────

    def _on_save(self):
        """将所有 GUI 值写回 config dict"""
        self.config["source_lang"] = self.source_lang_combo.currentText().strip()
        self.config["target_lang"] = self.target_lang_combo.currentText().strip()

        api_mappings = [
            ("gemini", self.gemini_url_input, self.gemini_key_input),
            ("zhipu", self.zhipu_url_input, self.zhipu_key_input),
            ("openai", self.openai_url_input, self.openai_key_input),
            ("anthropic", self.anthropic_url_input, self.anthropic_key_input),
            ("deepseek", self.deepseek_url_input, self.deepseek_key_input),
            ("siliconflow", self.siliconflow_url_input, self.siliconflow_key_input),
            ("moonshot", self.moonshot_url_input, self.moonshot_key_input),
            ("xai", self.xai_url_input, self.xai_key_input),
            ("alibaba", self.alibaba_url_input, self.alibaba_key_input),
            ("volcengine", self.volcengine_url_input, self.volcengine_key_input),
            ("custom", self.custom_url_input, self.custom_key_input),
        ]
        for prefix, url_input, key_input in api_mappings:
            self.config[f"{prefix}_url"] = url_input.text().strip()
            self.config[f"{prefix}_api_key"] = key_input.text().strip()

        self.config["builtin_api_key"] = self.builtin_key_input.text().strip()
        self.config["trial_key_url"] = self.trial_url_input.text().strip()
        # 写回每个节点的 URL
        builtin_nodes = self.config.get("builtin_nodes", [])
        current_builtin_url = self.config.get("builtin_url", "")
        for i, (name, url_input) in enumerate(self._builtin_node_inputs):
            new_url = url_input.text().strip()
            if i < len(builtin_nodes):
                old_url = builtin_nodes[i].get("url", "")
                builtin_nodes[i]["url"] = new_url
                # 若当前活动节点的 URL 被修改，同步更新 builtin_url
                if old_url == current_builtin_url:
                    self.config["builtin_url"] = new_url

        self.config["prefetch_count"] = self.prefetch_spin.value()
        self.config["debounce_ms"] = self.debounce_spin.value()
        self.config["socket_port"] = self.socket_port_spin.value()
        self.config["force_topmost"] = self.force_topmost_check.isChecked()
        self.config["show_character_name"] = self.show_character_name_check.isChecked()

        self.config["system_prompt"] = self.sys_prompt_input.toPlainText().strip()
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
