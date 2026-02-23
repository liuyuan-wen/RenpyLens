# -*- coding: utf-8 -*-
"""翻译缓存 - 内存 + SQLite 持久化（按游戏隔离）"""

import sqlite3
import os
import threading
from config import CONFIG_DIR

DB_PATH = os.path.join(CONFIG_DIR, "translation_cache.db")


class TranslationCache:
    def __init__(self):
        self._mem_cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._game_id = ""  # 当前游戏标识（exe 文件名）
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        # 迁移：检查旧表结构，如果没有 game_id 列则重建
        cursor = conn.execute("PRAGMA table_info(cache)")
        columns = [row[1] for row in cursor.fetchall()]
        if "game_id" not in columns:
            conn.execute("DROP TABLE IF EXISTS cache")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(game_id TEXT, source TEXT, translation TEXT, "
            "PRIMARY KEY (game_id, source))"
        )
        # 清理上次残留的失败记录
        conn.execute("DELETE FROM cache WHERE translation LIKE '[翻译失败%'")
        conn.commit()
        conn.close()

    def set_game(self, game_exe: str):
        """设置当前游戏，加载对应缓存到内存"""
        game_id = os.path.basename(game_exe) if game_exe else ""
        with self._lock:
            self._game_id = game_id
            self._mem_cache.clear()
        # 从 SQLite 加载该游戏的缓存
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT source, translation FROM cache WHERE game_id = ?",
                (game_id,),
            ).fetchall()
            with self._lock:
                for src, tgt in rows:
                    self._mem_cache[src] = tgt
            conn.close()
            print(f"[Cache] Loaded {len(rows)} cached translations for {game_id or '(no game)'}")
        except Exception:
            pass

    def get(self, text: str) -> str | None:
        with self._lock:
            return self._mem_cache.get(text)

    def is_empty(self) -> bool:
        """当前游戏的缓存是否为空"""
        with self._lock:
            return len(self._mem_cache) == 0

    def put(self, text: str, translation: str):
        with self._lock:
            self._mem_cache[text] = translation
            game_id = self._game_id
        # 异步写入 SQLite
        threading.Thread(target=self._persist, args=(game_id, text, translation), daemon=True).start()

    def _persist(self, game_id: str, text: str, translation: str):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT OR REPLACE INTO cache (game_id, source, translation) VALUES (?, ?, ?)",
                (game_id, text, translation),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def clear(self):
        """清空当前游戏的缓存"""
        with self._lock:
            game_id = self._game_id
            self._mem_cache.clear()
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM cache WHERE game_id = ?", (game_id,))
            conn.commit()
            conn.close()
            print(f"[Cache] Cleared cache for {game_id or '(all)'}")
        except Exception:
            pass
