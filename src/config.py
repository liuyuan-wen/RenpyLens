# -*- coding: utf-8 -*-
"""配置管理"""

import json
import os
import sys

# 当使用 PyInstaller打包成单文件时，__file__ 指向的是每次解压的临时目录 _MEIPASS
# 因此需要将配置和缓存保存到用户目录下，保证持久化
if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RenpyLens")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".renpylens")

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


DEFAULT_CONFIG = {
    "version": "v1.0.0-Beta",
    "translation_engine": "builtin",  # "ollama", "gemini", "zhipu", "builtin"
    "gemini_api_key": "",
    "gemini_url": "https://generativelanguage.googleapis.com",
    "gemini_model": "gemini-2.5-flash-lite",
    "zhipu_api_key": "",
    "zhipu_url": "https://open.bigmodel.cn",
    "zhipu_model": "glm-4.7-flash",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "gemma3:4b",
    "ollama_available_models": ["gemma3:4b", "qwen3:8b"],
    "builtin_url": "http://localhost:8000",
    "builtin_model": "模型1",
    "builtin_api_key": "",
    "builtin_nodes": [
        {"name": "中国大陆节点", "url": "https://frp-bar.com:50588/"},
        {"name": "海外/备用节点", "url": "https://flush-communities-maintained-polyester.trycloudflare.com"}
    ],
    "openai_api_key": "",
    "openai_url": "https://api.openai.com",
    "openai_model": "gpt-4o-mini",
    "anthropic_api_key": "",
    "anthropic_url": "https://api.anthropic.com",
    "anthropic_model": "claude-3-5-haiku-20241022",
    "deepseek_api_key": "",
    "deepseek_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "siliconflow_api_key": "",
    "siliconflow_url": "https://api.siliconflow.cn",
    "siliconflow_model": "Pro/deepseek-ai/DeepSeek-V3",
    "moonshot_api_key": "",
    "moonshot_url": "https://api.moonshot.cn",
    "moonshot_model": "moonshot-v1-8k",
    "xai_api_key": "",
    "xai_url": "https://api.x.ai",
    "xai_model": "grok-2-latest",
    "alibaba_api_key": "",
    "alibaba_url": "https://dashscope.aliyuncs.com/compatible-mode",
    "alibaba_model": "qwen-plus",
    "volcengine_api_key": "",
    "volcengine_url": "https://ark.cn-beijing.volces.com",
    "volcengine_model": "",
    "custom_api_key": "",
    "custom_url": "http://localhost:8000",
    "custom_model": "custom-model",
    "system_prompt": "You are a professional game dialogue translator. Translate the user's message into {target_lang}. Keep it natural and concise for a visual novel. Output ONLY the translated text. No numbering, no quotes, no explanations.",
    "batch_prompt": "You are a professional game dialogue translator. Translate ALL numbered dialogues into {target_lang}. Keep translations natural and concise. Output ONLY translations in the same numbered format [1]...[2]... No extra text.",
    "temperature": 0.3,
    "keep_original_names": True,  # 保留原文人名不翻译
    "source_lang": "English",
    "target_lang": "Chinese",
    "socket_port": 19876,
    "font_size": 22,
    "overlay_opacity": 1.0,
    "overlay_x": 100,
    "overlay_y": 100,
    "overlay_width": 800,
    "prefetch_count": 5,
    "debounce_ms": 100,  # 防抖延迟(毫秒)，快速翻页时只翻译最后停下的句子
    "enable_timing_log": True,  # 是否打印翻译各阶段耗时日志
    "trial_key_url": "https://frp-bar.com:58385/get_trial_key",
}



# 设为 True 则启动时强制使用本文件配置覆盖 config.json（适合手动改代码配置）
PRIORITY_CONFIG_PY = False

def load_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            # 如果优先使用本文件配置，则用 DEFAULT_CONFIG 覆盖 saved
            if PRIORITY_CONFIG_PY:
                merged.update(DEFAULT_CONFIG)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
