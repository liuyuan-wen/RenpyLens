# -*- coding: utf-8 -*-
"""Desktop package discovery. No UI or engine runtime is imported here."""

from __future__ import annotations

import os
import plistlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GameLaunchSpec:
    executable: str
    arguments: str = ""
    working_directory: str = ""
    shortcut_path: str = ""


@dataclass(frozen=True)
class RenpyLayout:
    executable: str
    root_dir: str
    content_dir: str
    bundle_path: str = ""
    title: str = ""


def _app_bundle(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.suffix.lower() == ".app" and candidate.is_dir():
            return candidate
    return None


def _bundle_info(bundle: Path) -> dict:
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            return plistlib.load(handle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}


def resolve_game_launch(path: str) -> GameLaunchSpec | None:
    path = os.path.abspath(path)
    selected = Path(path)
    if selected.suffix.lower() == ".app" and selected.is_dir():
        name = _bundle_info(selected).get("CFBundleExecutable", "")
        if not isinstance(name, str) or not name or Path(name).name != name:
            return None
        executable = selected / "Contents" / "MacOS" / name
        if executable.is_file():
            return GameLaunchSpec(str(executable), working_directory=str(executable.parent))
        return None
    if selected.suffix.lower() == ".exe" or (
        os.name != "nt" and selected.is_file() and os.access(path, os.X_OK)
    ):
        return GameLaunchSpec(path)
    if selected.suffix.lower() != ".lnk" or os.name != "nt":
        return None
    try:
        from win32com.client import Dispatch

        shortcut = Dispatch("WScript.Shell").CreateShortcut(path)
        target = os.path.abspath(str(shortcut.TargetPath or "").strip())
        if not target.lower().endswith(".exe"):
            return None
        return GameLaunchSpec(
            executable=target,
            arguments=str(shortcut.Arguments or ""),
            working_directory=str(shortcut.WorkingDirectory or "").strip(),
            shortcut_path=path,
        )
    except Exception:
        return None


def find_renpy_layout(path: str) -> RenpyLayout | None:
    spec = resolve_game_launch(path)
    executable = Path(spec.executable if spec else path).absolute()
    bundle = _app_bundle(executable)
    roots = [executable.parent]
    info = {}
    if bundle:
        info = _bundle_info(bundle)
        resources = bundle / "Contents" / "Resources"
        roots = [resources / "autorun", resources, *roots]
    for root in roots:
        game = root / "game"
        if not game.is_dir():
            continue
        if not ((root / "renpy").is_dir() or (root / "lib").is_dir()
                or any(p.suffix.lower() in {".rpy", ".rpyc", ".rpa"} for p in game.iterdir())):
            continue
        return RenpyLayout(
            str(executable), str(root), str(game), str(bundle or ""),
            str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or executable.stem),
        )
    return None
