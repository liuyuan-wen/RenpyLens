# -*- coding: utf-8 -*-
"""Hook injection helpers for Ren'Py games."""

from __future__ import annotations

import os
import subprocess
import shlex
import tempfile
import textwrap
import time
import zipfile


_HOOK_FILE_PREFIX = "_translator_hook"
_HOOK_FILE_EXTENSIONS = {".rpy", ".rpyc"}
_SEMANTICS_ARCHIVE_NAME = "_renpylens_semantics.zip"
_SEMANTICS_MARKER = "RenpyLens Semantic Kernel\n"


from game_package import GameLaunchSpec, find_renpy_layout, resolve_game_launch
from process_runtime import ProcessEntry as _ProcessEntry
from process_runtime import process_entries, process_executable_path


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_within_dir(path: str, root: str) -> bool:
    path = _normalize_path(path)
    root = _normalize_path(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _iter_process_entries() -> list[_ProcessEntry]:
    return process_entries()


def _query_process_image_path(pid: int) -> str | None:
    return process_executable_path(pid) or None


def is_game_running(exe_path: str) -> bool:
    """Return whether the exact executable is already running."""
    if not exe_path:
        return False

    target_path = _normalize_path(exe_path)
    expected_name = os.path.basename(target_path).lower()
    for entry in _iter_process_entries():
        if entry.name.lower() != expected_name:
            continue
        if _query_process_image_path(entry.pid) == target_path:
            return True
    return False


class GameLaunchHandle:
    """Track a launched game and keep wrapper-spawned children alive logically."""

    def __init__(self, process: subprocess.Popen, exe_path: str):
        self._process = process
        self._root_pid = int(process.pid)
        self._root_returncode: int | None = None
        self._launch_started_at = time.monotonic()
        self._tracked_pids = {self._root_pid}
        self._game_root = _normalize_path(os.path.dirname(exe_path))
        self._expected_exe_name = os.path.basename(exe_path).lower()

    @property
    def pid(self) -> int:
        return self._root_pid

    def _collect_game_descendants(self) -> set[int]:
        entries = _iter_process_entries()
        if not entries:
            return set()

        children_by_parent: dict[int, list[_ProcessEntry]] = {}
        for entry in entries:
            children_by_parent.setdefault(entry.ppid, []).append(entry)

        live_pids = {entry.pid for entry in entries}
        descendants = {pid for pid in self._tracked_pids if pid in live_pids and pid != self._root_pid}
        queue = [pid for pid in self._tracked_pids if pid in live_pids or pid == self._root_pid]
        path_cache: dict[int, str | None] = {}

        while queue:
            parent_pid = queue.pop()
            for child in children_by_parent.get(parent_pid, []):
                if child.pid in descendants:
                    continue

                image_path = path_cache.get(child.pid)
                if image_path is None:
                    image_path = _query_process_image_path(child.pid)
                    path_cache[child.pid] = image_path

                # 只接管游戏目录内的子进程，或同名的包装 EXE，避免误跟踪外部程序。
                if image_path and _is_within_dir(image_path, self._game_root):
                    descendants.add(child.pid)
                    queue.append(child.pid)
                    continue

                if child.name.lower() == self._expected_exe_name:
                    descendants.add(child.pid)
                    queue.append(child.pid)

        if descendants:
            return descendants

        # 个别启动器会瞬间退出并重拉同名 EXE，这里给一个短暂的兜底窗口。
        if time.monotonic() - self._launch_started_at > 8.0:
            return set()

        fallback: set[int] = set()
        for entry in entries:
            if entry.name.lower() != self._expected_exe_name:
                continue

            image_path = path_cache.get(entry.pid)
            if image_path is None:
                image_path = _query_process_image_path(entry.pid)
                path_cache[entry.pid] = image_path

            if image_path and _is_within_dir(image_path, self._game_root):
                fallback.add(entry.pid)

        return fallback

    def poll(self) -> int | None:
        root_returncode = self._process.poll()
        if root_returncode is None:
            return None

        if self._root_returncode is None:
            self._root_returncode = int(root_returncode)

        descendants = self._collect_game_descendants()
        if descendants:
            self._tracked_pids.update(descendants)
            return None

        return self._root_returncode


def find_game_dir(exe_path: str) -> str | None:
    layout = find_renpy_layout(exe_path)
    return layout.content_dir if layout else None


def is_renpy_game(exe_path: str) -> bool:
    return find_renpy_layout(exe_path) is not None


def _render_hook_script(hook_rpy_path: str, socket_port: int, graph_id: str = "") -> str:
    with open(hook_rpy_path, "r", encoding="utf-8") as f:
        content = f.read()

    rendered = (
        content.replace("{{SOCKET_PORT}}", str(int(socket_port)))
        .replace("{{CONTROL_PORT}}", str(int(socket_port) + 1))
        .replace("{{GRAPH_ID}}", str(graph_id or ""))
    )
    header, body = rendered.split("init python:\n", 1)
    # Run the hook in a Python module rather than the game's rollback store.
    # Script indexes, transport state and callbacks must survive save/load
    # without being serialized. python hide also keeps loader locals out of
    # the store on older engines that do not support constant named stores.
    body = textwrap.dedent(body)
    return header + (
        "init python hide:\n"
        "    import sys as _rl_sys\n"
        "    import types as _rl_types\n"
        "    import renpy as _rl_renpy\n"
        "    _rl_runtime = _rl_types.ModuleType(_rl_renpy.compat.pystr('_renpylens_hook'))\n"
        "    _rl_runtime.__dict__.update(\n"
        "        (k, v) for k, v in vars(_rl_renpy.minstore).items()\n"
        "        if not k.startswith('__')\n"
        "    )\n"
        "    _rl_runtime.config = _rl_renpy.config\n"
        "    _rl_sys.modules['_renpylens_hook'] = _rl_runtime\n"
        "    exec(u" + repr(body) + ", _rl_runtime.__dict__)\n"
    )


def _find_hook_artifacts(game_dir: str) -> list[str]:
    """Find RenpyLens Hook source/compiled files, including renamed copies."""
    def raise_scan_error(error: OSError):
        raise error

    artifacts: list[str] = []
    for root, dirs, files in os.walk(
        game_dir,
        topdown=True,
        onerror=raise_scan_error,
        followlinks=False,
    ):
        dirs[:] = [
            name for name in dirs
            if not os.path.islink(os.path.join(root, name))
        ]
        for filename in files:
            stem, extension = os.path.splitext(filename)
            if (
                stem.casefold().startswith(_HOOK_FILE_PREFIX.casefold())
                and extension.casefold() in _HOOK_FILE_EXTENSIONS
            ):
                artifacts.append(os.path.join(root, filename))

    return sorted(
        artifacts,
        key=lambda path: os.path.relpath(path, game_dir).casefold(),
    )


def _hook_relative_path(path: str, game_dir: str) -> str:
    return os.path.relpath(path, game_dir).replace(os.sep, "/")


def _find_semantics_source(hook_rpy_path: str) -> str | None:
    hook_dir = os.path.dirname(os.path.abspath(hook_rpy_path))
    candidates = (
        os.path.join(hook_dir, "renpylens_semantics"),
        os.path.join(hook_dir, "..", "src", "renpylens_semantics"),
    )
    for candidate in candidates:
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "__init__.py")):
            return os.path.abspath(candidate)
    return None


def _semantics_archive_owned(path: str) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            return archive.read("__renpylens_manifest__.txt").decode("utf-8") == _SEMANTICS_MARKER
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, UnicodeError):
        return False


def _stage_semantics_archive(source_dir: str, game_dir: str) -> str:
    destination = os.path.join(game_dir, _SEMANTICS_ARCHIVE_NAME)
    if os.path.exists(destination) and not _semantics_archive_owned(destination):
        raise RuntimeError("existing semantic kernel archive is not owned by RenpyLens")

    temporary = tempfile.NamedTemporaryFile(
        prefix=".renpylens-semantics-", suffix=".tmp", dir=game_dir, delete=False
    )
    temporary_path = temporary.name
    temporary.close()
    try:
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("__renpylens_manifest__.txt", _SEMANTICS_MARKER)
            for root, _dirs, files in os.walk(source_dir):
                for filename in sorted(files):
                    if not filename.endswith(".py"):
                        continue
                    path = os.path.join(root, filename)
                    relative = os.path.relpath(path, source_dir).replace(os.sep, "/")
                    archive.write(path, "renpylens_semantics/" + relative)
        return temporary_path
    except Exception:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise


def inject_hook(exe_path: str, hook_rpy_path: str, socket_port: int, *, install_dir: str | None = None) -> tuple[bool, str]:
    """Render and inject the hook script into the target game's game/ folder."""
    if not os.path.isfile(exe_path):
        return False, f"Game file not found: {exe_path}"

    if not is_renpy_game(exe_path):
        return False, "Not a Ren'Py game (game/ directory not found)"

    game_dir = find_game_dir(exe_path)
    if not game_dir:
        return False, "game/ directory not found"

    content_dir = game_dir
    if install_dir is not None:
        game_dir = os.path.abspath(install_dir)
        try:
            os.makedirs(game_dir, exist_ok=True)
        except OSError as exc:
            return False, f"Failed to create Hook directory: {exc}"
    dest = os.path.join(game_dir, "_translator_hook.rpy")
    try:
        from renpylens_semantics.renpy_adapter import graph_id_for

        rendered = _render_hook_script(hook_rpy_path, socket_port, graph_id_for(content_dir))
    except Exception as e:
        return False, f"Failed to read Hook script: {e}"

    semantics_source = _find_semantics_source(hook_rpy_path)
    if semantics_source is None:
        return False, "Semantic Kernel package not found"
    try:
        staged_archive = _stage_semantics_archive(semantics_source, game_dir)
    except Exception as e:
        return False, f"Failed to stage Semantic Kernel: {e}"

    dest_normalized = _normalize_path(dest)
    try:
        residuals = [
            path for path in _find_hook_artifacts(game_dir)
            if _normalize_path(path) != dest_normalized
        ]
    except Exception as e:
        return False, f"Failed to scan game directory for residual Hooks: {e}"
    removed: list[str] = []
    for path in residuals:
        relative_path = _hook_relative_path(path, game_dir)
        try:
            os.remove(path)
            removed.append(relative_path)
        except Exception as e:
            try:
                os.remove(staged_archive)
            except OSError:
                pass
            return False, f"Failed to remove residual Hook '{relative_path}': {e}"

    try:
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
    except Exception as e:
        try:
            os.remove(staged_archive)
        except OSError:
            pass
        return False, f"Failed to write Hook script: {e}"

    try:
        os.replace(staged_archive, os.path.join(game_dir, _SEMANTICS_ARCHIVE_NAME))
    except Exception as e:
        try:
            os.remove(staged_archive)
        except OSError:
            pass
        return False, f"Failed to install Semantic Kernel: {e}"

    message = f"Injected to: {dest} (Semantic Kernel installed)"
    if removed:
        message += f"; cleaned {len(removed)} residual Hook file(s): {', '.join(removed)}"
    return True, message


def remove_hook(exe_path: str, *, install_dir: str | None = None) -> tuple[bool, str]:
    """Remove owned hook files from the selected installation directory."""
    game_dir = install_dir if install_dir is not None else find_game_dir(exe_path)
    if not game_dir:
        return False, "game/ directory not found"

    try:
        artifacts = _find_hook_artifacts(game_dir)
    except Exception as e:
        return False, f"Failed to scan game directory for Hooks: {e}"

    removed: list[str] = []
    for path in artifacts:
        relative_path = _hook_relative_path(path, game_dir)
        try:
            os.remove(path)
            removed.append(relative_path)
        except Exception as e:
            return False, f"Failed to remove Hook '{relative_path}': {e}"

    semantics_archive = os.path.join(game_dir, _SEMANTICS_ARCHIVE_NAME)
    if os.path.isfile(semantics_archive) and _semantics_archive_owned(semantics_archive):
        try:
            os.remove(semantics_archive)
            removed.append(_SEMANTICS_ARCHIVE_NAME)
        except Exception as e:
            return False, f"Failed to remove Semantic Kernel: {e}"

    if removed:
        return True, f"Removed: {', '.join(removed)}"
    return True, "No Hook files to remove"


def launch_game(
    exe_path: str,
    arguments: str = "",
    working_directory: str = "",
    environment: dict[str, str] | None = None,
) -> GameLaunchHandle | None:
    """Launch the selected game executable."""
    try:
        command = [exe_path]
        options = {"cwd": working_directory or os.path.dirname(exe_path)}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            if arguments:
                command = f"{subprocess.list2cmdline([exe_path])} {arguments}"
        else:
            command.extend(shlex.split(arguments))
            options["start_new_session"] = True
        if environment is not None:
            options["env"] = environment
        process = subprocess.Popen(command, **options)
        return GameLaunchHandle(process, exe_path)
    except Exception as exc:
        print(f"[Launcher] Failed to launch {exe_path}: {exc}")
        return None
