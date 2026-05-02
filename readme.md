# Codex Session Toolkit

`Codex Session Toolkit` 是一个面向 Codex Desktop / CLI 会话的 TUI 优先工具箱，用来浏览会话、导出 Bundle、跨设备导入、同步 Skills、修复 Desktop 可见性，并把本地 Bundle 工作区同步到独立 GitHub 仓库。

它不是单个脚本，而是一套统一的交互式 TUI + 可脚本化 CLI。日常使用建议从 TUI 进入；CLI 适合批处理、dry-run 和自动化。

![Codex Session Toolkit 界面预览](./assets/12345.png)

## 解决什么问题

如果你有这些场景，这个工具就是为它们设计的：

- 在 Codex Desktop 和 Codex CLI 之间切换，想快速找到某个会话
- 换电脑后，希望把旧机器的会话按项目导入到新机器
- 只想同步某个项目的会话，而不是把整台机器的会话混在一起
- 希望会话导入后仍能在 Codex Desktop 左侧线程栏里正常显示
- 自定义 Skills 分散在多台机器上，需要独立导出、导入和去重
- 想把 `./codex_bundles` 同步到 GitHub，但不想和本项目源码仓库混在一起
- 切换 provider 或账号登录模式后，旧会话需要安全迁移或修复
- 导入覆盖前想自动保留备份，必要时能在 TUI 中找回

## 核心设计

- **TUI 优先**：常用能力都在交互界面中完整提供，避免用户记复杂命令。
- **Bundle 中转**：所有跨设备会话和 Skills 都先进入 `./codex_bundles/`，不直接在两台机器之间改 `~/.codex/`。
- **独立 GitHub 同步**：GitHub 同步只针对 `./codex_bundles/`，必须连接用户新建的独立仓库，不允许同步到本项目源码仓库。
- **会话和 Skills 分层**：会话 Bundle 只携带本会话实际依赖的自定义 Skills；全量 Skill 库同步走 `Skills / Transfer`。
- **保守写入**：导入、修复、清理、同步等写入动作支持 Dry-run；危险操作会二次确认。
- **可恢复**：导入覆盖本机会话前会生成 `.bak.<timestamp>` 备份，TUI 可浏览、恢复或删除。

## 快速开始

### macOS / Linux

```bash
chmod +x ./install.sh ./install.command ./codex-session-toolkit ./codex-session-toolkit.command
./install.sh
./codex-session-toolkit
```

macOS 也可以双击：

- `install.command`
- `codex-session-toolkit.command`

### Windows

双击 `install.bat`，或运行：

```powershell
.\install.ps1
.\codex-session-toolkit.cmd
```

安装脚本会在项目根目录创建隔离的本地 `.venv/`，不会污染 base Python 环境。安装后也可以直接运行：

```bash
./.venv/bin/codex-session-toolkit
```

Windows：

```powershell
.\.venv\Scripts\codex-session-toolkit.cmd
```

查看版本：

```bash
./codex-session-toolkit --version
```

## TUI 五大功能域

无参数启动会进入主界面：

```bash
codex-session-toolkit
```

主菜单包含 5 个功能域：

1. `Session / Browse`
2. `Bundle / Transfer`
3. `Skills / Transfer`
4. `Repair / Maintenance`
5. `GitHub / Sync`

### Session / Browse

用于找会话、看详情、按项目组织会话。

- 浏览最近会话
- 搜索 session id、标题、预览、provider、cwd
- 查看会话详情
- 导出单个会话为 Bundle
- 粘贴项目路径，只浏览这个项目下的会话
- 按项目批量导出当前项目下的全部会话

项目导出会写入：

```text
./codex_bundles/<machine>/sessions/project/<project>/<timestamp>/<session_id>/
```

### Bundle / Transfer

用于浏览、校验、导出和导入会话 Bundle。

- 浏览 `./codex_bundles/` 中的 Bundle
- 校验 Bundle manifest、session JSONL、history JSONL
- 批量导出全部 Desktop 会话
- 批量导出 active Desktop 会话
- 批量导出 CLI 会话
- 导入单个 Bundle 为会话
- 按 `设备 -> 分类 -> 项目` 批量导入
- 导入 project 分类时，可把源机器 cwd 映射到当前机器项目目录
- 导入时自动维护 `session_index.jsonl`、Desktop `threads` 表和 workspace roots

### Skills / Transfer

用于管理自定义 Skills 的独立迁移。

- 浏览本机 Skills，默认只显示自定义 Skills
- 导出单个自定义 Skill
- 导出全部自定义 Skills 为 standalone Skills Bundle
- 浏览 standalone Skills Bundle
- 导入单个 Skills Bundle
- 批量导入 Skills Bundle，可按来源机器过滤
- 删除本机自定义 Skill，删除前会确认

`.agents/skills/foo` 和 `.codex/skills/foo` 会按同一个相对 Skill 识别，避免跨根目录重复导入。

### Repair / Maintenance

用于修复、迁移和找回备份。

- 迁移到当前 Provider
- 修复会话在 Codex Desktop 中显示
- 管理会话备份
- 清理旧版无标记副本
- 支持 Dry-run 预演
- 可选把尚未登记的 CLI 会话纳入 Desktop

`repair-desktop` 默认只修复 active 会话。需要 archived 会话时，CLI 可加 `--include-archived`。

### GitHub / Sync

用于把 `./codex_bundles/` 同步到独立 GitHub 仓库。

菜单顺序：

1. `连接独立 GitHub 仓库`
2. `查看 GitHub 同步状态`
3. `从 GitHub 拉取更新`
4. `推送本机更新到 GitHub`

同步范围包含：

- 会话 Bundle：`sessions/`
- standalone Skills Bundle：`skills/`

GitHub 同步不会直接提交或推送 `~/.codex/` 原始会话目录。

## 推荐工作流

### 1. 导出某个项目的全部会话

1. 打开 TUI。
2. 进入 `Session / Browse`。
3. 选择 `按项目路径查看并导出会话`。
4. 粘贴项目根目录路径。
5. 确认匹配到的会话列表。
6. 按 `x` 批量导出。
7. 第一次建议选择 Dry-run，预览完成后按 Enter 会回到执行选择页。

### 2. 在另一台电脑导入项目会话

1. 把源机器的 `./codex_bundles/` 拷贝过来，或先从 GitHub 拉取更新。
2. 打开 TUI。
3. 进入 `Bundle / Transfer`。
4. 选择 `批量导入 Bundle 为会话`。
5. 依次选择 `设备 -> project 分类 -> 项目文件夹`。
6. 查看工具识别出的本机项目路径。
7. 必要时修改目标项目路径。
8. 选择是否自动创建缺失目录。
9. 执行导入。

导入时如果会覆盖本机旧 rollout，工具会先备份旧文件。

### 3. 同步自定义 Skills

源机器：

1. 进入 `Skills / Transfer`。
2. 选择 `导出全部自定义 Skills`。
3. 得到 `./codex_bundles/<machine>/skills/all/<timestamp>/`。

目标机器：

1. 进入 `Skills / Transfer`。
2. 选择 `导入单个 Skills Bundle` 或 `批量导入 Skills Bundle`。
3. 内容一致的 Skill 会直接复用。
4. 内容冲突默认跳过，不覆盖本机版本。

### 4. 连接 GitHub 同步仓库

先在 GitHub 上新建一个独立仓库，例如：

```text
git@github.com:you/codex-bundles.git
```

然后在 TUI 中：

1. 进入 `GitHub / Sync`。
2. 选择 `连接独立 GitHub 仓库`。
3. 输入这个新仓库地址。
4. 工具会拒绝连接到当前项目源码仓库 remote。
5. 连接后可选择是否立即首次推送本机 Bundle。

连接动作支持 Dry-run。Dry-run 结束后按 Enter 会回到同一个执行选择页，不会回主菜单。

### 5. 拉取 / 推送 GitHub 更新

拉取：

1. 进入 `GitHub / Sync`。
2. 选择 `从 GitHub 拉取更新`。
3. TUI 会显示当前连接的 remote 和分支，例如 `origin/main`。
4. 选择 `拉取`、`Dry-run 预览` 或 `返回`。

推送：

1. 进入 `GitHub / Sync`。
2. 选择 `推送本机更新到 GitHub`。
3. TUI 会显示待同步变更数量、会话变更数量和 Skills 变更数量。
4. 选择 `推送`、`Dry-run 预览` 或 `返回`。

同步策略：

- 状态页会先显示本地快照，再用进度条检查远端更新时间。
- 首页、Bundle、Skills 页面只显示本地同步提示，不会偷偷联网检查远端。
- 导入手动拷贝来的 Bundle 时不会弹强制拉取提示。
- 拉取或推送前会检查远端更新。
- 可自动合并时自动合并。
- 文件冲突时停止并列出冲突文件，避免静默覆盖。
- 路径展示统一为 POSIX 风格，兼容 macOS `/` 与 Windows `\` 输入。

### 6. 找回导入覆盖前的本机会话

1. 进入 `Repair / Maintenance`。
2. 选择 `管理会话备份`。
3. 按 `/` 搜索 session id、provider、cwd 或路径。
4. 按 `d` 查看详情。
5. 按 `r` 恢复选中备份。
6. 按 `x` 删除不再需要的备份。
7. 输入 `DELETE` 二次确认。

恢复前如果当前 rollout 仍存在，工具会先生成一份 `rollout-xxx.jsonl.bak.restore.<timestamp>`，再恢复选中的备份。

## 常用按键

主界面和功能页：

| 按键 | 作用 |
|---|---|
| `↑/↓` 或 `j/k` | 移动 |
| `Enter` | 进入功能页或执行当前动作 |
| `←/→` | 切换功能页 |
| `PgUp/PgDn` | 切换功能页 |
| `h` | 打开帮助 |
| `q` | 返回或退出 |
| `0` | 直接退出 |

二级选择页：

| 按键 | 作用 |
|---|---|
| `↑/↓` 或 `j/k` | 选择执行方式、修复范围或同步方式 |
| `Enter` | 确认当前选项 |
| `q` / `←` / `Esc` | 返回上一步 |

浏览器页面：

| 按键 | 作用 |
|---|---|
| `/` | 搜索会话、Bundle、Skill 或备份 |
| `Enter` | 打开当前条目的操作面板，选择模式下直接确认 |
| `d` | 查看详情 |
| `e` | 在会话列表中导出当前会话 |
| `p` | 在项目会话浏览器中重新输入项目路径 |
| `x` | 在项目会话浏览器导出当前项目全部会话；在 Skills 列表导出全部自定义 Skills；在备份列表删除备份 |
| `s` | 切换 Bundle 导出方式 |
| `m` | 按导出机器切换 Bundle 搜索范围 |
| `l` | 切换显示全部历史 Bundle / 仅显示最新 Bundle |
| `i` / `v` | 导入当前 Bundle / 导入并自动创建缺失目录 |
| `g` | 在 Skills 列表切换是否显示系统/运行时 Skills |
| `r` | 在 Skills 列表删除自定义 Skill；在备份列表恢复备份 |

## CLI 速查

### 启动和浏览

```bash
codex-session-toolkit
codex-session-toolkit --version
codex-session-toolkit list
codex-session-toolkit list <session_id_or_keyword>
codex-session-toolkit list-project-sessions /Users/example/project-a
codex-session-toolkit list-bundles
codex-session-toolkit validate-bundles
```

### 导出会话

```bash
codex-session-toolkit export <session_id>
codex-session-toolkit export-project /Users/example/project-a --dry-run
codex-session-toolkit export-project /Users/example/project-a
codex-session-toolkit export-active-desktop-all
codex-session-toolkit export-desktop-all
codex-session-toolkit export-cli-all
```

### 导入会话

```bash
codex-session-toolkit import <session_id_or_bundle_dir>
codex-session-toolkit import <session_id_or_bundle_dir> --desktop-visible
codex-session-toolkit import-desktop-all --machine Work-Laptop --export-group active
codex-session-toolkit import-desktop-all --machine Work-Laptop --export-group project --project project-a --target-project-path /Users/example/project-a --desktop-visible
```

### Skills

```bash
codex-session-toolkit list-skills
codex-session-toolkit list-skills --include-system
codex-session-toolkit export-skills
codex-session-toolkit export-skills my-skill
codex-session-toolkit list-skill-bundles
codex-session-toolkit import-skill-bundle <skill_bundle_dir_or_skill_name>
codex-session-toolkit import-skill-bundles --machine Work-Laptop
codex-session-toolkit delete-skill my-skill --source-root agents --dry-run
codex-session-toolkit delete-skill my-skill --source-root agents
```

### GitHub 同步

```bash
codex-session-toolkit connect-github git@github.com:you/codex-bundles.git --dry-run
codex-session-toolkit connect-github git@github.com:you/codex-bundles.git
codex-session-toolkit connect-github git@github.com:you/codex-bundles.git --push-after-connect
codex-session-toolkit pull-github --dry-run
codex-session-toolkit pull-github
codex-session-toolkit sync-github --dry-run
codex-session-toolkit sync-github --message "Sync laptop bundles"
codex-session-toolkit sync-github --no-push
```

### 备份和修复

```bash
codex-session-toolkit list-backups
codex-session-toolkit restore-backup <backup_path_or_session_id> --dry-run
codex-session-toolkit restore-backup <backup_path_or_session_id>
codex-session-toolkit delete-backup <backup_path_or_session_id> --dry-run
codex-session-toolkit delete-backup <backup_path_or_session_id>
codex-session-toolkit repair-desktop --dry-run
codex-session-toolkit repair-desktop
codex-session-toolkit repair-desktop --include-cli
codex-session-toolkit repair-desktop --include-archived
codex-session-toolkit clone-provider --dry-run
codex-session-toolkit clean-clones --dry-run
```

## Bundle 目录策略

所有新版 Bundle 动作都围绕当前项目目录下的 `./codex_bundles/` 工作区进行。

默认目录：

- Codex 数据目录：`~/.codex/`
- Bundle 工作区：`./codex_bundles/`

默认结构：

```text
./codex_bundles/<machine>/sessions/single/<timestamp>/<session_id>/
./codex_bundles/<machine>/sessions/desktop/<timestamp>/<session_id>/
./codex_bundles/<machine>/sessions/active/<timestamp>/<session_id>/
./codex_bundles/<machine>/sessions/cli/<timestamp>/<session_id>/
./codex_bundles/<machine>/sessions/project/<project>/<timestamp>/<session_id>/
./codex_bundles/<machine>/skills/single/<timestamp>/
./codex_bundles/<machine>/skills/all/<timestamp>/
```

`<machine>` 默认来自当前电脑主机名。需要手动指定时，可在导出前设置：

```bash
export CST_MACHINE_LABEL=My-MacBook
```

兼容旧布局：

- 工具仍能识别 `./codex_sessions/`
- 工具仍能识别 `./codex_sessions/bundles/`
- 工具仍能识别 `./codex_sessions/desktop_bundles/`
- 新导出默认写入 `./codex_bundles/`

如果手动传入 Bundle 目录，这个目录必须位于 `./codex_bundles/` 或旧版兼容目录下，否则工具会拒绝执行。

## Bundle 内容

会话 Bundle 默认包含：

- `codex/<relative rollout path>.jsonl`
- `history.jsonl`
- `manifest.env`
- `skills_manifest.json`，可选
- `skills/`，可选，只包含本会话实际依赖的自定义 Skill 文件

standalone Skills Bundle 默认包含：

- `manifest.env`
- `skills_manifest.json`
- `skills/`

project 分类还会记录：

- 导出项目名
- 导出项目原路径
- 每个会话的原始 `cwd`

这让目标机器可以按项目筛选，再把 cwd 映射到本机项目目录。

## Skills 搬运规则

会话导出时，工具会读取会话上下文中的 `<skills_instructions>`，区分“可用 Skill”和“实际使用过的 Skill”：

- 实际使用过的自定义 Skill 会被完整打包到 `skills/`
- 只在上下文中可用、但本会话没有实际使用的 Skill 只记录元数据
- 系统 Skill 和运行时 Skill 只记录元数据，不打包文件

导入时默认是 `best-effort`：

| 状态 | 行为 |
|---|---|
| 本机不存在 | 从 Bundle 恢复 |
| 本机已存在且内容一致 | 直接复用 |
| 本机已存在但内容不同 | 跳过，不覆盖 |
| 会话依赖但 Bundle 未携带 | 记录 missing，不阻塞 |

`--skills-mode` 可选：

| 模式 | 行为 |
|---|---|
| `best-effort` | 默认模式，尽量恢复，冲突和缺失记录为 warning |
| `strict` | 缺失、冲突或异常时中止 |
| `skip` | 完全不处理 Skills |
| `overwrite` | 允许覆盖本机已有 Skill |

批量导入会生成 Skills 恢复报告，通常位于 `./codex_bundles/_skills_restore_report.<timestamp>.<id>.json`。

## Provider 和 Desktop 标题

工具会尽量保留用户在 Desktop 中看到的真实标题和 provider 语义。

- 导出时优先读取源机器 Desktop `state_*.sqlite` 中的 `threads.title`
- `THREAD_NAME` 用来保存左侧线程短标题
- `FIRST_USER_MESSAGE` 用来保存第一条用户消息，作为兜底预览
- 导入时优先使用 `THREAD_NAME`
- 旧 Bundle 没有标题时，才从现有 Desktop 标题、`session_index.jsonl` 或 rollout 首条用户消息恢复
- 账号登录模式下，如果 `~/.codex/config.toml` 没有 `model_provider`，会从 Desktop `threads` 表和最新 rollout 中推断
- `repair-desktop` 会保留已有 Desktop 短标题，只修复 provider、索引、workspace roots 和 `threads` 登记

Provider 识别顺序：

1. 命令显式参数
2. `~/.codex/config.toml`
3. 最新 Desktop `threads` 表
4. 最新 rollout 会话文件

## 安全性说明

- 不修改对话正文内容
- 不会悄悄覆盖原始 session
- 导入覆盖前会自动备份旧 rollout
- 清理操作只针对旧版无标记 clone
- 删除 Skill 和删除备份都需要确认
- 导入前会校验 manifest、路径和 JSONL
- GitHub 同步只处理 `./codex_bundles/`
- 父项目源码仓库通过 `.gitignore` 忽略 `codex_bundles/`
- 建议写入型动作第一次都先 Dry-run

## 运行环境

- Python >= 3.8
- 无第三方运行时依赖
- 支持 macOS / Windows / Linux
- GitHub 同步需要本机可用的 `git`

## 环境变量

| 变量 | 作用 |
|---|---|
| `NO_COLOR=1` | 禁用颜色 |
| `CST_ASCII_UI=1` | 使用 ASCII UI |
| `CST_TUI_MAX_WIDTH=120` | 限制 TUI 最大宽度 |
| `CST_MACHINE_LABEL=My-MacBook` | 指定导出时的机器标签 |
| `CST_LAUNCH_MODE=auto|source|installed` | 控制 launcher 使用源码模式或安装模式 |

## 开发验证

```bash
python3 -m ruff check src tests
python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
```

## 社区支持

<div align="center">

**学 AI，上 L 站**

[![LINUX DO](https://img.shields.io/badge/LINUX%20DO-社区-gray?style=flat-square)](https://linux.do/) [![社区支持](https://img.shields.io/badge/社区支持-交流-blue?style=flat-square)](https://linux.do/)

本项目在 [LINUX DO](https://linux.do/) 社区发布与交流，感谢佬友们的支持与反馈。

</div>

## 许可证

MIT License
