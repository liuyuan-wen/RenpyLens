import os
import subprocess
import sys
import shutil
import argparse

def build_exe():
    parser = argparse.ArgumentParser(description="打包 RenpyLens")
    parser.add_argument("--python", type=str, default=sys.executable,
                        help="指定用于打包的 Python 解释器路径 (默认使用当前运行的 Python)")
    args = parser.parse_args()

    print("开始打包 RenpyLens...")
    
    # 确保在当前目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # 使用指定的 Python 解释器
    python_exe = args.python
    if not os.path.exists(python_exe):
        print(f"[WARN] 指定的 Python 解释器不存在: {python_exe}，将回退使用默认解释器")
        python_exe = sys.executable

    # 构建 PyInstaller 命令
    command = [
        python_exe, "-m", "PyInstaller",
        "--name", "RenpyLens",
        "--windowed", # 隐藏控制台窗口
        "--onefile",   # --onedir 可以打包成一个目录
        "--paths", "src", # 将 src 目录添加到模块搜索路径
        "--add-data", "assets/_translator_hook.rpy;.", # 包含必须的资源文件
        "--add-data", "assets/icon.ico;.", # 包含图标以便程序运行时提取
        "--add-data", "assets/icon.png;.", 
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
        
        # 启用 UPX 压缩 (需要当前目录有 upx.exe)
        "--upx-dir", ".",
        
        "--clean",
        "--noconfirm",
        "--distpath", ".",  # 将输出目录修改为当前目录，不使用默认的 dist
        "src/main.py"
    ]
    
    print(f"运行命令: {' '.join(command)}")
    result = subprocess.run(command)
    
    if result.returncode == 0:
        print("\n[OK] 打包成功！")
        print("打包生成的文件 'RenpyLens.exe' 已经直接放在当前代码目录下。")
        print("您可以直接双击 'RenpyLens.exe' 运行，或者将其发给用户（无需安装 Python）。")
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
    spec_file = os.path.join(os.getcwd(), "RenpyLens.spec")
    if os.path.exists(spec_file):
        try:
            os.remove(spec_file)
            print(f"[OK] 已删除临时配置: {spec_file}")
        except Exception as e:
            print(f"[WARN] 删除 spec 文件失败: {e}")

if __name__ == "__main__":
    build_exe()
