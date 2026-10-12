"""macOS registered hotkeys, dispatched by the application event loop.

RegisterEventHotKey needs no keyboard monitoring permission. Qt's portable Ctrl
maps to Command on macOS; portable Meta maps to Control, as in QKeySequence.
"""

import ctypes
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence


_KEY_CODES = {
    "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7,
    "C": 8, "V": 9, "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15,
    "Y": 16, "T": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "+": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "O": 31, "U": 32, "[": 33, "I": 34, "P": 35,
    "RETURN": 36, "ENTER": 76, "L": 37, "J": 38, "'": 39, "K": 40,
    ";": 41, "\\": 42, ",": 43, "/": 44, "N": 45, "M": 46, ".": 47,
    "TAB": 48, "SPACE": 49, "`": 50, "BACKSPACE": 51, "ESC": 53,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
    "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
    "F13": 105, "F14": 107, "F15": 113, "F16": 106, "F17": 64,
    "F18": 79, "F19": 80, "F20": 90, "HOME": 115, "PAGEUP": 116,
    "DELETE": 117, "END": 119, "PAGEDOWN": 121,
    "LEFT": 123, "RIGHT": 124, "DOWN": 125, "UP": 126,
}


def parse_macos_shortcut(value):
    sequence = QKeySequence.fromString(str(value or ""), QKeySequence.PortableText)
    if sequence.isEmpty():
        return None
    chord = int(sequence[0])
    key = QKeySequence(chord & ~int(Qt.KeyboardModifierMask)).toString(QKeySequence.PortableText).upper()
    code = _KEY_CODES.get(key)
    if code is None:
        return None
    modifiers = 0
    for qt_flag, carbon_flag in ((Qt.ControlModifier, 1 << 8), (Qt.ShiftModifier, 1 << 9),
                                 (Qt.AltModifier, 1 << 11), (Qt.MetaModifier, 1 << 12)):
        if chord & int(qt_flag):
            modifiers |= carbon_flag
    return code, modifiers


class _EventType(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _HotkeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class MacHotkey:
    def __init__(self, activated):
        self._activated = activated
        self._hotkey = ctypes.c_void_p()
        self._handler = ctypes.c_void_p()
        self._callback = None
        self._carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon = self._carbon
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        self._callback_type = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, self._callback_type, ctypes.c_uint32,
                                               ctypes.POINTER(_EventType), ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotkeyID,
                                               ctypes.c_void_p, ctypes.c_uint32,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.c_void_p]
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]

    def configure(self, value):
        self.stop()
        parsed = parse_macos_shortcut(value)
        if parsed is None:
            if value:
                print(f"[Hotkey] Unsupported macOS shortcut: {value}")
            return

        def callback(_next, event, _data):
            identifier = _HotkeyID()
            status = self._carbon.GetEventParameter(event, 0x2D2D2D2D, 0x686B6964, None,
                                                     ctypes.sizeof(identifier), None,
                                                     ctypes.byref(identifier))
            if status == 0 and identifier.signature == 0x524C4E53 and identifier.id == 1:
                self._activated()
                return 0
            return -9874  # eventNotHandledErr

        self._callback = self._callback_type(callback)
        event_type = _EventType(0x6B657962, 5)  # keyboard / hotkey pressed
        target = self._carbon.GetApplicationEventTarget()
        status = self._carbon.InstallEventHandler(target, self._callback, 1, ctypes.byref(event_type),
                                                  None, ctypes.byref(self._handler))
        if status == 0:
            status = self._carbon.RegisterEventHotKey(*parsed, _HotkeyID(0x524C4E53, 1),
                                                      target, 0, ctypes.byref(self._hotkey))
        if status != 0:
            self.stop()
            print(f"[Hotkey] Could not register macOS shortcut {value}: OSStatus {status}")

    def stop(self):
        if self._hotkey.value:
            self._carbon.UnregisterEventHotKey(self._hotkey)
            self._hotkey = ctypes.c_void_p()
        if self._handler.value:
            self._carbon.RemoveEventHandler(self._handler)
            self._handler = ctypes.c_void_p()
