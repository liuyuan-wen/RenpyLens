# -*- coding: utf-8 -*-
"""Engine detection and installation adapters used by RenpyLens."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from injector import inject_hook as inject_renpy_hook
from injector import is_renpy_game, remove_hook as remove_renpy_hook


ENGINE_RENPY = "renpy"
ENGINE_RPGMAKER_MV = "rpgmaker_mv"
ENGINE_RPGMAKER_MZ = "rpgmaker_mz"

RPGMAKER_MARKER_BEGIN = "/* RENPYLENS_BRIDGE_BEGIN */"
RPGMAKER_MARKER_END = "/* RENPYLENS_BRIDGE_END */"
RPGMAKER_QOL_MARKER_BEGIN = "/* RPGMAKER_QOL_BEGIN */"
RPGMAKER_QOL_MARKER_END = "/* RPGMAKER_QOL_END */"
EDORIAM_QOL_MARKER_BEGIN = "/* EDORIAM_QOL_BEGIN */"
EDORIAM_QOL_MARKER_END = "/* EDORIAM_QOL_END */"
RPGMAKER_BRIDGE_NAME = "RenpyLensBridge"
RPGMAKER_STATE_DIR = ".renpylens"
RPGMAKER_MANIFEST = "install.json"


@dataclass(frozen=True)
class EngineCapabilities:
    realtime_text: bool = True
    speaker: bool = True
    choices: bool = True
    prefetch: bool = True
    offline_scan: bool = False
    runtime_scan: bool = False


@dataclass(frozen=True)
class GameTarget:
    exe_path: str
    root_dir: str
    content_dir: str
    engine: str
    engine_version: str
    title: str
    cache_id: str
    capabilities: EngineCapabilities

    @property
    def engine_label(self) -> str:
        labels = {
            ENGINE_RENPY: "Ren'Py",
            ENGINE_RPGMAKER_MV: "RPG Maker MV",
            ENGINE_RPGMAKER_MZ: "RPG Maker MZ",
        }
        label = labels.get(self.engine, self.engine)
        return f"{label} {self.engine_version}".strip()


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _cache_id(engine: str, exe_path: str) -> str:
    identity = f"{engine}\0{_normalized_path(exe_path)}".encode("utf-8")
    return f"v2:{engine}:{hashlib.sha256(identity).hexdigest()[:24]}"


def _read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _rpgmaker_content_dir(exe_path: str) -> tuple[Path | None, str | None]:
    root = Path(exe_path).resolve().parent
    for content in (root, root / "www"):
        js_dir = content / "js"
        if (js_dir / "rmmz_core.js").is_file():
            return content, ENGINE_RPGMAKER_MZ
        if (js_dir / "rpg_core.js").is_file():
            return content, ENGINE_RPGMAKER_MV
    return None, None


def _engine_version(content_dir: Path, engine: str) -> str:
    core_name = "rmmz_core.js" if engine == ENGINE_RPGMAKER_MZ else "rpg_core.js"
    try:
        content = (content_dir / "js" / core_name).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"RPGMAKER_VERSION(?:['\"]\]|\s*)\s*=\s*['\"]([^'\"]+)", content)
    return match.group(1).strip() if match else ""


def _game_title(content_dir: Path, exe_path: str) -> str:
    try:
        system = _read_json(content_dir / "data" / "System.json")
        title = str(system.get("gameTitle") or "").strip()
        if title:
            return title
    except (OSError, ValueError, TypeError):
        pass
    return Path(exe_path).stem or "Unknown Game"


def detect_game(exe_path: str) -> GameTarget | None:
    if not exe_path or not os.path.isfile(exe_path):
        return None

    if is_renpy_game(exe_path):
        root = str(Path(exe_path).resolve().parent)
        return GameTarget(
            exe_path=str(Path(exe_path).resolve()),
            root_dir=root,
            content_dir=str(Path(root) / "game"),
            engine=ENGINE_RENPY,
            engine_version="",
            title=Path(exe_path).stem,
            cache_id=_cache_id(ENGINE_RENPY, exe_path),
            capabilities=EngineCapabilities(runtime_scan=True),
        )

    content_dir, engine = _rpgmaker_content_dir(exe_path)
    if content_dir is None or engine is None:
        return None

    # Validated against MV 1.6.1 and MZ 1.8.1; compatible public APIs are used for other versions.
    return GameTarget(
        exe_path=str(Path(exe_path).resolve()),
        root_dir=str(Path(exe_path).resolve().parent),
        content_dir=str(content_dir),
        engine=engine,
        engine_version=_engine_version(content_dir, engine),
        title=_game_title(content_dir, exe_path),
        cache_id=_cache_id(engine, exe_path),
        capabilities=EngineCapabilities(offline_scan=True),
    )


def is_supported_game(exe_path: str) -> bool:
    return detect_game(exe_path) is not None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes):
    temp = path.with_name(f".{path.name}.renpylens-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _remove_named_marker_block(text: str, begin: str, end: str) -> str:
    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end) + r"(?:\r?\n)?",
        flags=re.DOTALL,
    )
    return pattern.sub("", text)


def _remove_marker_block(text: str) -> str:
    return _remove_named_marker_block(text, RPGMAKER_MARKER_BEGIN, RPGMAKER_MARKER_END)


def _remove_legacy_qol_blocks(text: str) -> str:
    text = _remove_named_marker_block(
        text, RPGMAKER_QOL_MARKER_BEGIN, RPGMAKER_QOL_MARKER_END
    )
    return _remove_named_marker_block(
        text, EDORIAM_QOL_MARKER_BEGIN, EDORIAM_QOL_MARKER_END
    )


def _render_plugin_registration(
    socket_port: int,
    session_id: str,
    rpgmaker_qol_enabled: bool = False,
    rpgmaker_qol_locale: str = "en_US",
    rpgmaker_qol_features: dict[str, bool] | None = None,
) -> str:
    features = {
        str(key): bool(value)
        for key, value in (rpgmaker_qol_features or {}).items()
    }
    parameters = {
        "socketPort": str(int(socket_port)),
        "sessionId": str(session_id),
        "protocolVersion": "2",
        "qolEnabled": "true" if rpgmaker_qol_enabled else "false",
        "qolLocale": str(rpgmaker_qol_locale or "en_US"),
        "qolFeatures": json.dumps(features, ensure_ascii=False, separators=(",", ":")),
    }
    entry = {
        "name": RPGMAKER_BRIDGE_NAME,
        "status": True,
        "description": "RenpyLens runtime bridge.",
        "parameters": parameters,
    }
    return (
        f"{RPGMAKER_MARKER_BEGIN}\n"
        f"$plugins.push({json.dumps(entry, ensure_ascii=False, separators=(',', ':'))});\n"
        f"{RPGMAKER_MARKER_END}\n"
    )


def _install_rpgmaker_bridge(
    target: GameTarget,
    bridge_source: str,
    socket_port: int,
    session_id: str,
    rpgmaker_qol_enabled: bool = False,
    rpgmaker_qol_locale: str = "en_US",
    rpgmaker_qol_features: dict[str, bool] | None = None,
) -> tuple[bool, str]:
    content_dir = Path(target.content_dir)
    plugins_js = content_dir / "js" / "plugins.js"
    plugin_dir = content_dir / "js" / "plugins"
    bridge_dest = plugin_dir / f"{RPGMAKER_BRIDGE_NAME}.js"
    state_dir = content_dir / RPGMAKER_STATE_DIR
    manifest_path = state_dir / RPGMAKER_MANIFEST
    backup_path = state_dir / "plugins.js.original"

    if not plugins_js.is_file():
        return False, f"RPG Maker plugin registry not found: {plugins_js}"
    if not os.path.isfile(bridge_source):
        return False, f"RPG Maker bridge source not found: {bridge_source}"

    try:
        original_bytes = plugins_js.read_bytes()
        backup_bytes = backup_path.read_bytes() if backup_path.is_file() else original_bytes
        original_text = original_bytes.decode("utf-8-sig")
        clean_text = _remove_legacy_qol_blocks(_remove_marker_block(original_text))
        registration = _render_plugin_registration(
            socket_port,
            session_id,
            rpgmaker_qol_enabled,
            rpgmaker_qol_locale,
            rpgmaker_qol_features,
        )
        newline = "\r\n" if "\r\n" in original_text else "\n"
        if clean_text and not clean_text.endswith(("\n", "\r")):
            clean_text += newline
        patched_text = clean_text + registration.replace("\n", newline)
        bom = b"\xef\xbb\xbf" if original_bytes.startswith(b"\xef\xbb\xbf") else b""
        patched_bytes = bom + patched_text.encode("utf-8")
        bridge_bytes = Path(bridge_source).read_bytes()

        state_dir.mkdir(exist_ok=True)
        plugin_dir.mkdir(exist_ok=True)
        if not backup_path.is_file():
            backup_path.write_bytes(backup_bytes)
        _atomic_write(bridge_dest, bridge_bytes)
        _atomic_write(plugins_js, patched_bytes)

        manifest = {
            "engine": target.engine,
            "original_sha256": _sha256(backup_bytes),
            "patched_sha256": _sha256(patched_bytes),
            "bridge_sha256": _sha256(bridge_bytes),
            "session_id": session_id,
            "rpgmaker_qol_enabled": bool(rpgmaker_qol_enabled),
            "rpgmaker_qol_locale": str(rpgmaker_qol_locale or "en_US"),
            "rpgmaker_qol_features": {
                str(key): bool(value)
                for key, value in (rpgmaker_qol_features or {}).items()
            },
        }
        _atomic_write(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return True, f"Injected RPG Maker bridge: {bridge_dest}"
    except Exception as exc:
        try:
            if backup_path.is_file():
                _atomic_write(plugins_js, backup_path.read_bytes())
        except Exception:
            pass
        return False, f"Failed to install RPG Maker bridge: {exc}"


def _uninstall_rpgmaker_bridge(target: GameTarget) -> tuple[bool, str]:
    content_dir = Path(target.content_dir)
    plugins_js = content_dir / "js" / "plugins.js"
    bridge_dest = content_dir / "js" / "plugins" / f"{RPGMAKER_BRIDGE_NAME}.js"
    state_dir = content_dir / RPGMAKER_STATE_DIR
    manifest_path = state_dir / RPGMAKER_MANIFEST
    backup_path = state_dir / "plugins.js.original"

    try:
        manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
        current_bytes = plugins_js.read_bytes() if plugins_js.is_file() else b""
        patched_hash = str(manifest.get("patched_sha256") or "")
        if backup_path.is_file() and patched_hash and _sha256(current_bytes) == patched_hash:
            _atomic_write(plugins_js, backup_path.read_bytes())
        elif plugins_js.is_file():
            current_text = current_bytes.decode("utf-8-sig")
            cleaned_text = _remove_marker_block(current_text)
            if cleaned_text != current_text:
                bom = b"\xef\xbb\xbf" if current_bytes.startswith(b"\xef\xbb\xbf") else b""
                _atomic_write(plugins_js, bom + cleaned_text.encode("utf-8"))

        expected_bridge_hash = str(manifest.get("bridge_sha256") or "")
        if bridge_dest.is_file():
            if not expected_bridge_hash or _sha256(bridge_dest.read_bytes()) == expected_bridge_hash:
                bridge_dest.unlink()

        for path in (manifest_path, backup_path):
            if path.exists():
                path.unlink()
        if state_dir.is_dir() and not any(state_dir.iterdir()):
            state_dir.rmdir()
        return True, "RPG Maker bridge removed"
    except Exception as exc:
        return False, f"Failed to remove RPG Maker bridge: {exc}"


def install_hook(
    target: GameTarget,
    renpy_hook_source: str,
    rpgmaker_bridge_source: str,
    socket_port: int,
    session_id: str = "",
    rpgmaker_qol_enabled: bool = False,
    rpgmaker_qol_locale: str = "en_US",
    rpgmaker_qol_features: dict[str, bool] | None = None,
) -> tuple[bool, str]:
    if target.engine == ENGINE_RENPY:
        return inject_renpy_hook(target.exe_path, renpy_hook_source, socket_port)
    session_id = session_id or uuid.uuid4().hex
    return _install_rpgmaker_bridge(
        target,
        rpgmaker_bridge_source,
        socket_port,
        session_id,
        rpgmaker_qol_enabled,
        rpgmaker_qol_locale,
        rpgmaker_qol_features,
    )


def uninstall_hook(target: GameTarget) -> tuple[bool, str]:
    if target.engine == ENGINE_RENPY:
        return remove_renpy_hook(target.exe_path)
    return _uninstall_rpgmaker_bridge(target)


_DYNAMIC_CODE_RE = re.compile(r"\\(V|N|P)\[(\d+)\]", re.IGNORECASE)
_FORMAT_CODE_RE = re.compile(
    r"\\(?:C|I|FS|PX|PY|OW|OC|SE|SP|SM|SA)\[[^\]]*\]",
    re.IGNORECASE,
)
_TIMING_CODE_RE = re.compile(r"\\[.\\|!><^{}]")


def normalize_rpgmaker_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _DYNAMIC_CODE_RE.sub(
        lambda match: f"⟦RL_{match.group(1).upper()}_{int(match.group(2))}⟧",
        value,
    )
    value = re.sub(r"\\G\b", "⟦RL_G⟧", value, flags=re.IGNORECASE)
    value = _FORMAT_CODE_RE.sub("", value)
    value = _TIMING_CODE_RE.sub("", value)
    value = re.sub(r"<\/?(?:WordWrap|CENTER|LEFT|RIGHT|TOP|MIDDLE|BOTTOM)>", "", value, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+\n", "\n", value).strip()


def _extract_speaker(text: str, actors: dict[int, str], native_speaker: str = "") -> tuple[str, str]:
    value = str(text or "")
    speaker = str(native_speaker or "").strip()
    bracket = re.match(r"^\s*【([^】\r\n]{1,80})】\s*", value)
    if not speaker and bracket:
        speaker = bracket.group(1).strip()
        value = value[bracket.end():]
    actor_prefix = re.match(r"^\s*\\N\[(\d+)\]\s*[:：]\s*", value, flags=re.IGNORECASE)
    if not speaker and actor_prefix:
        speaker = actors.get(int(actor_prefix.group(1)), f"Actor {actor_prefix.group(1)}")
        value = value[actor_prefix.end():]
    name_box = re.match(r"^\s*\\(?:N|N[1-5]|NC|NR)<([^>]+)>\s*", value, flags=re.IGNORECASE)
    if not speaker and name_box:
        speaker = name_box.group(1).strip()
        value = value[name_box.end():]
    return speaker, value


def _iter_event_lists(data_dir: Path):
    for path in sorted(data_dir.glob("Map*.json")):
        if not re.fullmatch(r"Map\d+\.json", path.name, flags=re.IGNORECASE):
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        for event in data.get("events", []) or []:
            if not event:
                continue
            for page in event.get("pages", []) or []:
                yield path.name, page.get("list", []) or []

    common_path = data_dir / "CommonEvents.json"
    if common_path.is_file():
        for event in _read_json(common_path) or []:
            if event:
                yield common_path.name, event.get("list", []) or []

    troops_path = data_dir / "Troops.json"
    if troops_path.is_file():
        for troop in _read_json(troops_path) or []:
            if not troop:
                continue
            for page in troop.get("pages", []) or []:
                yield troops_path.name, page.get("list", []) or []


def scan_rpgmaker_game(
    target: GameTarget,
    on_chunk: Callable[[list[dict]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> list[dict]:
    data_dir = Path(target.content_dir) / "data"
    actors: dict[int, str] = {}
    actors_path = data_dir / "Actors.json"
    if actors_path.is_file():
        for actor in _read_json(actors_path) or []:
            if actor:
                actors[int(actor.get("id") or 0)] = str(actor.get("name") or "")

    results: dict[str, dict] = {}
    chunk: list[dict] = []

    def add(source: str, entry_type: str, speaker: str = ""):
        source = normalize_rpgmaker_text(source)
        if not source or source in results:
            return
        item = {"source": source, "entry_type": entry_type, "speaker": str(speaker or "").strip()}
        results[source] = item
        chunk.append(item)
        if on_chunk and len(chunk) >= 200:
            on_chunk(list(chunk))
            chunk.clear()

    for filename, commands in _iter_event_lists(data_dir):
        if cancel_requested and cancel_requested():
            break
        index = 0
        while index < len(commands):
            command = commands[index] or {}
            code = int(command.get("code") or 0)
            params = command.get("parameters") or []
            if code == 101:
                native_speaker = str(params[4] or "") if len(params) >= 5 else ""
                lines = []
                cursor = index + 1
                while cursor < len(commands) and int((commands[cursor] or {}).get("code") or 0) == 401:
                    line_params = (commands[cursor] or {}).get("parameters") or []
                    lines.append(str(line_params[0] if line_params else ""))
                    cursor += 1
                speaker, text = _extract_speaker("\n".join(lines), actors, native_speaker)
                add(text, "dialogue", normalize_rpgmaker_text(speaker))
                index = cursor
                continue
            if code == 102 and params and isinstance(params[0], list):
                for choice in params[0]:
                    add(str(choice or ""), "choice")
            if code == 105:
                lines = []
                cursor = index + 1
                while cursor < len(commands) and int((commands[cursor] or {}).get("code") or 0) == 405:
                    line_params = (commands[cursor] or {}).get("parameters") or []
                    lines.append(str(line_params[0] if line_params else ""))
                    cursor += 1
                add("\n".join(lines), "dialogue")
                index = cursor
                continue
            index += 1

    if on_chunk and chunk:
        on_chunk(list(chunk))
    return list(results.values())
