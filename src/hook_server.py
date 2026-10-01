# -*- coding: utf-8 -*-
"""TCP server that receives messages from injected game-engine bridges."""

from __future__ import annotations

import ast
import json

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QHostAddress, QTcpServer


def normalize_legacy_renpy_text(value) -> str:
    """Unwrap the repr of Python 2 text sequences sent by old Ren'Py."""
    text = value if isinstance(value, str) else str(value or "")
    stripped = text.strip()
    if not stripped or stripped[0] not in "[(" or stripped[-1] not in ")]":
        return text

    try:
        parsed = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return text
    if not isinstance(parsed, (list, tuple)):
        return text

    parts = []

    def append_strings(item):
        if isinstance(item, str):
            parts.append(item)
            return True
        if isinstance(item, (list, tuple)):
            return all(append_strings(child) for child in item)
        return False

    if not append_strings(parsed):
        return text
    return "".join(parts)


class HookServer(QObject):
    """Listen for localhost JSON messages produced by supported game hooks."""

    text_received = pyqtSignal(str, str, bool, list, bool)
    text_event_received = pyqtSignal(object)
    prefetch_received = pyqtSignal(list)
    message_received = pyqtSignal(dict)

    def __init__(self, port: int = 19876, parent=None):
        super().__init__(parent)
        self.port = port
        self._server = None
        self._buffers = {}
        self._expected_session = ""

    def set_expected_session(self, session_id: str = ""):
        self._expected_session = str(session_id or "")

    def start(self):
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        ok = self._server.listen(QHostAddress.LocalHost, self.port)
        if ok:
            print(f"[HookServer] Listening on: 127.0.0.1:{self.port}")
            return

        print(f"[HookServer] Listen failed: {self._server.errorString()}")
        self._server.close()
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        ok = self._server.listen(QHostAddress.LocalHost, self.port)
        if ok:
            print(f"[HookServer] Retry listen succeeded: 127.0.0.1:{self.port}")
        else:
            print(f"[HookServer] Retry listen also failed: {self._server.errorString()}")

    def stop(self):
        if self._server:
            self._server.close()
        self._buffers.clear()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            print(
                f"[HookServer] Connection received: "
                f"{socket.peerAddress().toString()}:{socket.peerPort()}"
            )
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_ready_read(self, socket):
        data = socket.readAll().data()
        if socket in self._buffers:
            self._buffers[socket] += data

    @staticmethod
    def uses_text_event(msg: dict) -> bool:
        """Whether current text is delivered with protocol metadata."""
        return (
            isinstance(msg.get("current_segment"), dict)
            or isinstance(msg.get("choice_segments"), list)
            or "character_id" in msg
        )

    def _emit_current_text(self, msg: dict):
        current_segment = msg.get("current_segment")
        choice_segments = msg.get("choice_segments")
        if isinstance(current_segment, dict) or isinstance(choice_segments, list):
            current_segment = dict(current_segment or {})
            # v3 producers may send only ``choice_segments`` for a menu while
            # retaining the legacy top-level current fields.  Preserve those
            # fields as the fallback segment so the caption/speaker is not
            # lost when normalizing the structured event.
            current_source = str(
                current_segment.get("source")
                or current_segment.get("display_text")
                or msg.get("source")
                or msg.get("display_text")
                or msg.get("what")
                or ""
            )
            current_display = str(
                current_segment.get("display_text")
                or msg.get("display_text")
                or current_source
            )
            normalized_choices = []
            for item in choice_segments or []:
                if isinstance(item, dict):
                    normalized_choices.append(dict(item))
            normalized_prefetch = []
            for item in msg.get("prefetch", []) or []:
                if not isinstance(item, dict):
                    continue
                normalized_prefetch.append(
                    {
                        "what": str(item.get("source") or item.get("display_text") or ""),
                        "source": str(item.get("source") or item.get("display_text") or ""),
                        "display_text": str(item.get("display_text") or item.get("source") or ""),
                        "token_values": dict(item.get("token_values") or {}),
                        "who": str(item.get("who") or ""),
                        "character_id": str(item.get("character_id") or ""),
                        "italic": bool(item.get("italic", False)),
                        "node_id": str(item.get("node_id") or item.get("menu_id") or ""),
                        "edge_id": str(item.get("edge_id") or ""),
                    }
                )
            event = {
                "type": "current",
                "engine": str(msg.get("engine") or ""),
                "session_id": str(msg.get("session_id") or ""),
                "protocol_version": int(msg.get("protocol_version", 1) or 1),
                "graph_id": str(msg.get("graph_id") or ""),
                "event_seq": msg.get("event_seq"),
                "state_version": msg.get("state_version"),
                "node_id": str(msg.get("node_id") or msg.get("menu_id") or ""),
                "edge_id": str(msg.get("edge_id") or ""),
                "block_id": str(msg.get("block_id") or ""),
                "current": {
                    "source": current_source,
                    "display_text": current_display,
                    "token_values": dict(current_segment.get("token_values") or {}),
                    "who": str(current_segment.get("who") or msg.get("who") or ""),
                    "character_id": str(current_segment.get("character_id") or msg.get("character_id") or ""),
                    "italic": bool(current_segment.get("italic", msg.get("italic", False))),
                    "node_id": str(current_segment.get("node_id") or msg.get("node_id") or ""),
                    "edge_id": str(current_segment.get("edge_id") or msg.get("edge_id") or ""),
                },
                "choices": normalized_choices,
                "menu_active": bool(msg.get("menu_active", False)),
                "prefetch": normalized_prefetch,
                "prefetch_menu_id": str(msg.get("prefetch_menu_id") or ""),
            }
            self.prefetch_received.emit(normalized_prefetch)
            self.text_event_received.emit(event)
            return

        who = str(msg.get("who", "") or "")
        what = normalize_legacy_renpy_text(msg.get("what", ""))
        prefetch_debug = msg.get("prefetch_debug")
        if isinstance(prefetch_debug, dict):
            print(
                "[HookServer][PrefetchDebug] "
                + json.dumps(prefetch_debug, ensure_ascii=False, sort_keys=True)
            )
        raw_choices = msg.get("choices", [])
        choices = []
        if isinstance(raw_choices, list):
            for item in raw_choices:
                text = normalize_legacy_renpy_text(item)
                text = text.strip()
                if text:
                    choices.append(text)

        prefetch = msg.get("prefetch", [])
        if prefetch:
            print(f"[HookServer] Prefetch {len(prefetch)} items")
        self.prefetch_received.emit(prefetch)

        if what or choices:
            italic = bool(msg.get("italic", False))
            menu_active = bool(msg.get("menu_active", False))
            print(
                f"[HookServer] Current text: who={who}, what={what[:50]}, "
                f"italic={italic}, choices={len(choices)}, menu_active={menu_active}"
            )
            if "character_id" in msg:
                self.text_event_received.emit({
                    "type": "current",
                    "session_id": str(msg.get("session_id") or ""),
                    "protocol_version": int(msg.get("protocol_version", 1) or 1),
                    "graph_id": str(msg.get("graph_id") or ""),
                    "event_seq": msg.get("event_seq"),
                    "state_version": msg.get("state_version"),
                    "node_id": str(msg.get("node_id") or msg.get("menu_id") or ""),
                    "edge_id": str(msg.get("edge_id") or ""),
                    "block_id": str(msg.get("block_id") or ""),
                    "current": {"source": what, "who": who, "italic": italic,
                                "character_id": str(msg.get("character_id") or ""),
                                "node_id": str(msg.get("node_id") or "")},
                    "choices": [{"source": choice} for choice in choices],
                    "menu_active": menu_active,
                    "prefetch_menu_id": str(msg.get("prefetch_menu_id") or ""),
                })
            else:
                self.text_received.emit(who, what, italic, choices, menu_active)

    def _on_disconnected(self, socket):
        data = self._buffers.pop(socket, b"")
        socket.deleteLater()
        if not data:
            return

        print(f"[HookServer] Data received ({len(data)} bytes): {data[:200]}")
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception as e:
            print(f"[HookServer] JSON parse error: {e}")
            return

        if not isinstance(msg, dict):
            print("[HookServer] Ignoring non-dict payload")
            return

        engine = str(msg.get("engine") or "")
        session_id = str(msg.get("session_id") or "")
        if (
            self._expected_session
            and engine.startswith("rpgmaker_")
            and session_id != self._expected_session
        ):
            print("[HookServer] Ignoring stale RPG Maker session")
            return

        self.message_received.emit(dict(msg))

        if str(msg.get("type", "") or "") == "current":
            self._emit_current_text(msg)
