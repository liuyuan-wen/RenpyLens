"""Cross-platform ownership and graceful replacement of a desktop instance."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QIODevice, QLockFile, QObject, QSaveFile, QThread, QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
import psutil

from application_paths import application_data_dir
from process_runtime import terminate_port_listener


class SingleInstance(QObject):
    def __init__(self, data_dir: str | None = None, parent=None):
        super().__init__(parent)
        directory = os.path.realpath(data_dir or application_data_dir())
        Path(directory).mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(os.path.normcase(directory).encode('utf-8')).hexdigest()[:24]
        self.server_name = f'renpylens-{identity}'
        self._lock = QLockFile(os.path.join(directory, 'instance.lock'))
        self._lock.setStaleLockTime(0)
        self._state_file = Path(directory) / 'instance-state.json'
        self._started_at = time.monotonic_ns()
        self._process_created_at = psutil.Process().create_time()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._connections = {}
        self._handler = None
        self._replacement_response = None

    def acquire(self, timeout_ms: int = 10000, port: int | None = None) -> dict | None:
        """Own the profile before opening storage/UI; None means superseded."""
        deadline = time.monotonic() + timeout_ms / 1000
        state = {}
        owner = None
        forced = False
        while not self._lock.tryLock(0):
            if self._lock.error() != QLockFile.LockFailedError:
                raise OSError(f'Cannot lock RenpyLens profile: {self._lock.error()}')
            valid, pid, _hostname, _name = self._lock.getLockInfo()
            if valid and (owner is None or owner.pid != pid):
                try:
                    owner = psutil.Process(pid)
                except psutil.NoSuchProcess:
                    continue
            if owner and owner.create_time() > self._process_created_at:
                return None  # A later launch already took ownership.
            if time.monotonic() >= deadline:
                if not forced and owner and port is not None and terminate_port_listener(owner, port):
                    forced = True
                    deadline = time.monotonic() + 2
                    continue
                raise TimeoutError('Previous instance has not finished exiting')
            response = self._request_replacement(deadline)
            if response:
                if response['status'] == 'superseded':
                    return None
                if response['status'] == 'rejected':
                    raise RuntimeError(response['error'])
                state = response['state']
            QThread.msleep(20)

        # Only the lock owner may remove a socket left by a crashed process.
        QLocalServer.removeServer(self.server_name)
        if not self._server.listen(self.server_name):
            error = self._server.errorString()
            self.close()
            raise OSError(error)
        if self._state_file.exists():
            # A concurrent launch can acquire the lock after the old owner
            # exits without having received its IPC response.
            state = json.loads(self._state_file.read_text(encoding='utf-8'))
            self._state_file.unlink()
        return state

    def set_replacement_handler(self, handler):
        self._handler = handler
        for socket in list(self._connections):
            self._read_request(socket)

    def _request_replacement(self, deadline: float) -> dict | None:
        socket = QLocalSocket()
        try:
            socket.connectToServer(self.server_name)
            if not socket.waitForConnected(min(100, self._remaining_ms(deadline))):
                return None  # The owner may still be starting or shutting down.
            request = {'command': 'replace', 'created_at': self._process_created_at, 'started_at': self._started_at}
            socket.write(json.dumps(request).encode('utf-8') + b'\n')
            socket.flush()
            response = b''
            while time.monotonic() < deadline:
                response += bytes(socket.readAll())
                if b'\n' in response:
                    return json.loads(response.split(b'\n', 1)[0])
                if socket.state() == QLocalSocket.UnconnectedState:
                    return None
                socket.waitForReadyRead(min(100, self._remaining_ms(deadline)))
            return None
        finally:
            socket.abort()

    @staticmethod
    def _remaining_ms(deadline: float) -> int:
        return max(1, round((deadline - time.monotonic()) * 1000))

    def _accept_connections(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._connections[socket] = b''
            socket.readyRead.connect(lambda s=socket: self._read_request(s))
            socket.disconnected.connect(lambda s=socket: self._disconnect(s))
            self._read_request(socket)

    def _read_request(self, socket):
        if socket not in self._connections:
            return
        self._connections[socket] += bytes(socket.readAll())
        data = self._connections[socket]
        if b'\n' not in data or self._handler is None:
            return
        try:
            request = json.loads(data.split(b'\n', 1)[0])
            if request['command'] != 'replace':
                raise ValueError('Invalid instance request')
            if (request['created_at'], request['started_at']) <= (self._process_created_at, self._started_at):
                self._respond(socket, {'status': 'superseded'})
                return
            if self._replacement_response is None:
                state = self._handler()
                handoff = QSaveFile(str(self._state_file))
                if not handoff.open(QIODevice.WriteOnly):
                    raise OSError(handoff.errorString())
                handoff.write(json.dumps(state, ensure_ascii=False).encode('utf-8'))
                if not handoff.commit():
                    raise OSError(handoff.errorString())
                self._replacement_response = {'status': 'accepted', 'state': state}
            self._respond(socket, self._replacement_response)
            QTimer.singleShot(0, QCoreApplication.instance().quit)
        except Exception as error:
            self._respond(socket, {'status': 'rejected', 'error': str(error)})

    def _respond(self, socket, response):
        socket.write(json.dumps(response, ensure_ascii=False).encode('utf-8') + b'\n')
        socket.flush()
        while socket.bytesToWrite() and socket.waitForBytesWritten(1000):
            pass
        socket.disconnectFromServer()

    def _disconnect(self, socket):
        self._connections.pop(socket, None)
        socket.deleteLater()

    def close(self):
        self._server.close()
        for socket in list(self._connections):
            socket.abort()
        self._lock.unlock()
