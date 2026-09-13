# -*- coding: utf-8 -*-
"""Shared shortcut definitions and normalization."""

from __future__ import annotations

from PyQt5.QtGui import QKeySequence


SHORTCUT_ACTIONS = (
    "toggle_overlay",
    "rpgmaker_dialogue_rollback",
    "rpgmaker_save_anywhere",
    "rpgmaker_move_speed",
    "rpgmaker_through",
    "rpgmaker_encounters",
    "rpgmaker_battle_victory",
)

DEFAULT_SHORTCUTS = {
    "toggle_overlay": "Ctrl+Alt+T",
    "rpgmaker_dialogue_rollback": "Ctrl+Z",
    "rpgmaker_save_anywhere": "Ctrl+S",
    "rpgmaker_move_speed": "G",
    "rpgmaker_through": "H",
    "rpgmaker_encounters": "N",
    "rpgmaker_battle_victory": "K",
}

RPGMAKER_SHORTCUT_ACTIONS = SHORTCUT_ACTIONS[1:]


def normalize_shortcut(value: object) -> str:
    """Return a portable, single-chord shortcut string or an empty value."""
    text = str(value or "").strip()
    if not text:
        return ""
    sequence = QKeySequence.fromString(text, QKeySequence.PortableText)
    normalized = sequence.toString(QKeySequence.PortableText).split(",", 1)[0].strip()
    return normalized


def resolve_shortcuts(config: dict | None) -> dict[str, str]:
    saved = (config or {}).get("shortcuts", {})
    saved = saved if isinstance(saved, dict) else {}
    return {
        action: normalize_shortcut(saved[action]) if action in saved else default
        for action, default in DEFAULT_SHORTCUTS.items()
    }


def find_shortcut_conflicts(shortcuts: dict[str, str]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    conflicts = []
    for action in SHORTCUT_ACTIONS:
        value = normalize_shortcut(shortcuts.get(action, ""))
        folded = value.casefold()
        if not folded:
            continue
        if folded in seen:
            conflicts.append((seen[folded], action))
        else:
            seen[folded] = action
    return conflicts


def rpgmaker_shortcuts(config: dict | None) -> dict[str, str]:
    resolved = resolve_shortcuts(config)
    return {action: resolved[action] for action in RPGMAKER_SHORTCUT_ACTIONS}
