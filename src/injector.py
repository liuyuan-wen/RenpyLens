# -*- coding: utf-8 -*-
"""Hook injection helpers for Ren'Py games."""

from __future__ import annotations

import os
import subprocess


def find_game_dir(exe_path: str) -> str | None:
    """Infer the game/ directory from the selected executable."""
    exe_dir = os.path.dirname(os.path.abspath(exe_path))
    game_dir = os.path.join(exe_dir, "game")
    if os.path.isdir(game_dir):
        return game_dir
    return None


def is_renpy_game(exe_path: str) -> bool:
    """Best-effort check for a Ren'Py game layout."""
    exe_dir = os.path.dirname(os.path.abspath(exe_path))
    game_dir = os.path.join(exe_dir, "game")
    renpy_dir = os.path.join(exe_dir, "renpy")
    lib_dir = os.path.join(exe_dir, "lib")

    if not os.path.isdir(game_dir):
        return False

    if os.path.isdir(renpy_dir) or os.path.isdir(lib_dir):
        return True

    for filename in os.listdir(game_dir):
        if filename.endswith((".rpy", ".rpyc", ".rpa")):
            return True

    return False


def _render_hook_script(hook_rpy_path: str, socket_port: int) -> str:
    with open(hook_rpy_path, "r", encoding="utf-8") as f:
        content = f.read()

    return (
        content.replace("{{SOCKET_PORT}}", str(int(socket_port)))
        .replace("{{CONTROL_PORT}}", str(int(socket_port) + 1))
    )


def inject_hook(exe_path: str, hook_rpy_path: str, socket_port: int) -> tuple[bool, str]:
    """Render and inject the hook script into the target game's game/ folder."""
    if not os.path.isfile(exe_path):
        return False, f"Game file not found: {exe_path}"

    if not is_renpy_game(exe_path):
        return False, "Not a Ren'Py game (game/ directory not found)"

    game_dir = find_game_dir(exe_path)
    if not game_dir:
        return False, "game/ directory not found"

    dest = os.path.join(game_dir, "_translator_hook.rpy")
    dest_rpyc = os.path.join(game_dir, "_translator_hook.rpyc")

    try:
        rendered = _render_hook_script(hook_rpy_path, socket_port)
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)

        if os.path.exists(dest_rpyc):
            os.remove(dest_rpyc)
    except Exception as e:
        return False, f"Failed to render hook script: {e}"

    return True, f"Injected to: {dest}"


def remove_hook(exe_path: str) -> tuple[bool, str]:
    """Remove previously injected hook files from the game directory."""
    game_dir = find_game_dir(exe_path)
    if not game_dir:
        return False, "game/ directory not found"

    hook_file = os.path.join(game_dir, "_translator_hook.rpy")
    hook_compiled = os.path.join(game_dir, "_translator_hook.rpyc")

    removed = []
    for path in (hook_file, hook_compiled):
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(os.path.basename(path))
            except Exception as e:
                return False, f"Failed to delete: {e}"

    if removed:
        return True, f"Removed: {', '.join(removed)}"
    return True, "No files to remove"


def launch_game(exe_path: str) -> subprocess.Popen | None:
    """Launch the selected game executable."""
    try:
        process = subprocess.Popen(
            [exe_path],
            cwd=os.path.dirname(exe_path),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return process
    except Exception:
        return None
