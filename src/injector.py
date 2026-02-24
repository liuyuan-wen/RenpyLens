# -*- coding: utf-8 -*-
"""注入器 - 复制 hook 脚本到游戏 game/ 目录，启动游戏"""

import os
import shutil
import subprocess


def find_game_dir(exe_path: str) -> str | None:
    """从游戏 exe 路径推断 game/ 目录位置"""
    exe_dir = os.path.dirname(os.path.abspath(exe_path))

    # 典型 Ren'Py 结构: game_root/game/ 存在于 exe 同级目录
    game_dir = os.path.join(exe_dir, "game")
    if os.path.isdir(game_dir):
        return game_dir

    return None


def is_renpy_game(exe_path: str) -> bool:
    """检测是否为 Ren'Py 游戏"""
    exe_dir = os.path.dirname(os.path.abspath(exe_path))

    # Ren'Py 游戏特征：有 game/ 目录 + renpy/ 目录 或 lib/ 目录
    game_dir = os.path.join(exe_dir, "game")
    renpy_dir = os.path.join(exe_dir, "renpy")
    lib_dir = os.path.join(exe_dir, "lib")

    if not os.path.isdir(game_dir):
        return False

    if os.path.isdir(renpy_dir) or os.path.isdir(lib_dir):
        return True

    # 备选检测：game/ 下有 .rpy 或 .rpyc 文件
    for f in os.listdir(game_dir):
        if f.endswith((".rpy", ".rpyc", ".rpa")):
            return True

    return False


def inject_hook(exe_path: str, hook_rpy_path: str) -> tuple[bool, str]:
    """将 hook 脚本注入到游戏的 game/ 目录"""
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
        shutil.copy2(hook_rpy_path, dest)
        # 删除旧的 .rpyc 缓存，强制 Ren'Py 重新编译
        if os.path.exists(dest_rpyc):
            os.remove(dest_rpyc)
    except Exception as e:
        return False, f"Failed to copy hook script: {e}"

    return True, f"Injected to: {dest}"


def remove_hook(exe_path: str) -> tuple[bool, str]:
    """从游戏中移除 hook 脚本"""
    game_dir = find_game_dir(exe_path)
    if not game_dir:
        return False, "game/ directory not found"

    hook_file = os.path.join(game_dir, "_translator_hook.rpy")
    hook_compiled = os.path.join(game_dir, "_translator_hook.rpyc")

    removed = []
    for f in [hook_file, hook_compiled]:
        if os.path.exists(f):
            try:
                os.remove(f)
                removed.append(os.path.basename(f))
            except Exception as e:
                return False, f"Failed to delete: {e}"

    if removed:
        return True, f"Removed: {', '.join(removed)}"
    return True, "No files to remove"


def launch_game(exe_path: str) -> subprocess.Popen | None:
    """启动游戏进程"""
    try:
        process = subprocess.Popen(
            [exe_path],
            cwd=os.path.dirname(exe_path),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return process
    except Exception:
        return None
