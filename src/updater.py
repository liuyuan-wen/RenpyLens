# -*- coding: utf-8 -*-
"""Simple self-updater helpers for Windows PyInstaller builds."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

import httpx


GITHUB_API_BASE = "https://api.github.com"


@dataclass
class ReleaseInfo:
    tag_name: str
    html_url: str
    body: str
    published_at: str
    asset_name: str
    asset_url: str


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version or ""))
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer_version(latest: str, current: str) -> bool:
    return _parse_version_tuple(latest) > _parse_version_tuple(current)


def _choose_asset(assets: list[dict]) -> tuple[str, str]:
    if not isinstance(assets, list):
        return "", ""

    candidates = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "")
        url = str(item.get("browser_download_url", "") or "")
        if name.lower().endswith(".exe") and url:
            candidates.append((name, url))

    if not candidates:
        return "", ""

    for name, url in candidates:
        if "renpylens" in name.lower():
            return name, url
    return candidates[0]


def fetch_latest_release(repo: str, timeout_sec: float = 8.0) -> tuple[ReleaseInfo | None, str | None]:
    repo = str(repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        return None, "Invalid GitHub repo. Expected format: owner/repo"

    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RenpyLens-Updater",
    }

    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return None, "TIMEOUT: Failed to fetch latest release from GitHub"
    except httpx.NetworkError as e:
        return None, f"NETWORK: Failed to fetch latest release from GitHub: {e}"
    except Exception as e:
        return None, f"Failed to fetch latest release: {e}"

    tag_name = str(data.get("tag_name", "") or "")
    html_url = str(data.get("html_url", "") or "")
    body = str(data.get("body", "") or "")
    published_at = str(data.get("published_at", "") or "")
    asset_name, asset_url = _choose_asset(data.get("assets", []))

    if not tag_name:
        return None, "Latest release does not contain tag_name"

    return ReleaseInfo(
        tag_name=tag_name,
        html_url=html_url,
        body=body,
        published_at=published_at,
        asset_name=asset_name,
        asset_url=asset_url,
    ), None


def download_release_asset(download_url: str, file_name: str, timeout_sec: float = 120.0) -> tuple[str | None, str | None]:
    download_url = str(download_url or "").strip()
    file_name = os.path.basename(str(file_name or "").strip()) or "RenpyLens_update.exe"
    if not download_url:
        return None, "Missing download URL"

    temp_dir = tempfile.gettempdir()
    dest_path = os.path.join(temp_dir, file_name)

    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            with client.stream("GET", download_url) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
    except Exception as e:
        return None, f"Failed to download update asset: {e}"

    return dest_path, None


def launch_windows_updater_script(new_exe_path: str, target_exe_path: str, current_pid: int) -> tuple[bool, str | None]:
    new_exe_path = os.path.abspath(new_exe_path)
    target_exe_path = os.path.abspath(target_exe_path)
    script_path = os.path.join(tempfile.gettempdir(), "RenpyLens_update_runner.bat")

    script = f"""@echo off
setlocal
set "NEW_EXE={new_exe_path}"
set "TARGET_EXE={target_exe_path}"
set "TARGET_PID={int(current_pid)}"

for /L %%i in (1,1,120) do (
  tasklist /FI "PID eq %TARGET_PID%" | find "%TARGET_PID%" >nul
  if errorlevel 1 goto :copy_new
  timeout /t 1 >nul
)

:copy_new
for /L %%j in (1,1,30) do (
  move /Y "%NEW_EXE%" "%TARGET_EXE%" >nul 2>nul
  if not errorlevel 1 goto :launch_new
  timeout /t 1 >nul
)
exit /b 1

:launch_new
start "" "%TARGET_EXE%"
del "%~f0"
exit /b 0
"""

    try:
        with open(script_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(script)
    except Exception as e:
        return False, f"Failed to create updater script: {e}"

    try:
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            ["cmd", "/c", script_path],
            creationflags=creation_flags,
            close_fds=True,
        )
        return True, None
    except Exception as e:
        return False, f"Failed to launch updater script: {e}"
