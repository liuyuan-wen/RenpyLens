# -*- coding: utf-8 -*-
"""TCP server that receives messages from the injected Ren'Py hook."""

from __future__ import annotations

import json

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QHostAddress, QTcpServer


class HookServer(QObject):
    """Listen for localhost JSON messages produced by the game hook."""

    text_received = pyqtSignal(str, str, bool, list, bool)
    prefetch_received = pyqtSignal(list)
    message_received = pyqtSignal(dict)

    def __init__(self, port: int = 19876, parent=None):
        super().__init__(parent)
        self.port = port
        self._server = None
        self._buffers = {}

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

    def _emit_current_text(self, msg: dict):
        who = str(msg.get("who", "") or "")
        what = str(msg.get("what", "") or "")
        raw_choices = msg.get("choices", [])
        choices = []
        if isinstance(raw_choices, list):
            for item in raw_choices:
                text = item if isinstance(item, str) else str(item or "")
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

        self.message_received.emit(dict(msg))

        if str(msg.get("type", "") or "") == "current":
            self._emit_current_text(msg)
