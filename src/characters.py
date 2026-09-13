"""Game-local character identities and editable translation context."""

from contextlib import closing


def character_key(character_id: str, speaker: str) -> str:
    if not speaker:
        return ""
    return str(character_id or "").strip() or "name:" + speaker


class CharacterRepository:
    """Uses the translation database, independently of translation cache lifetime."""

    def __init__(self, connect):
        self._connect = connect
        with closing(connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS characters ("
                "game_id TEXT NOT NULL, character_id TEXT NOT NULL, "
                "display_name TEXT NOT NULL, gender TEXT NOT NULL DEFAULT 'unknown', "
                "notes TEXT NOT NULL DEFAULT '', PRIMARY KEY (game_id, character_id))"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
            if "name_source" not in columns:
                # Existing profiles were all discovered at runtime.
                conn.execute("ALTER TABLE characters ADD COLUMN name_source TEXT NOT NULL DEFAULT 'runtime'")

    def import_static(self, game_id, items):
        """Seed names and unresolved variables without overwriting runtime observations."""
        if not game_id or not items:
            return
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO characters (game_id, character_id, display_name, name_source) "
                "VALUES (?, ?, ?, 'static') ON CONFLICT(game_id, character_id) DO UPDATE SET "
                "display_name=excluded.display_name WHERE characters.name_source='static'",
                [(game_id, item["character_id"], item["who"]) for item in items],
            )

    def observe(self, game_id, items, *, current=False):
        if not game_id:
            return
        records = {}
        for item in items:
            speaker = str(item.get("who") or item.get("speaker") or "").strip()
            key = character_key(item.get("character_id", ""), speaker)
            if key:
                records[key] = speaker
        if not records:
            return
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO characters (game_id, character_id, display_name, name_source) VALUES (?, ?, ?, 'runtime') "
                "ON CONFLICT(game_id, character_id) DO UPDATE SET "
                "display_name=excluded.display_name, name_source='runtime'"
                + ("" if current else " WHERE characters.name_source='static' OR characters.display_name=''"),
                [(game_id, key, name) for key, name in records.items()],
            )

    def list(self, game_id):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT character_id, display_name, gender, notes FROM characters "
                "WHERE game_id=? ORDER BY rowid", (game_id,),
            ).fetchall()
        return [dict(zip(("character_id", "display_name", "gender", "notes"), row)) for row in rows]

    def update(self, game_id, character_id, gender, notes):
        if gender not in ("unknown", "male", "female", "other"):
            raise ValueError("Invalid character gender")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE characters SET gender=?, notes=? WHERE game_id=? AND character_id=?",
                (gender, notes, game_id, character_id),
            )

    def context(self, game_id, items):
        profiles = {p["character_id"]: p for p in self.list(game_id)}
        result = []
        for item in items:
            speaker = str(item.get("who") or item.get("speaker") or "").strip()
            key = character_key(item.get("character_id", ""), speaker)
            profile = profiles.get(key, {})
            result.append({**profile, "character_id": key, "speaker": speaker})
        return result
