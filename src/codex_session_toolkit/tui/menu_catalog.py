"""Interactive TUI menu catalog.

This module adapts the canonical CLI command catalog into TUI sections and
actions. View models stay as passive data structures; menu ownership lives here.
"""

from __future__ import annotations

from typing import List, Sequence

from ..command_catalog import command_domain, command_domains
from .terminal import Ansi
from .view_models import TuiMenuAction, TuiMenuSection


SECTION_TITLES = {
    "session": "Session / Browse",
    "bundle": "Bundle / Transfer",
    "skills": "Skills / Transfer",
    "github": "GitHub / Sync",
    "repair": "Repair / Maintenance",
}
SECTION_BORDER_CODES = {
    "session": (Ansi.DIM, Ansi.CYAN),
    "bundle": (Ansi.DIM, Ansi.MAGENTA),
    "skills": (Ansi.DIM, Ansi.BRIGHT_BLUE),
    "github": (Ansi.DIM, Ansi.YELLOW),
    "repair": (Ansi.DIM, Ansi.GREEN),
}

TUI_ACTION_SECTION_OVERRIDES = {
    "project_sessions": "session",
    "provider_migration": "repair",
    "desktop_repair": "repair",
    "delete_migrated_originals": "repair",
    "github_status": "github",
    "github_proxy": "github",
    "exit": "system",
}

TUI_ACTION_NOTES = {
    "provider_migration": [
        "为旧 Provider 会话创建一份适配当前 Provider 的新副本。",
        "原始会话会保留，后续可用“删除已复制的旧 Provider 会话”清理。",
    ],
    "clean_legacy": [
        "清理旧版本遗留的重复副本文件。",
        "这个功能只处理旧版无标记副本，不删除带 cloned_from 关系的原始会话。",
    ],
    "delete_migrated_originals": [
        "删除已经复制到当前 Provider 的旧 Provider 原始会话。",
        "只会列出能通过 cloned_from 找到新 Provider 副本的旧会话。",
        "进入列表后可预览、勾选、全选，再二次确认删除。",
    ],
    "delete_archived_sessions": [
        "进入归档会话列表，先预览再删除。",
        "支持空格逐条勾选，也支持按 a 选中全部匹配项后删除。",
        "会同步移除 Desktop 线程栏和本工具索引里的归档记录。",
    ],
    "list_sessions": [
        "内置会话浏览器，支持搜索、预览、详情查看和导出。",
        "支持空格逐条勾选，也支持按 a 选中全部匹配项后导出。",
    ],
    "project_sessions": [
        "粘贴项目路径后，只查看这个项目下的全部会话。",
        "支持当前会话、勾选多条导出，也支持按 a 选中该项目全部匹配项后导出。",
    ],
    "browse_bundles": ["独立浏览和管理 Bundle 记录，不在这里执行导入。", "支持按 Bundle 类别、来源机器和历史范围筛选，也支持勾选后删除本地 Bundle。"],
    "validate_bundles": ["扫描 Bundle 导出目录里的 manifest、session JSONL 和 history JSONL。", "适合在批量导入前先找出坏包。"],
    "export_desktop_all": ["默认归档到 ./codex_bundles/<machine>/sessions/desktop/<timestamp>/。", "范围包含 active + archived 的 Desktop 会话，并分别生成 Bundle。"],
    "export_desktop_active": ["默认归档到 ./codex_bundles/<machine>/sessions/active/<timestamp>/。", "仅导出 ~/.codex/sessions/ 下的 Desktop 会话，不会扫描 ~/.codex/archived_sessions/。"],
    "export_cli_all": ["默认归档到 ./codex_bundles/<machine>/sessions/cli/<timestamp>/。", "范围包含 active + archived 的 CLI 会话，并分别生成 Bundle。"],
    "import_bundles": [
        "进入 Bundle 列表后可搜索、筛选、勾选再导入。",
        "支持当前 Bundle、勾选多条导入，也支持按 a 选中全部匹配 Bundle 后导入；删除请回到浏览 Bundle。",
        "导入会同步修复 history / index / Desktop 线程表和侧栏状态。",
    ],
    "list_skills": [
        "浏览本机已安装的 Skills，默认只显示自定义 Skills。",
        "支持当前 Skill、勾选多条导出，也支持按 a 选中全部匹配自定义 Skills 后导出。",
    ],
    "export_skill_one": ["从本机 Skills 列表中选择一个自定义 Skill 单独导出。"],
    "export_skills_all": ["将本机自定义 Skills 独立导出，适合跨设备同步 Skill 库。"],
    "browse_skill_bundles": [
        "浏览 standalone Skills Bundle，和会话 Bundle 分开管理。",
        "支持当前 Bundle、勾选多条导入，也支持按 a 选中全部匹配 Skills Bundle 后导入。",
    ],
    "import_skill_bundle": ["选择一个 Skills Bundle 导入；同内容复用，冲突默认跳过。"],
    "import_skill_bundles": ["批量导入 standalone Skills Bundle，可按来源机器过滤。"],
    "delete_skill": [
        "删除本机自定义 Skills。只允许删除 .agents/.codex 下的 custom Skill。",
        "支持空格逐条勾选，也支持按 a 选中全部匹配自定义 Skills 后删除。",
    ],
    "github_status": [
        "先快速读取本地连接状态，再检查 GitHub 远端更新时间。",
        "检测期间会显示进度，不让 TUI 空白卡住。",
    ],
    "connect_github": [
        "先在 GitHub 上创建一个独立仓库，然后把 ./codex_bundles 连接到这个仓库。",
        "TUI 会询问是否连接后立即首次推送本机 Bundle。",
        "工具会拒绝连接到当前项目源码仓库的 remote。",
    ],
    "github_proxy": [
        "为 GitHub 同步配置本机代理接口。",
        "配置后状态检查、拉取、推送都会走代理；也可以随时断开。",
    ],
    "sync_github": [
        "把 ./codex_bundles 中的会话 Bundle 和 Skills Bundle 推送到已连接的独立仓库。",
        "同步前会检查远端更新；可合并则合并，冲突会停止并报告。",
    ],
    "pull_github": [
        "从已连接的独立 GitHub 仓库拉取会话 Bundle 和 Skills Bundle 更新。",
        "如果本地未提交变更会被远端更新覆盖，工具会停止并提示先处理本地变更。",
    ],
    "desktop_repair": [
        "把现有会话修正到当前 Provider，并修复 Desktop 显示、索引和登记信息。",
        "会处理侧栏筛选/折叠、线程池排序、pin 状态、空 thread_source 和失效 threads 行。",
    ],
    "browse_backups": [
        "浏览、恢复或删除导入覆盖前自动保留的会话备份。",
        "恢复/删除都会在 TUI 中二次确认；恢复前会再备份当前文件。",
    ],
    "exit": ["退出工具箱。"],
}

SECTION_NOTES = {
    "session": [
        "聚焦本机会话浏览、筛选和导出。",
        "适合先定位会话，再按当前、勾选或全部范围导出。",
    ],
    "bundle": [
        "聚焦 Bundle 导出记录与跨设备迁移。",
        "包含浏览、校验、导出全部与可选择导入。",
    ],
    "skills": [
        "聚焦 Skills 的独立同步。",
        "会话导入导出只携带实际依赖的 Skills，全量同步放在这里处理。",
    ],
    "repair": [
        "按目标处理 Provider 复制、Provider 迁移、Desktop 显示修复与旧副本清理。",
        "动作内部只保留必要选项，避免把底层实现细节直接摊给使用者。",
    ],
    "github": [
        "聚焦把本机导出的 Bundle 工作区同步到一个独立 GitHub 仓库。",
        "用于跨设备共享 codex_bundles，而不直接触碰 ~/.codex 会话数据。",
    ],
}


def tui_action_section(action_id: str, cli_args: Sequence[str]) -> str:
    if action_id in TUI_ACTION_SECTION_OVERRIDES:
        return TUI_ACTION_SECTION_OVERRIDES[action_id]
    if not cli_args:
        raise ValueError(f"TUI action {action_id!r} must declare a section override or CLI command")
    return command_domain(cli_args[0])


def _menu_action(
    action_id: str,
    hotkey: str,
    label: str,
    cli_args: tuple[str, ...] = (),
    *,
    is_dangerous: bool = False,
    is_dry_run: bool = False,
) -> TuiMenuAction:
    return TuiMenuAction(
        action_id,
        hotkey,
        label,
        tui_action_section(action_id, cli_args),
        cli_args,
        is_dangerous=is_dangerous,
        is_dry_run=is_dry_run,
    )


def build_tui_menu_actions() -> List[TuiMenuAction]:
    return [
        _menu_action("list_sessions", "l", "浏览并导出会话", ("list", "--limit", "20")),
        _menu_action("project_sessions", "p", "按项目路径查看并导出会话"),
        _menu_action("browse_bundles", "o", "浏览 Bundle", ("list-bundles", "--limit", "20")),
        _menu_action("validate_bundles", "y", "校验 Bundle", ("validate-bundles", "--source", "all")),
        _menu_action("export_desktop_all", "b", "导出全部 Desktop 会话为 Bundle", ("export-desktop-all",)),
        _menu_action("export_desktop_active", "v", "导出全部 Active Desktop 会话为 Bundle", ("export-active-desktop-all",)),
        _menu_action("export_cli_all", "c", "导出全部 CLI 会话为 Bundle", ("export-cli-all",)),
        _menu_action("import_bundles", "i", "导入 Bundle 为会话", ("import", "<bundle_dir...>")),
        _menu_action("list_skills", "s", "浏览并导出本机 Skills", ("list-skills",)),
        _menu_action("browse_skill_bundles", "i", "浏览并导入 Skills Bundle", ("list-skill-bundles",)),
        _menu_action("delete_skill", "d", "删除本机 Skills", ("delete-skill", "<skill_name>"), is_dangerous=True),
        _menu_action("connect_github", "c", "连接独立 GitHub 仓库", ("connect-github", "<repo_url>")),
        _menu_action("github_proxy", "x", "连接/断开代理", ("github-proxy", "<proxy_url>")),
        _menu_action("github_status", "s", "查看 GitHub 同步状态"),
        _menu_action("pull_github", "p", "从 GitHub 拉取更新", ("pull-github",)),
        _menu_action("sync_github", "g", "推送本机更新到 GitHub", ("sync-github",)),
        _menu_action("provider_migration", "1", "复制会话到当前 Provider"),
        _menu_action("desktop_repair", "2", "迁移会话到当前 Provider"),
        _menu_action("browse_backups", "3", "管理会话备份", ("list-backups",)),
        _menu_action("delete_archived_sessions", "4", "删除归档会话", ("delete-archived-sessions",), is_dangerous=True),
        _menu_action("delete_migrated_originals", "5", "删除已复制的旧 Provider 会话", ("delete-migrated-originals",), is_dangerous=True),
        _menu_action("clean_legacy", "6", "清理旧版重复副本", ("clean-clones",), is_dangerous=True),
        _menu_action("exit", "0", "退出"),
    ]


def build_tui_menu_sections() -> List[TuiMenuSection]:
    return [
        TuiMenuSection(
            SECTION_TITLES[domain],
            domain,
            SECTION_BORDER_CODES[domain],
        )
        for domain in command_domains()
    ]
