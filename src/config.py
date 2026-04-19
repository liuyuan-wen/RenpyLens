# -*- coding: utf-8 -*-
"""Configuration loading and persistence."""

from __future__ import annotations

import json
import os
import sys

if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RenpyLens")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".renpylens")

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "version": "v1.2.0",
    "translation_engine": "builtin",
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
    "builtin_api_expiry": "",
    "builtin_nodes": [
        {"name": "中国大陆节点", "url": "https://frp-bar.com:50588/"},
        {"name": "海外/备用节点", "url": "https://flush-communities-maintained-polyester.trycloudflare.com"},
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
    "system_prompt": (
        'You are a game localization expert specializing in visual novels. '
        'You are currently localizing the game "{game_title}". '
        "LOCALIZE the following text into {target_lang} so it reads as if it were originally written in {target_lang}. "
        "Key principles: - Dialogue should sound like real people talking. "
        "- Narration should flow like polished prose. "
        "- Dramatic or poetic lines should carry weight and beauty. "
        "- Never translate word-for-word. Adapt idioms, sentence structure, and phrasing to what feels natural in {target_lang}. "
        "- Output ONLY the localized text."
    ),
    "batch_prompt": (
        'You are a game localization expert specializing in visual novels. '
        'You are currently localizing the game "{game_title}". '
        "LOCALIZE ALL numbered lines into {target_lang} so they read as if originally written in {target_lang}. "
        "Dialogue should sound natural, narration should flow like polished prose. "
        "Never translate word-for-word. Output ONLY translations in the same numbered format [1]...[2]... No extra text."
    ),
    "temperature": 0.3,
    "keep_original_names": True,
    "source_lang": "English",
    "target_lang": "简体中文",
    "socket_port": 19876,
    "font_size": 22,
    "overlay_opacity": 1.0,
    "overlay_x": 100,
    "overlay_y": 100,
    "overlay_width": 800,
    "overlay_edit_width": 480,
    "overlay_edit_height": 150,
    "overlay_edit_ui_version": 5,
    "prefetch_count": 5,
    "debounce_ms": 100,
    "bulk_translate_batch_size": 5,
    "bulk_translate_rpm": 60,
    "enable_timing_log": True,
    "trial_key_url": "https://frp-bar.com:58385/get_trial_key",
    "github_repo": "liuyuan-wen/RenpyLens",
    "force_topmost": True,
    "show_character_name": False,
    "workbench_x": 120,
    "workbench_y": 120,
    "workbench_width": 960,
    "workbench_height": 640,
}

PRIORITY_CONFIG_PY = False


def load_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)

            old_system_prompts = [
                "You are a professional game dialogue translator. Translate the user's message into {target_lang}. Keep it natural and concise for a visual novel. Output ONLY the translated text. No numbering, no quotes, no explanations.",
                "You are a game localization expert specializing in visual novels. LOCALIZE the following text into {target_lang} so it reads as if it were originally written in {target_lang}. Key principles: - Dialogue should sound like real people talking. - Narration should flow like polished prose. - Dramatic or poetic lines should carry weight and beauty. - Never translate word-for-word. Adapt idioms, sentence structure, and phrasing to what feels natural in {target_lang}. - Output ONLY the localized text.",
            ]
            old_batch_prompts = [
                "You are a professional game dialogue translator. Translate ALL numbered dialogues into {target_lang}. Keep translations natural and concise. Output ONLY translations in the same numbered format [1]...[2]... No extra text.",
                "You are a game localization expert specializing in visual novels. LOCALIZE ALL numbered lines into {target_lang} so they read as if originally written in {target_lang}. Dialogue should sound natural, narration should flow like polished prose. Never translate word-for-word. Output ONLY translations in the same numbered format [1]...[2]... No extra text.",
            ]

            if saved.get("system_prompt") in old_system_prompts:
                saved["system_prompt"] = DEFAULT_CONFIG["system_prompt"]
            if saved.get("batch_prompt") in old_batch_prompts:
                saved["batch_prompt"] = DEFAULT_CONFIG["batch_prompt"]
            if saved.get("overlay_edit_ui_version", 0) < DEFAULT_CONFIG["overlay_edit_ui_version"]:
                saved["overlay_edit_width"] = DEFAULT_CONFIG["overlay_edit_width"]
                saved["overlay_edit_height"] = DEFAULT_CONFIG["overlay_edit_height"]
                saved["overlay_edit_ui_version"] = DEFAULT_CONFIG["overlay_edit_ui_version"]

            merged = {**DEFAULT_CONFIG, **saved}
            merged["version"] = DEFAULT_CONFIG["version"]
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
