# -*- coding: utf-8 -*-
"""Hook injection helpers for Ren'Py games."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass


if os.name == "nt":
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_UNICODE_PATH = 32768

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass(frozen=True)
class _ProcessEntry:
    pid: int
    ppid: int
    name: str


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_within_dir(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _iter_process_entries() -> list[_ProcessEntry]:
    if os.name != "nt":
        return []

    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []

    entries: list[_ProcessEntry] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

    try:
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return entries

        while True:
            entries.append(
                _ProcessEntry(
                    pid=int(entry.th32ProcessID),
                    ppid=int(entry.th32ParentProcessID),
                    name=str(entry.szExeFile),
                )
            )
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)

    return entries


def _query_process_image_path(pid: int) -> str | None:
    if os.name != "nt" or pid <= 0:
        return None

    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None

    try:
        size = wintypes.DWORD(MAX_UNICODE_PATH)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            image_path = buffer.value
            if image_path:
                return _normalize_path(image_path)
    finally:
        _kernel32.CloseHandle(handle)

    return None


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


def launch_game(exe_path: str) -> GameLaunchHandle | None:
    """Launch the selected game executable."""
    try:
        process = subprocess.Popen(
            [exe_path],
            cwd=os.path.dirname(exe_path),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return GameLaunchHandle(process, exe_path)
    except Exception:
        return None
