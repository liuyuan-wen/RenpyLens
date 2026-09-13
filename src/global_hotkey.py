# -*- coding: utf-8 -*-
"""Windows low-level keyboard listener for the overlay shortcut."""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

from PyQt5.QtCore import QObject, pyqtSignal

from shortcuts import normalize_shortcut


_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C}
_NAMED_KEYS = {
    "SPACE": 0x20, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
    "ENTER": 0x0D, "RETURN": 0x0D, "BACKSPACE": 0x08, "DELETE": 0x2E,
    "INSERT": 0x2D, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21,
    "PAGEDOWN": 0x22, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "+": 0xBB, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    ";": 0xBA, "=": 0xBB, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for _index in range(1, 25):
    _NAMED_KEYS[f"F{_index}"] = 0x6F + _index


def parse_windows_shortcut(value: object) -> tuple[int, frozenset[str]] | None:
    text = normalize_shortcut(value)
    if not text:
        return None
    if text.endswith("++"):
        parts = [part.strip() for part in text[:-2].split("+") if part.strip()] + ["+"]
    else:
        parts = [part.strip() for part in text.split("+") if part.strip()]
    modifiers = frozenset(
        {"CTRL" if part.casefold() in {"ctrl", "control"} else part.upper()
         for part in parts[:-1]}
    )
    key = parts[-1].upper()
    if len(key) == 1 and key.isalnum():
        vk = ord(key)
    else:
        vk = _NAMED_KEYS.get(key)
    if vk is None or vk in _MODIFIER_VKS or not modifiers.issubset({"CTRL", "ALT", "SHIFT"}):
        return None
    return vk, modifiers


class OverlayHotkeyListener(QObject):
    activated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shortcut = None
        self._enabled = True
        self._stop_event = threading.Event()
        self._thread = None
        self._thread_id = 0
        self._callback = None

    def configure(self, value: object):
        self._shortcut = parse_windows_shortcut(value)

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)

    def start(self):
        if (
            os.name != "nt"
            or os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen"
            or (self._thread and self._thread.is_alive())
        ):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="RenpyLensHotkey", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self._thread_id = kernel32.GetCurrentThreadId()

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        target_down = False

        def callback(code, message, data):
            nonlocal target_down
            if code >= 0:
                event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                shortcut = self._shortcut
                if shortcut and event.vkCode == shortcut[0]:
                    if message in (0x0100, 0x0104):
                        modifiers = set()
                        if user32.GetAsyncKeyState(0x11) & 0x8000: modifiers.add("CTRL")
                        if user32.GetAsyncKeyState(0x12) & 0x8000: modifiers.add("ALT")
                        if user32.GetAsyncKeyState(0x10) & 0x8000: modifiers.add("SHIFT")
                        if self._enabled and not target_down and modifiers == set(shortcut[1]):
                            target_down = True
                            self.activated.emit()
                    elif message in (0x0101, 0x0105):
                        target_down = False
            return user32.CallNextHookEx(None, code, message, data)

        self._callback = callback_type(callback)
        hook = user32.SetWindowsHookExW(13, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._thread_id = 0
            return
        message = wintypes.MSG()
        while not self._stop_event.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(hook)
        self._thread_id = 0


def foreground_process_id() -> int:
    if os.name != "nt":
        return 0
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def process_executable_path(process_id: int) -> str:
    if os.name != "nt" or not process_id:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.normcase(os.path.abspath(buffer.value))
    finally:
        kernel32.CloseHandle(handle)
    return ""


def process_ancestry(process_id: int) -> set[int]:
    """Return the process and its ancestors using the Windows process snapshot."""
    if os.name != "nt" or not process_id:
        return set()
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return {process_id}
    parents = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    result = set()
    current = process_id
    while current and current not in result:
        result.add(current)
        current = parents.get(current, 0)
    return result
