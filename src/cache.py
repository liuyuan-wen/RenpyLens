# -*- coding: utf-8 -*-
"""Translation repository with in-memory cache and SQLite persistence."""

from __future__ import annotations

import ast
import os
import re
import sqlite3
import threading
import time
from typing import Any

from config import CONFIG_DIR

DB_PATH = os.path.join(CONFIG_DIR, "translation_cache.db")

ENTRY_TYPE_DIALOGUE = "dialogue"
ENTRY_TYPE_CHOICE = "choice"


def normalize_speaker_name(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        parts = []
        seen = set()
        for item in value:
            normalized = normalize_speaker_name(item)
            if normalized and normalized not in seen:
                seen.add(normalized)
                parts.append(normalized)
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return " / ".join(parts)

    text = str(value or "").strip()
    if not text or text in {"[]", "()", "{}", "None"}:
        return ""

    if len(text) >= 2 and text[0] in "[(" and text[-1] in "])":
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return normalize_speaker_name(parsed)

    text = re.sub(r"\s+", " ", text).strip()
    return text


class TranslationCache:
    def __init__(self):
        self._mem_entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._game_id = ""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(DB_PATH)

    def _now(self) -> int:
        return int(time.time())

    def _init_db(self):
        conn = self._connect()
        cursor = conn.execute("PRAGMA table_info(cache)")
        columns = [row[1] for row in cursor.fetchall()]

        if not columns:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "game_id TEXT NOT NULL, "
                "source TEXT NOT NULL, "
                "translation TEXT NOT NULL DEFAULT '', "
                "entry_type TEXT NOT NULL DEFAULT 'dialogue', "
                "speaker TEXT NOT NULL DEFAULT '', "
                "is_manual INTEGER NOT NULL DEFAULT 0, "
                "created_at INTEGER NOT NULL DEFAULT 0, "
                "updated_at INTEGER NOT NULL DEFAULT 0, "
                "last_seen_at INTEGER NOT NULL DEFAULT 0, "
                "seen_count INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (game_id, source)"
                ")"
            )
        else:
            add_columns = {
                "entry_type": "TEXT NOT NULL DEFAULT 'dialogue'",
                "speaker": "TEXT NOT NULL DEFAULT ''",
                "is_manual": "INTEGER NOT NULL DEFAULT 0",
                "created_at": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "INTEGER NOT NULL DEFAULT 0",
                "last_seen_at": "INTEGER NOT NULL DEFAULT 0",
                "seen_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in add_columns.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE cache ADD COLUMN {name} {definition}")

        now = self._now()
        conn.execute("UPDATE cache SET translation = COALESCE(translation, '')")
        conn.execute(
            "UPDATE cache SET entry_type = ? "
            "WHERE COALESCE(entry_type, '') = ''",
            (ENTRY_TYPE_DIALOGUE,),
        )
        conn.execute("UPDATE cache SET speaker = COALESCE(speaker, '')")
        conn.execute("UPDATE cache SET is_manual = COALESCE(is_manual, 0)")
        conn.execute(
            "UPDATE cache SET created_at = ? "
            "WHERE COALESCE(created_at, 0) = 0",
            (now,),
        )
        conn.execute(
            "UPDATE cache SET updated_at = created_at "
            "WHERE COALESCE(updated_at, 0) = 0",
        )
        conn.execute(
            "UPDATE cache SET last_seen_at = updated_at "
            "WHERE COALESCE(last_seen_at, 0) = 0",
        )
        conn.execute(
            "UPDATE cache SET seen_count = 1 "
            "WHERE COALESCE(seen_count, 0) = 0",
        )
        conn.execute("DELETE FROM cache WHERE translation LIKE '[翻译失败%'")
        conn.commit()
        conn.close()

    def _normalize_entry(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "game_id": str(row[0] or ""),
            "source": str(row[1] or ""),
            "translation": str(row[2] or ""),
            "entry_type": str(row[3] or ENTRY_TYPE_DIALOGUE),
            "speaker": normalize_speaker_name(row[4]),
            "is_manual": bool(row[5]),
            "created_at": int(row[6] or 0),
            "updated_at": int(row[7] or 0),
            "last_seen_at": int(row[8] or 0),
            "seen_count": int(row[9] or 0),
        }

    def _clone_entry(self, entry: dict[str, Any] | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        return dict(entry)

    def _persist_entry(self, entry: dict[str, Any]):
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO cache ("
                "game_id, source, translation, entry_type, speaker, is_manual, "
                "created_at, updated_at, last_seen_at, seen_count"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["game_id"],
                    entry["source"],
                    entry["translation"],
                    entry["entry_type"],
                    entry["speaker"],
                    int(bool(entry["is_manual"])),
                    entry["created_at"],
                    entry["updated_at"],
                    entry["last_seen_at"],
                    entry["seen_count"],
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _persist_entries(self, entries: list[dict[str, Any]]):
        snapshots = [self._clone_entry(entry) for entry in (entries or []) if entry]
        if not snapshots:
            return
        try:
            conn = self._connect()
            conn.executemany(
                "INSERT OR REPLACE INTO cache ("
                "game_id, source, translation, entry_type, speaker, is_manual, "
                "created_at, updated_at, last_seen_at, seen_count"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        entry["game_id"],
                        entry["source"],
                        entry["translation"],
                        entry["entry_type"],
                        entry["speaker"],
                        int(bool(entry["is_manual"])),
                        entry["created_at"],
                        entry["updated_at"],
                        entry["last_seen_at"],
                        entry["seen_count"],
                    )
                    for entry in snapshots
                ],
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _persist_async(self, entry: dict[str, Any]):
        snapshot = self._clone_entry(entry)
        if not snapshot:
            return
        threading.Thread(target=self._persist_entry, args=(snapshot,), daemon=True).start()

    def _build_entry(
        self,
        source: str,
        translation: str = "",
        entry_type: str = ENTRY_TYPE_DIALOGUE,
        speaker: str = "",
        is_manual: bool = False,
        now: int | None = None,
    ) -> dict[str, Any]:
        timestamp = self._now() if now is None else now
        return {
            "game_id": self._game_id,
            "source": source,
            "translation": translation,
            "entry_type": entry_type or ENTRY_TYPE_DIALOGUE,
            "speaker": normalize_speaker_name(speaker),
            "is_manual": bool(is_manual),
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_seen_at": timestamp,
            "seen_count": 1,
        }

    def _unwrap_outer_quotes(self, text: str) -> str:
        normalized = str(text or "").strip()
        if len(normalized) < 2:
            return normalized

        quote_pairs = (
            ('"', '"'),
            ("'", "'"),
            ("\u201c", "\u201d"),
            ("\u2018", "\u2019"),
        )

        while len(normalized) >= 2:
            changed = False
            for left, right in quote_pairs:
                if normalized.startswith(left) and normalized.endswith(right):
                    inner = normalized[len(left): len(normalized) - len(right)].strip()
                    if inner:
                        normalized = inner
                        changed = True
                    break
            if not changed:
                break
        return normalized

    def _source_candidates(self, text: str) -> list[str]:
        source = str(text or "").strip()
        if not source:
            return []

        candidates = [source]
        unwrapped = self._unwrap_outer_quotes(source)
        if unwrapped and unwrapped not in candidates:
            candidates.append(unwrapped)
        return candidates

    def _resolve_entry_key(self, text: str) -> str | None:
        candidates = self._source_candidates(text)
        if not candidates:
            return None

        fallback = None
        for candidate in candidates:
            entry = self._mem_entries.get(candidate)
            if not entry:
                continue

            translation = str(entry.get("translation") or "").strip()
            if translation or entry.get("is_manual"):
                return candidate

            if fallback is None:
                fallback = candidate

        return fallback

    def set_game(self, game_exe: str):
        """Set current game and load all existing entries into memory."""
        game_id = os.path.basename(game_exe) if game_exe else ""
        with self._lock:
            self._game_id = game_id
            self._mem_entries.clear()
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT game_id, source, translation, entry_type, speaker, is_manual, "
                "created_at, updated_at, last_seen_at, seen_count "
                "FROM cache WHERE game_id = ?",
                (game_id,),
            ).fetchall()
            entries = {row[1]: self._normalize_entry(row) for row in rows}
            with self._lock:
                self._mem_entries.update(entries)
            conn.close()
            print(f"[Cache] Loaded {len(rows)} entries for {game_id or '(no game)'}")
        except Exception:
            pass

    def get(self, text: str) -> str | None:
        with self._lock:
            key = self._resolve_entry_key(text)
            entry = self._mem_entries.get(key) if key else None
            if not entry:
                return None
            translation = str(entry.get("translation") or "").strip()
            return translation or None

    def get_entry(self, text: str) -> dict[str, Any] | None:
        with self._lock:
            key = self._resolve_entry_key(text)
            return self._clone_entry(self._mem_entries.get(key) if key else None)

    def has_translation_or_manual(self, text: str) -> bool:
        with self._lock:
            key = self._resolve_entry_key(text)
            entry = self._mem_entries.get(key) if key else None
            if not entry:
                return False
            translation = str(entry.get("translation") or "").strip()
            return bool(translation or entry.get("is_manual"))

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._mem_entries) == 0

    def has_manual_translation(self, text: str) -> bool:
        with self._lock:
            key = self._resolve_entry_key(text)
            entry = self._mem_entries.get(key) if key else None
            return bool(entry and entry.get("is_manual"))

    def mark_seen(self, text: str, entry_type: str = ENTRY_TYPE_DIALOGUE, speaker: str = ""):
        text = str(text or "").strip()
        speaker = normalize_speaker_name(speaker)
        if not text:
            return

        now = self._now()
        with self._lock:
            key = self._resolve_entry_key(text) or text
            entry = self._mem_entries.get(key)
            if entry is None:
                entry = self._build_entry(
                    source=text,
                    translation="",
                    entry_type=entry_type,
                    speaker=speaker,
                    is_manual=False,
                    now=now,
                )
                self._mem_entries[text] = entry
            else:
                entry["entry_type"] = entry_type or entry.get("entry_type") or ENTRY_TYPE_DIALOGUE
                if speaker and not entry.get("speaker"):
                    entry["speaker"] = speaker
                entry["last_seen_at"] = now
                entry["seen_count"] = int(entry.get("seen_count") or 0) + 1
            snapshot = self._clone_entry(entry)
        self._persist_async(snapshot)

    def save_machine_translation_if_absent(
        self,
        text: str,
        translation: str,
        entry_type: str = ENTRY_TYPE_DIALOGUE,
        speaker: str = "",
    ) -> bool:
        text = str(text or "").strip()
        translation = str(translation or "").strip()
        speaker = normalize_speaker_name(speaker)
        if not text or not translation:
            return False

        now = self._now()
        stored = False
        with self._lock:
            key = self._resolve_entry_key(text) or text
            entry = self._mem_entries.get(key)
            if entry is None:
                entry = self._build_entry(
                    source=text,
                    translation=translation,
                    entry_type=entry_type,
                    speaker=speaker,
                    is_manual=False,
                    now=now,
                )
                self._mem_entries[text] = entry
                stored = True
            elif entry.get("is_manual"):
                snapshot = None
            elif not str(entry.get("translation") or "").strip():
                entry["translation"] = translation
                entry["entry_type"] = entry_type or entry.get("entry_type") or ENTRY_TYPE_DIALOGUE
                if speaker:
                    entry["speaker"] = speaker
                entry["updated_at"] = now
                entry["last_seen_at"] = max(int(entry.get("last_seen_at") or 0), now)
                entry["seen_count"] = max(int(entry.get("seen_count") or 0), 1)
                stored = True
            else:
                entry["entry_type"] = entry_type or entry.get("entry_type") or ENTRY_TYPE_DIALOGUE
                if speaker and not entry.get("speaker"):
                    entry["speaker"] = speaker
            snapshot = self._clone_entry(entry) if entry and not entry.get("is_manual") else None

        if snapshot:
            self._persist_async(snapshot)
        return stored

    def save_machine_translations_if_absent(
        self,
        items: list[dict[str, Any]],
    ) -> set[str]:
        now = self._now()
        persisted_entries: list[dict[str, Any]] = []
        covered_sources: set[str] = set()

        with self._lock:
            for item in items or []:
                text = str((item or {}).get("source") or (item or {}).get("text") or "").strip()
                translation = str((item or {}).get("translation") or "").strip()
                entry_type = str((item or {}).get("entry_type") or ENTRY_TYPE_DIALOGUE)
                speaker = normalize_speaker_name((item or {}).get("speaker", ""))
                if not text or not translation:
                    continue

                key = self._resolve_entry_key(text) or text
                entry = self._mem_entries.get(key)
                changed = False

                if entry is None:
                    entry = self._build_entry(
                        source=text,
                        translation=translation,
                        entry_type=entry_type,
                        speaker=speaker,
                        is_manual=False,
                        now=now,
                    )
                    self._mem_entries[text] = entry
                    changed = True
                elif entry.get("is_manual"):
                    pass
                elif not str(entry.get("translation") or "").strip():
                    entry["translation"] = translation
                    entry["entry_type"] = entry_type or entry.get("entry_type") or ENTRY_TYPE_DIALOGUE
                    if speaker:
                        entry["speaker"] = speaker
                    entry["updated_at"] = now
                    entry["last_seen_at"] = max(int(entry.get("last_seen_at") or 0), now)
                    entry["seen_count"] = max(int(entry.get("seen_count") or 0), 1)
                    changed = True
                else:
                    if entry_type and entry_type != entry.get("entry_type"):
                        entry["entry_type"] = entry_type
                        changed = True
                    if speaker and not entry.get("speaker"):
                        entry["speaker"] = speaker
                        changed = True

                if entry and (str(entry.get("translation") or "").strip() or entry.get("is_manual")):
                    covered_sources.add(text)

                if changed and entry is not None:
                    persisted_entries.append(self._clone_entry(entry))

        self._persist_entries(persisted_entries)
        return covered_sources

    def save_manual_translation(
        self,
        text: str,
        translation: str,
        entry_type: str = ENTRY_TYPE_DIALOGUE,
        speaker: str = "",
    ) -> dict[str, Any] | None:
        text = str(text or "").strip()
        translation = str(translation or "").strip()
        speaker = normalize_speaker_name(speaker)
        if not text:
            return None

        now = self._now()
        with self._lock:
            key = self._resolve_entry_key(text) or text
            entry = self._mem_entries.get(key)
            if entry is None:
                entry = self._build_entry(
                    source=text,
                    translation=translation,
                    entry_type=entry_type,
                    speaker=speaker,
                    is_manual=True,
                    now=now,
                )
                self._mem_entries[text] = entry
            else:
                entry["translation"] = translation
                entry["entry_type"] = entry_type or entry.get("entry_type") or ENTRY_TYPE_DIALOGUE
                if speaker:
                    entry["speaker"] = speaker
                entry["is_manual"] = True
                entry["updated_at"] = now
                entry["last_seen_at"] = now
                entry["seen_count"] = max(int(entry.get("seen_count") or 0), 1)
            snapshot = self._clone_entry(entry)
        self._persist_async(snapshot)
        return snapshot

    def list_recent_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            entries = [self._clone_entry(entry) for entry in self._mem_entries.values()]
        entries = [entry for entry in entries if entry]
        entries.sort(
            key=lambda entry: (
                int(entry.get("last_seen_at") or 0),
                int(entry.get("updated_at") or 0),
            ),
            reverse=True,
        )
        return entries[:limit]

    def clear(self):
        """Clear all entries for the current game."""
        with self._lock:
            game_id = self._game_id
            self._mem_entries.clear()
        try:
            conn = self._connect()
            conn.execute("DELETE FROM cache WHERE game_id = ?", (game_id,))
            conn.commit()
            conn.close()
            print(f"[Cache] Cleared cache for {game_id or '(all)'}")
        except Exception:
            pass
