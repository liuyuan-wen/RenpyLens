"""Shared desktop process inspection, independent of Qt and engine adapters."""

from dataclasses import dataclass
import os

import psutil


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    ppid: int
    name: str


def process_entries() -> list[ProcessEntry]:
    if os.name == "nt":
        # Reading ppid/name/status separately for every Windows process is slow.
        from windows_process_runtime import process_entries as windows_process_entries
        return [ProcessEntry(*entry) for entry in windows_process_entries()]
    entries = []
    for process in psutil.process_iter(["pid", "ppid", "name", "status"]):
        try:
            info = process.info
            if info["status"] != psutil.STATUS_ZOMBIE:
                entries.append(ProcessEntry(info["pid"], info["ppid"], info["name"] or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return entries


def process_executable_path(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        path = psutil.Process(pid).exe()
        return os.path.normcase(os.path.abspath(path)) if path else ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def process_ancestry(pid: int) -> set[int]:
    if pid <= 0:
        return set()
    try:
        return {pid, *(parent.pid for parent in psutil.Process(pid).parents() if parent.pid > 0)}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {pid}


def terminate_port_listener(process: psutil.Process, port: int, timeout: float = 2.0) -> bool:
    """Stop an identified owner only while it still listens on the app's port."""
    if process.pid == os.getpid():
        return False
    try:
        connections = process.net_connections(kind='tcp')
        if not any(connection.status == psutil.CONN_LISTEN
                   and connection.laddr.ip == '127.0.0.1'
                   and connection.laddr.port == port for connection in connections):
            return False
        process.terminate()
        try:
            process.wait(timeout)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout)
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied as error:
        raise PermissionError(str(error)) from error
    except psutil.TimeoutExpired as error:
        raise TimeoutError(str(error)) from error
