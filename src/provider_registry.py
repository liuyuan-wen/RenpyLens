# -*- coding: utf-8 -*-
"""Data-driven translation provider definitions and configuration access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    protocol: str
    default_url: str = ""
    default_model: str = ""
    recommended_models: tuple[str, ...] = ()
    label_key: str = ""
    chat_path: str = "/v1/chat/completions"


@dataclass(frozen=True)
class ResolvedProvider:
    id: str
    name: str
    protocol: str
    url: str
    api_key: str
    model: str
    profile: str
    recommended_models: tuple[str, ...]
    chat_path: str
    is_custom: bool = False


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("builtin", "Built-in", "builtin", label_key="engine.builtin"),
    ProviderSpec("openai", "OpenAI", "openai", "https://api.openai.com", "gpt-4o-mini", ("gpt-4o-mini", "gpt-4o", "o1-mini", "o3-mini")),
    ProviderSpec("gemini", "Gemini", "gemini", "https://generativelanguage.googleapis.com", "gemini-2.5-flash-lite", ("gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro")),
    ProviderSpec("anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com", "claude-3-5-haiku-20241022", ("claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022")),
    ProviderSpec("deepseek", "DeepSeek", "openai", "https://api.deepseek.com", "deepseek-chat", ("deepseek-chat", "deepseek-reasoner"), chat_path="/chat/completions"),
    ProviderSpec("siliconflow", "SiliconFlow", "openai", "https://api.siliconflow.cn", "Pro/deepseek-ai/DeepSeek-V3", ("Pro/deepseek-ai/DeepSeek-V3", "Pro/deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-7B-Instruct"), label_key="engine.siliconflow"),
    ProviderSpec("moonshot", "Moonshot / Kimi", "openai", "https://api.moonshot.cn", "moonshot-v1-8k", ("moonshot-v1-8k", "moonshot-v1-32k"), label_key="engine.moonshot"),
    ProviderSpec("xai", "xAI (Grok)", "openai", "https://api.x.ai", "grok-2-latest", ("grok-2-latest", "grok-2-vision-latest")),
    ProviderSpec("alibaba", "Alibaba Cloud Model Studio (DashScope)", "openai", "https://dashscope.aliyuncs.com/compatible-mode", "qwen-plus", ("qwen-plus", "qwen-max", "qwen-turbo"), label_key="engine.alibaba"),
    ProviderSpec("volcengine", "Volcengine", "openai", "https://ark.cn-beijing.volces.com", "", ("ep-xxxx", "doubao-pro-32k", "doubao-lite-32k"), label_key="engine.volcengine", chat_path="/api/v3/chat/completions"),
    ProviderSpec("zhipu", "Zhipu AI (GLM)", "zhipu", "https://open.bigmodel.cn", "glm-4.7-flash", ("glm-4.7-flash", "glm-4.7-plus"), label_key="engine.zhipu"),
    ProviderSpec("ollama", "Ollama", "ollama", "http://localhost:11434", "gemma3:4b", ("gemma3:4b", "qwen3:8b")),
    ProviderSpec("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", "openrouter/auto", ("openrouter/auto", "openrouter/free"), label_key="engine.openrouter"),
    ProviderSpec("groq", "Groq", "openai", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", ("openai/gpt-oss-120b", "llama-3.3-70b-versatile"), label_key="engine.groq"),
    ProviderSpec("minimax", "MiniMax", "openai", "https://api.minimaxi.com/v1", "MiniMax-M3", ("MiniMax-M3",), label_key="engine.minimax"),
    ProviderSpec("custom", "Custom", "openai", "http://localhost:8000", "custom-model", ("custom-model",), label_key="engine.custom"),
)

_SPECS_BY_ID = {spec.id: spec for spec in PROVIDER_SPECS}


def get_provider_spec(provider_id: str) -> Optional[ProviderSpec]:
    return _SPECS_BY_ID.get(str(provider_id or ""))


def provider_display_name(spec: ProviderSpec, translate=None) -> str:
    if spec.label_key and translate is not None:
        return str(translate(spec.label_key))
    return spec.label


def custom_channels(config: dict) -> list[dict]:
    channels = config.get("custom_openai_channels", [])
    return channels if isinstance(channels, list) else []


def find_custom_channel(config: dict, provider_id: str) -> Optional[dict]:
    for channel in custom_channels(config):
        if isinstance(channel, dict) and channel.get("id") == provider_id:
            return channel
    return None


def iter_provider_options(config: dict) -> Iterable[tuple[str, str, Optional[ProviderSpec]]]:
    for spec in PROVIDER_SPECS:
        yield spec.id, spec.label, spec
    for channel in custom_channels(config):
        if not isinstance(channel, dict):
            continue
        provider_id = str(channel.get("id", "")).strip()
        name = str(channel.get("name", "")).strip()
        if provider_id and name:
            yield provider_id, name, None


def resolve_provider(config: dict, provider_id: str) -> Optional[ResolvedProvider]:
    spec = get_provider_spec(provider_id)
    if spec is not None:
        recommended = spec.recommended_models
        if spec.id == "ollama":
            configured = config.get("ollama_available_models")
            if isinstance(configured, list):
                recommended = tuple(str(item) for item in configured if str(item).strip())
        return ResolvedProvider(
            id=spec.id,
            name=spec.label,
            protocol=spec.protocol,
            url=str(config.get(f"{spec.id}_url", spec.default_url) or "").strip(),
            api_key=str(config.get(f"{spec.id}_api_key", "") or "").strip(),
            model=str(config.get(f"{spec.id}_model", spec.default_model) or "").strip(),
            profile=spec.id,
            recommended_models=recommended,
            chat_path=spec.chat_path,
        )

    channel = find_custom_channel(config, provider_id)
    if channel is None:
        return None
    profile = str(channel.get("profile", "generic") or "generic")
    profile_spec = get_provider_spec(profile)
    if profile_spec is None or profile_spec.protocol != "openai":
        profile_spec = get_provider_spec("custom")
    return ResolvedProvider(
        id=str(channel.get("id", "")),
        name=str(channel.get("name", "")).strip(),
        protocol="openai",
        url=str(channel.get("url", "") or "").strip(),
        api_key=str(channel.get("api_key", "") or "").strip(),
        model=str(channel.get("model", "") or "").strip(),
        profile=profile,
        recommended_models=profile_spec.recommended_models if profile_spec else (),
        chat_path=profile_spec.chat_path if profile_spec else "/v1/chat/completions",
        is_custom=True,
    )


def update_provider(config: dict, provider_id: str, **values) -> bool:
    spec = get_provider_spec(provider_id)
    if spec is not None:
        for field in ("url", "api_key", "model"):
            if field in values:
                config[f"{provider_id}_{field}"] = values[field]
        return True
    channel = find_custom_channel(config, provider_id)
    if channel is None:
        return False
    for field in ("name", "url", "api_key", "model", "profile"):
        if field in values:
            channel[field] = values[field]
    return True


def provider_connection_signature(config: dict, provider_id: str) -> tuple:
    provider = resolve_provider(config, provider_id)
    if provider is None:
        return ()
    return provider.url, provider.api_key, provider.model
