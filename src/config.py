# -*- coding: utf-8 -*-
"""Configuration loading and persistence."""

from __future__ import annotations

import json
import os
import sys
import copy

if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RenpyLens")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".renpylens")

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "version": "v1.5.2.1",
    "ui_language": "auto",
    "rpgmaker_qol_games": {},
    "rpgmaker_qol_features": {},
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
    "builtin_url": "https://www-api-1.h53633179.nyat.app:50588/v1",
    "builtin_model": "模型1",
    "builtin_api_key": "",
    "builtin_api_expiry": "",
    "builtin_nodes": [
        {"name": "中国大陆节点", "url": "https://www-api-1.h53633179.nyat.app:50588/v1"},
        {"name": "全球节点", "url": "https://www-api-2.h3043b325.nyat.app:42747/v1"},
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
    "openrouter_api_key": "",
    "openrouter_url": "https://openrouter.ai/api/v1",
    "openrouter_model": "openrouter/auto",
    "groq_api_key": "",
    "groq_url": "https://api.groq.com/openai/v1",
    "groq_model": "openai/gpt-oss-120b",
    "minimax_api_key": "",
    "minimax_url": "https://api.minimaxi.com/v1",
    "minimax_model": "MiniMax-M3",
    "custom_api_key": "",
    "custom_url": "http://localhost:8000",
    "custom_model": "custom-model",
    "custom_openai_channels": [],
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
    "api_timeout_seconds": 120,
    "enable_timing_log": True,
    "trial_key_url": "https://www-map.h53633179.nyat.app:58385/get_trial_key",
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

            # Migrate the built-in service away from legacy endpoints whose TLS
            # certificates do not match their hostnames.  This also updates
            # existing installations instead of only changing fresh defaults.
            builtin_url_migrations = {
                "http://localhost:8000": DEFAULT_CONFIG["builtin_nodes"][0]["url"],
                "https://frp-bar.com:50588": DEFAULT_CONFIG["builtin_nodes"][0]["url"],
                "https://frp-bar.com:50588/": DEFAULT_CONFIG["builtin_nodes"][0]["url"],
                "https://frp-bar.com:50588/v1": DEFAULT_CONFIG["builtin_nodes"][0]["url"],
                "https://frp-cup.com:42747": DEFAULT_CONFIG["builtin_nodes"][1]["url"],
                "https://frp-cup.com:42747/": DEFAULT_CONFIG["builtin_nodes"][1]["url"],
                "https://frp-cup.com:42747/v1": DEFAULT_CONFIG["builtin_nodes"][1]["url"],
                "https://flush-communities-maintained-polyester.trycloudflare.com": DEFAULT_CONFIG["builtin_nodes"][1]["url"],
            }
            current_builtin_url = saved.get("builtin_url")
            if current_builtin_url in builtin_url_migrations:
                saved["builtin_url"] = builtin_url_migrations[current_builtin_url]
            for node in saved.get("builtin_nodes", []):
                if isinstance(node, dict):
                    node["url"] = builtin_url_migrations.get(node.get("url"), node.get("url"))
                    for default_node in DEFAULT_CONFIG["builtin_nodes"]:
                        if node.get("url") == default_node["url"]:
                            node["name"] = default_node["name"]
                            break
            if saved.get("trial_key_url") in {
                "https://frp-bar.com:58385/get_trial_key",
                "https://www-map.h53633179.nyat.app:58385/get_trial_key",
            }:
                saved["trial_key_url"] = DEFAULT_CONFIG["trial_key_url"]

            merged = copy.deepcopy(DEFAULT_CONFIG)
            merged.update(saved)
            merged["version"] = DEFAULT_CONFIG["version"]
            if PRIORITY_CONFIG_PY:
                merged.update(DEFAULT_CONFIG)
            return merged
        except Exception:
            pass
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
