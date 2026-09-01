# -*- coding: utf-8 -*-
"""Runtime internationalization support for RenpyLens."""

from __future__ import annotations

import json
import os
import string
import sys
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QLibraryInfo, QLocale, QObject, QTranslator, pyqtSignal
from PyQt5.QtWidgets import QApplication


SUPPORTED_LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "ru_RU")
LANGUAGE_OPTIONS = (
    ("language.auto", "auto"),
    ("简体中文", "zh_CN"),
    ("繁體中文", "zh_TW"),
    ("English", "en_US"),
    ("日本語", "ja_JP"),
    ("한국어", "ko_KR"),
    ("Русский", "ru_RU"),
)


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
    else:
        root = Path(__file__).resolve().parents[1]
    return root.joinpath(*parts)


def resolve_locale(preference: str = "auto", system_locale: str | None = None) -> str:
    if preference in SUPPORTED_LOCALES:
        return preference
    locale_name = str(system_locale or QLocale.system().name() or "").replace("-", "_")
    language = locale_name.split("_", 1)[0].lower()
    if language == "zh":
        territory = locale_name.upper()
        return "zh_TW" if any(code in territory for code in ("_TW", "_HK", "_MO", "_HANT")) else "zh_CN"
    return {
        "en": "en_US",
        "ja": "ja_JP",
        "ko": "ko_KR",
        "ru": "ru_RU",
    }.get(language, "en_US")


def _field_names(template: str) -> set[str]:
    return {
        field_name.split(".", 1)[0].split("[", 1)[0]
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }


_TRADITIONAL_PHRASES = {
    "软件": "軟體", "文件夹": "資料夾", "文件": "檔案", "缓存": "快取",
    "默认": "預設", "网络": "網路", "程序": "程式", "接口": "介面",
    "数据库": "資料庫", "视频": "影片", "点击": "點擊", "信息": "資訊",
}
_TRADITIONAL_CHARS = str.maketrans({
    "游": "遊", "戏": "戲", "时": "時", "实": "實", "译": "譯", "简": "簡",
    "体": "體", "设": "設", "置": "置", "显": "顯", "示": "示", "语": "語",
    "页": "頁", "签": "籤", "关": "關", "于": "於", "开": "開", "发": "發",
    "协": "協", "议": "議", "项": "項", "欢": "歡", "问": "問", "题": "題",
    "见": "見", "议": "議", "联": "聯", "系": "繫", "组": "組", "图": "圖",
    "载": "載", "败": "敗", "获": "獲", "取": "取", "输": "輸", "入": "入",
    "请": "請", "求": "求", "过": "過", "滤": "濾", "频": "頻", "率": "率",
    "运": "運", "与": "與", "强": "強", "挡": "擋", "将": "將", "对": "對",
    "话": "話", "内": "內", "称": "稱", "动": "動", "态": "態", "编": "編",
    "辑": "輯", "复": "複", "制": "製", "原": "原", "择": "擇", "颜": "顏",
    "色": "色", "宽": "寬", "当": "當", "前": "前", "没": "沒", "记": "記",
    "录": "錄", "条": "條", "类": "類", "别": "別", "无": "無", "暂": "暫",
    "机": "機", "器": "器", "从": "從", "启": "啟", "清": "清", "除": "除",
    "节": "節", "点": "點", "线": "線", "选": "選", "试": "試", "密": "密",
    "钥": "鑰", "隐": "隱", "藏": "藏", "现": "現", "译": "譯", "报": "報",
    "错": "錯", "误": "誤", "达": "達", "进": "進", "暂": "暫", "扫": "掃",
    "描": "描", "离": "離", "线": "線", "读": "讀", "结": "結",
    "果": "果", "储": "儲", "备": "備", "终": "終", "继": "繼", "续": "續",
    "阶": "階", "段": "段", "为": "為", "达": "達", "仅": "僅", "后": "後",
    "会": "會", "应": "應", "这": "這", "个": "個", "数": "數", "据": "據",
    "夹": "夾", "并": "並", "让": "讓", "还": "還", "给": "給", "经": "經",
    "统": "統", "够": "夠", "种": "種", "尽": "盡", "联": "聯", "络": "絡",
})


def _to_traditional(text: str) -> str:
    for source, target in _TRADITIONAL_PHRASES.items():
        text = text.replace(source, target)
    return text.translate(_TRADITIONAL_CHARS)


class LocalizedString(str):
    """A rendered string that retains its message identity for live retranslation."""

    def __new__(cls, value: str, key: str, params: dict[str, Any]):
        instance = super().__new__(cls, value)
        instance.key = key
        instance.params = dict(params)
        return instance


class I18nManager(QObject):
    language_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._preference = "auto"
        self._locale = resolve_locale()
        self._catalogs: dict[str, dict[str, str]] = {}
        self._qt_translators: list[QTranslator] = []

    @property
    def locale(self) -> str:
        return self._locale

    @property
    def preference(self) -> str:
        return self._preference

    def _load_catalog(self, locale: str) -> dict[str, str]:
        if locale not in self._catalogs:
            path = resource_path("assets", "locales", f"{locale}.json")
            with path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid locale catalog: {path}")
            inherited = payload.get("__inherits__")
            messages = payload.get("messages", payload)
            if not isinstance(messages, dict):
                raise ValueError(f"Invalid locale messages: {path}")
            catalog = dict(self._load_catalog(str(inherited))) if inherited else {}
            if locale == "zh_TW" and inherited == "zh_CN":
                catalog = {key: _to_traditional(value) for key, value in catalog.items()}
            catalog.update(
                (str(key), str(value))
                for key, value in messages.items()
                if not str(key).startswith("__")
            )
            self._catalogs[locale] = catalog
        return self._catalogs[locale]

    def tr(self, key: str, **params: Any) -> str:
        candidates = (self._locale, "en_US", "zh_CN")
        template = ""
        for locale in dict.fromkeys(candidates):
            try:
                template = self._load_catalog(locale).get(key, "")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"[I18n] Failed to load {locale}: {exc}", file=sys.__stderr__)
            if template:
                break
        if not template:
            print(f"[I18n] Missing message key: {key}", file=sys.__stderr__)
            template = key
        try:
            return LocalizedString(template.format(**params), key, params)
        except (KeyError, ValueError):
            print(
                f"[I18n] Invalid parameters for {key}; expected {_field_names(template)}",
                file=sys.__stderr__,
            )
            return LocalizedString(template, key, params)

    def set_language(self, preference: str, app: QApplication | None = None) -> str:
        normalized = preference if preference in SUPPORTED_LOCALES or preference == "auto" else "auto"
        locale = resolve_locale(normalized)
        changed = locale != self._locale or normalized != self._preference
        self._preference = normalized
        self._locale = locale
        if app is not None:
            self._install_qt_translations(app)
        if changed:
            self.language_changed.emit(locale)
        return locale

    def _install_qt_translations(self, app: QApplication):
        for translator in self._qt_translators:
            app.removeTranslator(translator)
        self._qt_translators.clear()

        qt_locale = self._locale
        translation_dirs = [
            resource_path("assets", "qt_translations"),
            Path(QLibraryInfo.location(QLibraryInfo.TranslationsPath)),
        ]
        for prefix in ("qtbase", "qt"):
            translator = QTranslator(self)
            loaded = False
            for directory in translation_dirs:
                if translator.load(f"{prefix}_{qt_locale}.qm", os.fspath(directory)):
                    loaded = True
                    break
            if loaded:
                app.installTranslator(translator)
                self._qt_translators.append(translator)


_manager = I18nManager()


def manager() -> I18nManager:
    return _manager


def tr(key: str, **params: Any) -> str:
    return _manager.tr(key, **params)


def localized_node_name(name: str) -> str:
    key = {
        "中国大陆节点": "node.china_mainland",
        "全球节点": "node.global",
        "海外节点": "node.global",
        "海外/备用节点": "node.global",
        "海外/备用节点（暂时不可用）": "node.global",
        "海外/备用节点（暂时失效）": "node.global",
    }.get(str(name or ""))
    return tr(key) if key else str(name or "")


def set_language(preference: str, app: QApplication | None = None) -> str:
    return _manager.set_language(preference, app)


def validate_catalogs() -> list[str]:
    """Return catalog consistency errors; used by tests and packaging checks."""
    errors: list[str] = []
    catalogs: dict[str, dict[str, str]] = {}
    for locale in SUPPORTED_LOCALES:
        try:
            catalogs[locale] = _manager._load_catalog(locale)
        except Exception as exc:
            errors.append(f"{locale}: {exc}")
    if not catalogs:
        return errors
    expected = set(catalogs.get("zh_CN", {}))
    for locale, catalog in catalogs.items():
        missing = sorted(expected - set(catalog))
        extra = sorted(set(catalog) - expected)
        if missing:
            errors.append(f"{locale}: missing keys {missing}")
        if extra:
            errors.append(f"{locale}: extra keys {extra}")
        for key in expected & set(catalog):
            if not catalog[key].strip():
                errors.append(f"{locale}: empty value for {key}")
            elif _field_names(catalog[key]) != _field_names(catalogs["zh_CN"][key]):
                errors.append(f"{locale}: placeholder mismatch for {key}")
    return errors
