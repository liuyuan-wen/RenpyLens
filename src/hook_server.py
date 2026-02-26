# -*- coding: utf-8 -*-
"""TCP Socket 服务器 - 接收来自 Ren'Py hook 的文本
使用 QTcpServer 以确保与 Qt 事件循环正确集成"""

import json
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QTcpServer, QHostAddress


class HookServer(QObject):
    """监听来自游戏内 _translator_hook.rpy 的 TCP 连接"""

    text_received = pyqtSignal(str, str, bool)  # who, what, italic
    prefetch_received = pyqtSignal(list)  # [{"who": ..., "what": ...}, ...]

    def __init__(self, port: int = 19876, parent=None):
        super().__init__(parent)
        self.port = port
        self._server = None
        self._buffers = {}  # socket -> accumulated data

    def start(self):
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        ok = self._server.listen(QHostAddress.LocalHost, self.port)
        if ok:
            print(f"[HookServer] Listening on: 127.0.0.1:{self.port}")
        else:
            print(f"[HookServer] Listen failed: {self._server.errorString()}")
            # 尝试关闭后重新监听
            self._server.close()
            self._server = QTcpServer(self)
            self._server.newConnection.connect(self._on_new_connection)
            ok2 = self._server.listen(QHostAddress.LocalHost, self.port)
            if ok2:
                print(f"[HookServer] Retry listen succeeded: 127.0.0.1:{self.port}")
            else:
                print(f"[HookServer] Retry listen also failed: {self._server.errorString()}")

    def stop(self):
        if self._server:
            self._server.close()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            print(f"[HookServer] Connection received: {socket.peerAddress().toString()}:{socket.peerPort()}")
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_ready_read(self, socket):
        data = socket.readAll().data()
        if socket in self._buffers:
            self._buffers[socket] += data

    def _on_disconnected(self, socket):
        data = self._buffers.pop(socket, b"")
        socket.deleteLater()
        if data:
            print(f"[HookServer] Data received ({len(data)} bytes): {data[:200]}")
            try:
                msg = json.loads(data.decode("utf-8"))
                who = msg.get("who", "")
                what = msg.get("what", "")
                # 先发射预取信号（让 _latest_prefetch_items 先更新）
                prefetch = msg.get("prefetch", [])
                if prefetch:
                    print(f"[HookServer] Prefetch {len(prefetch)} items")
                    self.prefetch_received.emit(prefetch)
                # 再发射当前文本信号
                if what:
                    italic = msg.get("italic", False)
                    print(f"[HookServer] Signal emitted: who={who}, what={what[:50]}, italic={italic}")
                    self.text_received.emit(who, what, italic)
            except Exception as e:
                print(f"[HookServer] JSON parse error: {e}")
