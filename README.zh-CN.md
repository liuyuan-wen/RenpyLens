[![English](https://img.shields.io/badge/Language-English-4a9eff)](README.md)

# <img src="assets/icon.png" width="48" align="absmiddle"/> RenpyLens

RenpyLens 是一款面向 Windows 的轻量级实时 AI 游戏翻译浮窗工具，支持 **Ren'Py** 和 **RPG Maker MV/MZ**。

将游戏主程序拖入软件、选择翻译服务后，RenpyLens 会通过引擎原生桥接捕获对白、说话人和当前可见选项，并在可移动浮窗中显示译文，不会替换游戏原文。

软件界面支持 **简体中文、繁體中文、English、日本語、한국어和Русский**。

## 💬 社区与支持

- **Discord：**[https://discord.gg/c4putqY5zs](https://discord.gg/c4putqY5zs)
- **中文 QQ 交流群：**`1058127921`

欢迎加入社区获取技术支持、反馈问题、了解版本动态和体验内测功能。

## ✨ 核心特性

- **拖拽式操作：**将游戏 `.exe` 拖入 RenpyLens，即可安装翻译桥接并启动游戏。
- **多游戏引擎：**支持提取 Ren'Py 与 RPG Maker MV/MZ 的对白、说话人和可见选项。
- **灵活的 AI 服务：**
  - **内置通道：**提供开箱即用的中国大陆与全球线路。
  - **云端服务：**支持 OpenAI、Gemini、Anthropic Claude、DeepSeek、OpenRouter、Groq、MiniMax、硅基流动、月之暗面、xAI、阿里通义、火山引擎、智谱 AI 等。
  - **Ollama：**支持本地离线模型。
  - **自定义通道：**可添加任意 OpenAI 兼容 API 地址。
- **实时翻译浮窗：**使用无边框、可移动、可调节尺寸的浮窗在游戏上方显示译文。
- **RPGM工具：**可按游戏选择文字速度、对话框透明度、自动翻页、高速移动、穿墙、随机遇敌和一键战胜/战败。
- **翻译工作台：**游戏过程中可查看和编辑最近出现的对白与选项。
- **一键翻译整个游戏：**扫描受支持的游戏脚本并分批翻译，提供进度、频率限制和取消功能。
- **翻译记忆：**通过 SQLite 与内存缓存减少重复请求并保留人工校对结果。
- **多语言界面：**可直接在主界面切换六种界面语言。

## 📸 运行效果

<img src="assets/example.jpg" alt="RenpyLens 翻译浮窗" width="600">

## 🆕 版本更新

### v1.5.2 `最新`

- **RPGM 稳定性：**避免被阻塞的强制移动路线永久锁住自动执行剧情。
- **浮窗交互：**补偿丢失的拖动和调整大小释放事件，避免浮窗卡在鼠标操作状态。
- **便携打包：**当内置 `pyexpat` 依赖 Conda 的 `libexpat.dll` 时自动将其加入程序，修复干净 Windows 环境启动失败。

### v1.5.1

- **RPGM 工具：**新增按游戏保存的文字速度、对话框透明度、自动翻页、移动、穿墙、遇敌和战斗结果控制。
- **上下文翻译优化：**将当前可见文本与后续对白合并请求，在改善上下文的同时避免重复翻译。
- **界面体验改进：**优化模型输入、工作台窗口状态、本地化支持信息和 RPG Maker 工具说明。

### v1.5.0

- **完整多语言界面：**支持简体中文、繁體中文、English、日本語、한국어和Русский，并可在主界面快速切换。
- **内置通道升级：**更新中国大陆与全球节点，启用严格 TLS 校验，并新增线路延迟测试。
- **API 通道扩展：**重构 OpenAI 兼容通道配置，支持预设服务商和用户自定义通道管理。
- **全球社区支持：**新增 Discord，并优化各语言节点名称、状态提示和界面布局。
- **兼容性与稳定性：**改进 Ren'Py、RPG Maker MV/MZ 的 Hook 清理、文本提取、限流提示和旧配置迁移。

### v1.2.0～v1.3.0

- 新增 RPG Maker MV/MZ 适配，并增强 Ren'Py 对白、说话人、选项和连续对白提取。
- 新增翻译工作台、浮窗快捷校对和全游戏批量翻译，支持进度、取消及人工译文缓存。
- 优化预取任务、全游戏扫描时机、启动器与包装 EXE 跟踪，避免重复启动和游戏状态误判。
- 增加 API 超时及批量限制设置、429 限流提示、GitHub Release 下载和自动更新。

### v1.1.0～v1.1.4

- 优化翻译提示词、模型行为、缓存、并发及思维链输出清理。
- 扩大 Ren'Py Hook 与菜单兼容范围，支持当前可见选项翻译。
- 新增说话人、斜体、字体和颜色定制及强制置顶浮窗。
- 改进 OpenAI 兼容接口、运行时引擎切换、试用 API 状态和到期查询。

## 🎮 使用方法

1. **下载 RenpyLens**
   - 从仓库右侧的 **Releases** 页面下载最新版 `RenpyLens_v1.5.2.exe`。
   - 也可以按照下方开发说明从源码运行。
2. **选择翻译服务**
   - 若希望快速体验，选择 **内置通道**，然后点击 **获取试用 API**。
   - 若使用自己的服务，打开 **设置 → API 设置**，配置服务商、Ollama 或自定义 OpenAI 兼容通道。
3. **选择游戏**
   - 将 Ren'Py、RPG Maker MV 或 RPG Maker MZ 游戏的主程序 `.exe` 拖入软件窗口。
4. **安装 Hook 并启动**
   - 点击 **装载 Hook 并开始游戏**，软件会自动安装相应桥接并启动游戏。
   - Ren'Py 桥接安装在 `game/`；RPG Maker 桥接安装在 `js/plugins/`。
   - 导入 RPG Maker MV/MZ 后，可在翻译引擎与模型右侧开启“RPGM工具”，并从旁边的菜单勾选所需功能。
5. **使用浮窗**
   - 拖动浮窗可调整位置。
   - 右击浮窗可打开显示设置、翻译编辑和工作台。
6. **按需卸载 Hook**
   - 点击 **卸载 Hook**，即可安全删除 RenpyLens 安装的桥接文件。

> **模型建议：**实时翻译更适合不会输出长篇思维链或推理过程的模型。如果服务商无法关闭这类输出，翻译延迟可能增加，推理内容也可能混入译文。

## 🛠️ 代码开发

### 环境要求

- 推荐 Windows 10 或更高版本
- Python 3.10+

### 克隆与安装

```powershell
git clone https://github.com/liuyuan-wen/RenpyLens.git
cd RenpyLens

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 从源码运行

```powershell
python src/main.py
```

### 运行测试

```powershell
python -m unittest discover -s tests -v
```

## 📦 打包 Windows 可执行文件

```powershell
# 所选 Python 环境需要安装 PyInstaller。
python build.py

# 也可以指定 Python 解释器。
python build.py --python "C:\path\to\venv\python.exe"
```

打包产物为 `RenpyLens_xxx.exe`。如果项目根目录存在 `upx.exe`，打包脚本默认会使用它进行压缩。如果生成的程序被杀毒软件拦截或无法启动，可以使用以下命令禁用 UPX 后重新打包：

```powershell
python build.py --noupx
```

## 🧩 项目结构

- **`src/main.py` 与 `src/settings_dialog.py`：**PyQt5 主界面和应用流程。
- **`src/workbench.py`：**最近条目查看、人工校对和全游戏翻译状态。
- **`src/updater.py`：**GitHub Release 检查、下载和 Windows 自动更新。
- **`src/engine_adapters.py`：**游戏引擎识别、桥接安装及 RPG Maker 事件离线扫描。
- **`src/injector.py`：**Ren'Py 识别、启动处理及 Hook 安全安装。
- **`src/translator.py`：**翻译服务、连接池、批处理和限流处理。
- **`src/hook_server.py`：**RenpyLens 与游戏桥接之间的本地通信。
- **`src/cache.py`：**基于 SQLite 的翻译记忆。
- **`assets/_translator_hook.rpy`：**Ren'Py 运行时桥接。
- **`assets/RenpyLensBridge.js`：**RPG Maker MV/MZ 运行时桥接。

## 📄 开源协议

RenpyLens 使用 [GNU General Public License v3.0](LICENSE) 开源。
