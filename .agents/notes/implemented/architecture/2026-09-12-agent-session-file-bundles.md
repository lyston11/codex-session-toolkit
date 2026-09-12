# 非 Codex 会话 Bundle 采用文件级布局（AGENT manifest 键）

日期：2026-09-12 ｜ 状态：implemented ｜ 分类：architecture

## 背景

会话浏览器已支持 Codex / Claude Code / Pi / ZCode 四个 Agent 的 `g` 切换浏览（`stores/agent_sessions.py`），但 Bundle 导出/导入深度绑定 Codex：manifest 校验、`codex/` 前缀布局、history/index/desktop 修复、lineage、thread_history sidecar 都以 Codex rollout 格式为前提。Claude/Pi/ZCode 的会话是纯 JSONL 文件，没有这些状态库。

## 决定

非 Codex 会话 Bundle 采用**文件级布局**，与 Codex Bundle 共用目录约定但内容最简：

- 位置沿用 `<machine>/sessions/single/<批次>/<session_id>/`（`build_single_export_root`），从而自动纳入 GitHub 同步范围与 Bundle 浏览器扫描。
- 会话文件按**相对 home 的原始路径**存放（如 `.claude/projects/-x/<uuid>.jsonl`），不再套 `codex/` 前缀——路径本身已含 Agent 目录名，避免一层无信息量的包装。
- manifest 增加 `AGENT` 键（`validation.load_manifest` 允许集扩充；缺省视为 codex，旧 Bundle 完全兼容）。`RELATIVE_PATH` 即该 home 相对路径。
- 导入在 `import_session` 入口按 `AGENT` 分支：非 codex 直接走 `services/agent_session_transfer.import_agent_session_bundle`（复制回 home，sha256 相同=already_present，不同=conflict_skipped/overwrite/strict），**不做**任何 Codex 状态库修复。
- `validate_session_id` 字符集放宽为 `[A-Za-z0-9_-]+`：ZCode id 带 `sess_` 下划线前缀；UUID 不受影响。

## 否决的替代方案

- **复用 Codex 导入管线**：需伪造 history.jsonl/状态库迁移，对无状态库的 Agent 是纯开销，且 `validate_relative_path` 硬性要求 `sessions/` 前缀。
- **独立顶层目录（codex_bundles 外）**：脱离 GitHub 同步工作区，违背"同步范围是整个 codex_bundles"的约定。

## 后果与边界

- Bundle 校验（`validate_bundle_directory`）、Bundle 摘要（`BundleSummary.agent`）、TUI 详情均按 agent 分支展示。
- 按 Agent 分组导出：TUI 混选多个 Agent 时要求先切到单一 Agent 再导出，避免一次动作拼多条 CLI。
- 局限：非 Codex 会话无时间戳元数据时以文件 mtime 兜底；ZCode `model-io-*.jsonl` 是模型 IO 转储，预览仅到时间级别。

## 代码入口

- `services/agent_session_transfer.py`（导出/导入本体）
- `services/importing.py` 的 `import_session` AGENT 分支
- `stores/bundle_validation.py` 的 `_validate_agent_bundle_directory`
