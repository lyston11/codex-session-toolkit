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
    "repair": "Repair / Maintenance",
}
SECTION_BORDER_CODES = {
    "session": (Ansi.DIM, Ansi.CYAN),
    "bundle": (Ansi.DIM, Ansi.MAGENTA),
    "skills": (Ansi.DIM, Ansi.BRIGHT_BLUE),
    "repair": (Ansi.DIM, Ansi.GREEN),
}

TUI_ACTION_SECTION_OVERRIDES = {
    "export_one": "session",
    "project_sessions": "session",
    "provider_migration": "repair",
    "desktop_repair": "repair",
    "exit": "system",
}

TUI_ACTION_NOTES = {
    "provider_migration": [
        "为当前会话创建一份适配当前 Provider 的副本。",
    ],
    "clean_legacy": [
        "清理旧版本遗留的无标记副本文件。",
    ],
    "list_sessions": ["内置会话浏览器，支持搜索、预览和详情查看。"],
    "project_sessions": [
        "粘贴项目路径后，只查看这个项目下的全部会话。",
        "可直接批量导出到 ./codex_bundles/<machine>/sessions/project/<project_name>/<timestamp>/。",
    ],
    "browse_bundles": ["独立浏览 Bundle 导出记录，而不是只在导入时顺手选择。", "默认显示全部历史，支持按导出方式、机器和最新视图切换。"],
    "validate_bundles": ["扫描 Bundle 导出目录里的 manifest、session JSONL 和 history JSONL。", "适合在批量导入前先找出坏包。"],
    "export_one": ["从会话列表中选择要导出的 session。", "默认归档到 ./codex_bundles/<machine>/sessions/single/<timestamp>/。"],
    "export_desktop_all": ["默认归档到 ./codex_bundles/<machine>/sessions/desktop/<timestamp>/。", "范围包含 active + archived 的 Desktop 会话，并分别生成 Bundle。"],
    "export_desktop_active": ["默认归档到 ./codex_bundles/<machine>/sessions/active/<timestamp>/。", "仅导出 ~/.codex/sessions/ 下的 Desktop 会话，不会扫描 ~/.codex/archived_sessions/。"],
    "export_cli_all": ["默认归档到 ./codex_bundles/<machine>/sessions/cli/<timestamp>/。", "范围包含 active + archived 的 CLI 会话，并分别生成 Bundle。"],
    "import_one": ["从 Bundle 列表中选择要导入为会话的条目。", "可先按导出机器和导出方式筛选。", "导入时会顺手修复 history / index / Desktop 元数据。"],
    "import_desktop_all": [
        "先选择设备文件夹，再选择该设备下的分类文件夹，然后批量导入。",
        "分类文件夹会显示为 desktop / active / cli / project / single。",
        "如果选择 project，还会继续选择项目文件夹，并显示本机是否已有同名/同路径项目。",
    ],
    "list_skills": ["浏览本机已安装的 Skills，默认只显示自定义 Skills。"],
    "export_skill_one": ["从本机 Skills 列表中选择一个自定义 Skill 单独导出。"],
    "export_skills_all": ["将本机自定义 Skills 独立导出，适合跨设备同步 Skill 库。"],
    "browse_skill_bundles": ["浏览 standalone Skills Bundle，和会话 Bundle 分开管理。"],
    "import_skill_bundle": ["选择一个 Skills Bundle 导入；同内容复用，冲突默认跳过。"],
    "import_skill_bundles": ["批量导入 standalone Skills Bundle，可按来源机器过滤。"],
    "delete_skill": ["删除本机自定义 Skill。只允许删除 .agents/.codex 下的 custom Skill。"],
    "desktop_repair": [
        "修复会话在 Desktop 中的显示、索引和登记信息。",
    ],
    "browse_backups": [
        "浏览、恢复或删除导入覆盖前自动保留的会话备份。",
        "恢复/删除都会在 TUI 中二次确认；恢复前会再备份当前文件。",
    ],
    "exit": ["退出工具箱。"],
}

SECTION_NOTES = {
    "session": [
        "聚焦本机会话浏览与单会话操作。",
        "适合先定位会话，再做单会话导出或查看详情。",
    ],
    "bundle": [
        "聚焦 Bundle 导出记录与跨设备迁移。",
        "包含浏览、校验、批量导出与批量导入。",
    ],
    "skills": [
        "聚焦 Skills 的独立同步。",
        "会话导入导出只携带实际依赖的 Skills，全量同步放在这里处理。",
    ],
    "repair": [
        "按目标处理 Provider 迁移、Desktop 显示修复与旧副本清理。",
        "动作内部只保留必要选项，避免把底层实现细节直接摊给使用者。",
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
        _menu_action("list_sessions", "l", "浏览最近会话", ("list", "--limit", "20")),
        _menu_action("export_one", "e", "导出单个会话为 Bundle", ("export", "<session_id>")),
        _menu_action("project_sessions", "p", "按项目路径查看并导出会话"),
        _menu_action("browse_bundles", "o", "浏览 Bundle", ("list-bundles", "--limit", "20")),
        _menu_action("validate_bundles", "y", "校验 Bundle", ("validate-bundles", "--source", "all")),
        _menu_action("export_desktop_all", "b", "批量导出全部 Desktop 会话为 Bundle", ("export-desktop-all",)),
        _menu_action("export_desktop_active", "a", "批量导出全部 Active Desktop 会话为 Bundle", ("export-active-desktop-all",)),
        _menu_action("export_cli_all", "c", "批量导出全部 CLI 会话为 Bundle", ("export-cli-all",)),
        _menu_action("import_one", "i", "导入单个 Bundle 为会话", ("import", "<session_id|bundle_dir>")),
        _menu_action("import_desktop_all", "m", "批量导入 Bundle 为会话", ("import-desktop-all",)),
        _menu_action("list_skills", "s", "浏览本机 Skills", ("list-skills",)),
        _menu_action("export_skill_one", "e", "导出单个 Skill", ("export-skills", "<skill_name>")),
        _menu_action("export_skills_all", "x", "导出全部自定义 Skills", ("export-skills",)),
        _menu_action("browse_skill_bundles", "o", "浏览 Skills Bundle", ("list-skill-bundles",)),
        _menu_action("import_skill_bundle", "i", "导入单个 Skills Bundle", ("import-skill-bundle", "<bundle_dir|skill_name>")),
        _menu_action("import_skill_bundles", "m", "批量导入 Skills Bundle", ("import-skill-bundles",)),
        _menu_action("delete_skill", "d", "删除本机 Skill", ("delete-skill", "<skill_name>"), is_dangerous=True),
        _menu_action("provider_migration", "1", "迁移到当前 Provider"),
        _menu_action("desktop_repair", "2", "修复会话在 Desktop 中显示"),
        _menu_action("browse_backups", "3", "管理会话备份", ("list-backups",)),
        _menu_action("clean_legacy", "4", "清理旧版无标记副本", ("clean-clones",), is_dangerous=True),
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
