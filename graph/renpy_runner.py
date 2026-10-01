"""Shared helpers for launching an instrumented Ren'Py process."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


def resolve_executable(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(path)
    candidates = sorted(path.glob("*.exe"))
    if len(candidates) != 1:
        names = ", ".join(item.name for item in candidates) or "none"
        raise RuntimeError(f"Expected one game executable in {path}; found: {names}")
    return candidates[0]


class TemporaryLoader:
    """Install one command-registration script and restore prior files."""

    def __init__(
        self,
        game_dir: Path,
        runtime_module: str = "renpy_shadow_route_runtime",
        loader_name: str = "zz_renpylens_shadow_route.rpy",
    ):
        self.game_dir = game_dir
        self.runtime_module = runtime_module
        self.targets = [
            game_dir / loader_name,
            game_dir / (loader_name + "c"),
        ]
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.backups: dict[Path, Path] = {}

    def __enter__(self) -> "TemporaryLoader":
        self.temp_dir = tempfile.TemporaryDirectory(prefix="renpylens-loader-")
        backup_root = Path(self.temp_dir.name)
        for target in self.targets:
            if target.exists():
                backup = backup_root / target.name
                shutil.copy2(target, backup)
                self.backups[target] = backup
                target.unlink()
        self.targets[0].write_text(
            "init python:\n"
            "    import os as _rl_os\n"
            "    import sys as _rl_sys\n"
            "    _rl_sys.path.insert(0, _rl_os.environ['RENPYLENS_SHADOW_RUNTIME_DIR'])\n"
            f"    import {self.runtime_module} as _rl_runtime\n",
            encoding="utf-8",
            newline="\n",
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for target in self.targets:
            if target.exists():
                target.unlink()
        for target, backup in self.backups.items():
            shutil.copy2(backup, target)
        if self.temp_dir is not None:
            self.temp_dir.cleanup()
