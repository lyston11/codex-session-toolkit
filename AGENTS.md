# AGENTS.md

本项目的重构目标是工程化管理、工程化归类、工程化解耦，而不是按文件大小机械拆分。任何后续 Agent 进入本仓库时，都应优先保持行为兼容、边界清晰和测试可验证。

## 工作原则

- 不为了拆而拆。只有当新边界能明确表达所有权、依赖方向、复用语义或测试边界时，才新增模块。
- 先读现有结构，再改代码。优先沿用仓库已有命名、数据模型、服务入口和测试风格。
- 保留兼容层。已有 public API 和 legacy facade 不能随意删除；需要迁移时用显式转发和测试守住。
- 不反向依赖。低层模块不能为了方便 import 高层入口、TUI、presenter 或兼容 facade。
- 改行为必须有测试；改边界必须有架构测试或 smoke 测试守护。

## 工程分层

- `cli.py`、`commands.py`、`command_parser.py`、`__main__.py` 是入口层。入口层只负责启动、参数接入、兼容 CLI 行为。
- `command_catalog.py` 是命令目录的单一来源，维护 command name、domain、help、summary。CLI parser、TUI menu 和测试都应引用这里，不要重复定义命令集合。
- `application/command_handlers.py` 是 CLI 命令到 service/presenter 的编排层。不要把 argparse 细节或 TUI 逻辑放进这里。
- `services/` 是用例层，负责导入、导出、修复、迁移、Skills 同步等业务流程。services 可以调用 stores，但不能依赖 CLI/TUI/compat facade。
- `stores/` 是存储、扫描、解析、序列化层。stores 不能依赖 services、presenters、TUI 或入口层。
- `presenters/` 只负责输出格式和报告展示，不能反向调用 services 或 stores。
- `tui/` 是交互界面层。TUI flow 可以调用 services，但不要让 flow 模块 runtime import `tui.app`。
- `core.py`、`tui_app.py`、`terminal_ui.py`、`stores/bundles.py` 是兼容 facade，只做 legacy forwarding；项目内部新代码应 import canonical module。

## 关键所有权

- CLI 命令名、领域分组和帮助文案归 `command_catalog.py`。
- argparse 构造归 `command_parser.py`；命令执行归 `application/command_handlers.py`。
- TUI 菜单目录归 `tui/menu_catalog.py`；`tui/view_models.py` 只放被动数据结构。
- Skills manifest 数据结构、sidecar 读写和恢复报告归 `stores/skills_manifest.py`。
- Skills 发现、打包、恢复行为归 `stores/skills.py`；保留必要 re-export 兼容旧调用。
- Session bundle 中的 Skills sidecar 恢复编排归 `services/skill_sidecars.py`。
- 批量导出选择和元数据计划归 `services/export_planning.py`；实际导出执行归 `services/exporting.py`。
- 批量导入筛选、latest-only、project remap 和 report path 计划归 `services/import_planning.py`；实际导入执行归 `services/importing.py`。
- `manifest.env` 写入统一使用 `validation.write_manifest()`，不要在 service 中手写 shell quoting。
- 批量导出 manifest 文本写入统一使用 `stores/bundle_repository.write_batch_export_manifest()`。

## 架构约束

- 新增源码模块后，必须更新 `tests/test_architecture_contracts.py` 的分层分类。
- 保持运行时内部 import graph 无环。
- store layer 不得依赖 workflow、UI、entrypoint、presentation。
- service layer 不得依赖 entrypoint、TUI、presentation、compat facade。
- application layer 不得依赖 entrypoint、TUI、compat facade。
- presentation layer 不得依赖 workflow、storage、TUI。
- TUI flow 模块不得 runtime import `codex_session_toolkit.tui.app`；类型提示需要放在 `TYPE_CHECKING` 下。
- 项目内部不要 import `codex_session_toolkit.core`、`codex_session_toolkit.tui_app`、`codex_session_toolkit.terminal_ui` 或 `codex_session_toolkit.stores.bundles`。

## 测试要求

完成改动后至少运行：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
python3 -m ruff check src tests
```

若只改某个窄范围，可以先跑针对性测试，但最终合并前必须跑完整三件套。

## 工作区注意事项

- 不要删除或覆盖用户未跟踪文件，例如 `Todo.md` 和 `codex_bundles/`。
- 不要使用 `git reset --hard`、`git checkout --` 等会丢弃用户改动的命令，除非用户明确要求。
- 处理脏工作区时，只编辑与当前任务相关的文件；遇到不属于自己的改动，保留并绕开。
