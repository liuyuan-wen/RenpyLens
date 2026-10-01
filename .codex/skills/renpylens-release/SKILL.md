---
name: renpylens-release
description: 用户明确要求发布或继续发布 RenpyLens 新版本时，执行版本更新、测试、构建、Git 推送与 GitHub Release 核验。仅修改版本号、说明或单独打包时不使用。
---

## 版本发布完整流程

只有用户明确要求发布新版本时，才执行本节中的构建、提交、推送、打标签和创建 GitHub Release 等外部写入操作。

### 1. 更新版本号和发布说明

按仓库根目录 [AGENTS.md](../../../AGENTS.md) 的“版本更新”规则同步代码版本号、中英文 README 和 EXE 文件名，并检查版本引用。

### 2. 发布前检查和测试

- 先运行 `git status --short` 和 `git diff --check`，区分本次修改与用户已有修改，不得覆盖或丢弃无关改动。
- 运行完整测试：

```powershell
python -m unittest discover -s tests -v
```

- 测试失败时不得继续发布；先修复失败或向用户说明阻塞原因。

### 3. 准备 EXE

用户已明确说明目标版本打包完成时，按仓库根目录 [AGENTS.md](../../../AGENTS.md) 的“构建与打包”规则使用现有文件，继续第 4 步。

需要本流程构建时，运行：

```powershell
python build.py
```

构建脚本会从 `src/config.py` 读取版本，并在项目根目录生成 `RenpyLens_vX.Y.Z.exe`。

PyInstaller 或 UPX 的单项警告不等于构建失败；以 `build.py` 的退出状态、最终成功信息和 EXE 是否存在为准。

### 4. 提交并推送源码

- 发布产物必须对应已提交源码。提交前再次检查暂存内容，避免把日志、缓存、调试文件、密钥或其他无关文件带入提交。
- `RenpyLens_*.exe` 默认被忽略，不应加入源码提交；它只作为 Release 资产上传。
- 推荐按明确路径暂存本次文件，再提交：

```powershell
git add -- <本次发布涉及的文件>
git diff --cached --stat
git commit -m "feat: release vX.Y.Z"
```

- 只同步当前发布需要的远程分支，不要用可能覆盖本地同名历史标签的操作：

```powershell
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
```

- 确认远端没有目标标签，且分支不存在未处理的分歧后，推送提交和带说明的标签：

```powershell
git ls-remote --tags origin refs/tags/vX.Y.Z
git push origin main
git tag -a vX.Y.Z -m "RenpyLens vX.Y.Z"
git push origin refs/tags/vX.Y.Z
```

如果目标标签或 Release 已存在，不得直接覆盖或重建；应先检查其指向和资产，再按用户要求更新。

### 5. 创建 GitHub Release 并上传 EXE

- Release 标题使用 `RenpyLens vX.Y.Z`，选择已推送的同名标签，设为正式版本和最新版本，不标记为草稿或预发布。
- Release 正文只写 `## Highlights` 及功能亮点，不添加其它小节如 `Windows download` 或 `Documentation`。
- 在 PowerShell 中传递 Release 正文时，不要使用字面量 `\\n` 作为换行；使用 here-string（`@' ... '@`）或实际换行。
- 上传 `RenpyLens_vX.Y.Z.exe`，发布说明中的功能必须与 README 和实际改动一致。
- 如果本机已安装并登录 GitHub CLI，可直接使用 `gh release create`。如果没有 `gh`，可使用 GitHub 官方 REST API，并通过 Git Credential Manager 临时取得现有 GitHub 凭据。凭据只保存在当前 PowerShell 进程变量中，禁止打印、写入文件、日志、命令参数或提交到仓库。

没有 `gh` 时，按 [GitHub REST API 发布示例](references/github-rest.md) 执行。

若 Release 已存在且只需修改说明，应先通过 `/releases/tags/vX.Y.Z` 获取 Release ID，再对 `/releases/{id}` 发送 `PATCH`；不得重复创建。若需替换已上传的同名 EXE，应先向用户说明该操作会删除远程资产并取得明确确认。

### 6. 发布后核验

- 读取 `https://api.github.com/repos/liuyuan-wen/RenpyLens/releases/latest`，确认：
  - `tag_name` 和标题正确；
  - `draft` 与 `prerelease` 均为 `false`；
  - Release 确实是最新版本；
  - EXE 资产名称和状态正确；
  - 远程资产大小与本地文件一致；
- 最后运行 `git status -sb`，确认 `main` 与 `origin/main` 同步，并向用户提供 Release 页面、EXE 下载链接、提交哈希、文件大小。
