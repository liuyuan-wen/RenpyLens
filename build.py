import os
import subprocess
import sys
import shutil
import argparse


def find_python_runtime_dirs(python_exe):
    """Locate DLL directories supplied by the selected Python distribution."""
    script = """
import os
import sys

roots = [sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)]
seen = set()
for root in dict.fromkeys(roots):
    for relative_dir in (os.path.join("Library", "bin"), "DLLs", ""):
        candidate = os.path.normpath(os.path.join(root, relative_dir))
        if candidate not in seen and os.path.isdir(candidate):
            seen.add(candidate)
            print(candidate)
"""
    try:
        output = subprocess.check_output(
            [python_exe, "-c", script],
            text=True,
        )
        return [line for line in output.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return []


def select_output_name(version, output_dir):
    """Use a numbered name when the normal output executable is in use."""
    base_name = f"RenpyLens_{version}"
    target = os.path.join(output_dir, f"{base_name}.exe")
    if not os.path.isfile(target):
        return base_name

    try:
        with open(target, "r+b"):
            return base_name
    except PermissionError:
        suffix = 1
        while os.path.exists(os.path.join(output_dir, f"{base_name}_{suffix}.exe")):
            suffix += 1
        return f"{base_name}_{suffix}"


def build_exe():
    parser = argparse.ArgumentParser(description="打包 RenpyLens")
    parser.add_argument("--python", type=str, default=sys.executable,
                        help="指定用于打包的 Python 解释器路径 (默认使用当前运行的 Python)")
    parser.add_argument("--noupx", action="store_true",
                        help="Disable UPX compression (use if the built executable is blocked or fails to start)")
    args = parser.parse_args()

    # 获取版本号
    sys.path.append(os.path.join(os.getcwd(), "src"))
    try:
        from config import DEFAULT_CONFIG
        version = DEFAULT_CONFIG.get("version", "v1.5.2.1")
    except ImportError:
        version = "v1.5.2.1"

    print(f"开始打包 RenpyLens {version}...")
    
    # 确保在当前目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    output_name = select_output_name(version, os.getcwd())
    if output_name != f"RenpyLens_{version}":
        print(f"[WARN] 标准输出文件正在使用，将改为生成 {output_name}.exe")
    
    # 使用指定的 Python 解释器
    python_exe = args.python
    if not os.path.exists(python_exe):
        print(f"[WARN] 指定的 Python 解释器不存在: {python_exe}，将回退使用默认解释器")
        python_exe = sys.executable

    # PyInstaller does not always search Conda's Library/bin when the selected
    # interpreter is invoked outside an activated environment. Add all runtime
    # DLL directories to its search path so extension-module dependencies such
    # as OpenSSL, Expat, SQLite, and compression libraries are collected.
    runtime_dll_dirs = find_python_runtime_dirs(python_exe)
    build_env = os.environ.copy()
    if runtime_dll_dirs:
        build_env["PATH"] = os.pathsep.join(
            runtime_dll_dirs + [build_env.get("PATH", "")]
        )

    libexpat_path = next(
        (
            os.path.join(directory, "libexpat.dll")
            for directory in runtime_dll_dirs
            if os.path.isfile(os.path.join(directory, "libexpat.dll"))
        ),
        "",
    )

    qt_translation_dir = ""
    try:
        qt_translation_dir = subprocess.check_output(
            [
                python_exe,
                "-c",
                "from PyQt5.QtCore import QLibraryInfo; "
                "print(QLibraryInfo.location(QLibraryInfo.TranslationsPath))",
            ],
            text=True,
            env=build_env,
        ).strip()
    except Exception as exc:
        print(f"[WARN] 无法定位 Qt 翻译资源，将依赖 PyInstaller 默认收集: {exc}")

    # 构建 PyInstaller 命令
    # UPX is enabled by default when upx.exe is available. Use --noupx to disable it.
    upx_options = ["--noupx"] if args.noupx else ["--upx-dir", "."]

    command = [
        python_exe, "-m", "PyInstaller",
        "--name", output_name,
        "--windowed", # 隐藏控制台窗口
        "--onefile",   # --onedir 可以打包成一个目录
        "--paths", "src", # 将 src 目录添加到模块搜索路径
        "--add-data", "assets/_translator_hook.rpy;.", # 包含必须的资源文件
        "--add-data", "assets/RenpyLensBridge.js;.", # RPG Maker MV/MZ 运行时桥接
        "--add-data", "assets/icon.ico;.", # 包含图标以便程序运行时提取
        "--add-data", "assets/icon.png;.", 
        "--add-data", "assets/locales;assets/locales",
        "--icon", "assets/icon.ico", # 指定程序本身的图标
        
        # 排除体积巨大且程序明显用不到的科学计算和系统级大包
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "IPython",
        "--exclude-module", "jupyter",
        "--exclude-module", "notebook",
        "--exclude-module", "zmq",
        "--exclude-module", "tornado",
        "--exclude-module", "PIL", # pillow 如果没用到
        "--exclude-module", "tkinter",
        "--exclude-module", "sklearn",
        "--exclude-module", "plotly",
        "--exclude-module", "dash",
        "--exclude-module", "pyinstaller",
        
        # 排除不再使用的库和无用的庞大标准库
        "--exclude-module", "requests",
        "--exclude-module", "urllib3",
        "--exclude-module", "unittest",
        "--exclude-module", "html",
        "--exclude-module", "http.server",
        "--exclude-module", "xmlrpc",
        "--exclude-module", "pydoc",
        
        # PyQt5 优化：排除不使用的庞大模块
        "--exclude-module", "PyQt5.QtSql",
        "--exclude-module", "PyQt5.QtMultimedia",
        "--exclude-module", "PyQt5.QtBluetooth",
        "--exclude-module", "PyQt5.QtPositioning",
        "--exclude-module", "PyQt5.QtWebSockets",
        "--exclude-module", "PyQt5.QtWebChannel",
        "--exclude-module", "PyQt5.QtWebEngineCore",
        "--exclude-module", "PyQt5.QtWebEngine",
        "--exclude-module", "PyQt5.QtWebEngineWidgets",
        "--exclude-module", "PyQt5.QtXml",
        "--exclude-module", "PyQt5.QtTest",
        "--exclude-module", "PyQt5.QtPrintSupport",
        "--exclude-module", "PyQt5.QtSensors",
        "--exclude-module", "PyQt5.QtSerialPort",
        "--exclude-module", "PyQt5.QtNfc",
        "--exclude-module", "PyQt5.QtQuick",
        "--exclude-module", "PyQt5.QtQuickWidgets",
        "--exclude-module", "PyQt5.QtQuick3D",
        "--exclude-module", "PyQt5.QtQml",
        
        # 默认启用 UPX 压缩；使用 --noupx 时禁用
        *upx_options,
        
        "--clean",
        "--noconfirm",
        "--distpath", ".",  # 将输出目录修改为当前目录，不使用默认的 dist
        "src/main.py"
    ]

    if libexpat_path:
        command[command.index("src/main.py"):command.index("src/main.py")] = [
            "--add-binary", f"{libexpat_path};."
        ]

    if qt_translation_dir and os.path.isdir(qt_translation_dir):
        for locale in ("zh_CN", "zh_TW", "en", "ja", "ko", "ru"):
            for prefix in ("qt", "qtbase"):
                qm_path = os.path.join(qt_translation_dir, f"{prefix}_{locale}.qm")
                if os.path.isfile(qm_path):
                    command[command.index("src/main.py"):command.index("src/main.py")] = [
                        "--add-data", f"{qm_path};assets/qt_translations"
                    ]
    
    print(f"运行命令: {' '.join(command)}")
    result = subprocess.run(command, env=build_env)
    
    if result.returncode == 0:
        print("\n[OK] 打包成功！")
        print(f"打包生成的文件 '{output_name}.exe' 已经直接放在当前代码目录下。")
        print(f"您可以直接双击 '{output_name}.exe' 运行，或者将其发给用户（无需安装 Python）。")
    else:
        print("\n[ERROR] 打包失败，请查看上面的错误信息。")

    # ========= 打包后清理临时文件 =========
    print("\n[清理] 正在清理打包过程中产生的临时文件...")
    
    # 1. 清理 build/ 目录 (里面全是编译时的临时中间态对象)
    build_dir = os.path.join(os.getcwd(), "build")
    if os.path.exists(build_dir):
        try:
            shutil.rmtree(build_dir)
            print(f"[OK] 已删除临时构建目录: {build_dir}")
        except Exception as e:
            print(f"[WARN] 删除 build 目录失败: {e}")
            
    # 2. 清理产生的 .spec 文件
    spec_file = os.path.join(os.getcwd(), f"{output_name}.spec")
    if os.path.exists(spec_file):
        try:
            os.remove(spec_file)
            print(f"[OK] 已删除临时配置: {spec_file}")
        except Exception as e:
            print(f"[WARN] 删除 spec 文件失败: {e}")

if __name__ == "__main__":
    build_exe()
