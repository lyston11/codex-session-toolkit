"""TUI-specific view models and static menu catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .. import APP_COMMAND
from ..models import BundleSummary
from .terminal import Ansi


@dataclass(frozen=True)
class ToolkitAppContext:
    target_provider: str
    active_sessions_dir: str
    config_path: str
    bundle_root_label: str = "./codex_bundles"
    desktop_bundle_root_label: str = "./codex_bundles"
    entry_command: str = APP_COMMAND


@dataclass(frozen=True)
class TuiMenuAction:
    action_id: str
    hotkey: str
    label: str
    section_id: str
    cli_args: Tuple[str, ...]
    is_dangerous: bool = False
    is_dry_run: bool = False


@dataclass(frozen=True)
class TuiMenuSection:
    title: str
    section_id: str
    border_codes: Tuple[str, ...]


@dataclass(frozen=True)
class BundleBrowserSnapshot:
    entries: List[BundleSummary]
    machine_options: List[Tuple[str, str]]
    export_group_options: List[Tuple[str, str]]
    current_machine_label: str
    current_export_group_label: str


@dataclass(frozen=True)
class BatchBundleImportSelection:
    entries: List[BundleSummary]
    machine_filter: str
    machine_label: str
    export_group_filter: str
    export_group_label: str
    latest_only: bool
    project_filter: str = ""
    project_label: str = ""
    project_source_path: str = ""
    target_project_path: str = ""


@dataclass(frozen=True)
class BundleMachineFolderOption:
    machine_key: str
    machine_label: str
    bundle_count: int
    export_groups: Tuple[str, ...]


@dataclass(frozen=True)
class BundleCategoryFolderOption:
    export_group: str
    export_group_label: str
    bundle_count: int
    entries: List[BundleSummary]


@dataclass(frozen=True)
class BundleProjectFolderOption:
    project_key: str
    project_label: str
    project_path: str
    bundle_count: int
    entries: List[BundleSummary]
    local_status: str
    local_status_label: str
    local_target_path: str


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
        "浏览导入覆盖前自动保留的会话备份。",
        "可在 TUI 中二次确认后一键恢复；恢复前会再备份当前文件。",
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


def build_tui_menu_actions() -> List[TuiMenuAction]:
    return [
        TuiMenuAction("list_sessions", "l", "浏览最近会话", "session", ("list", "--limit", "20")),
        TuiMenuAction("export_one", "e", "导出单个会话为 Bundle", "session", ("export", "<session_id>")),
        TuiMenuAction("project_sessions", "p", "按项目路径查看并导出会话", "session", tuple()),
        TuiMenuAction("browse_bundles", "o", "浏览 Bundle", "bundle", ("list-bundles", "--limit", "20")),
        TuiMenuAction("validate_bundles", "y", "校验 Bundle", "bundle", ("validate-bundles", "--source", "all")),
        TuiMenuAction("export_desktop_all", "b", "批量导出全部 Desktop 会话为 Bundle", "bundle", ("export-desktop-all",)),
        TuiMenuAction("export_desktop_active", "a", "批量导出全部 Active Desktop 会话为 Bundle", "bundle", ("export-active-desktop-all",)),
        TuiMenuAction("export_cli_all", "c", "批量导出全部 CLI 会话为 Bundle", "bundle", ("export-cli-all",)),
        TuiMenuAction("import_one", "i", "导入单个 Bundle 为会话", "bundle", ("import", "<session_id|bundle_dir>")),
        TuiMenuAction("import_desktop_all", "m", "批量导入 Bundle 为会话", "bundle", ("import-desktop-all",)),
        TuiMenuAction("list_skills", "s", "浏览本机 Skills", "skills", ("list-skills",)),
        TuiMenuAction("export_skill_one", "e", "导出单个 Skill", "skills", ("export-skills", "<skill_name>")),
        TuiMenuAction("export_skills_all", "x", "导出全部自定义 Skills", "skills", ("export-skills",)),
        TuiMenuAction("browse_skill_bundles", "o", "浏览 Skills Bundle", "skills", ("list-skill-bundles",)),
        TuiMenuAction("import_skill_bundle", "i", "导入单个 Skills Bundle", "skills", ("import-skill-bundle", "<bundle_dir|skill_name>")),
        TuiMenuAction("import_skill_bundles", "m", "批量导入 Skills Bundle", "skills", ("import-skill-bundles",)),
        TuiMenuAction("delete_skill", "d", "删除本机 Skill", "skills", ("delete-skill", "<skill_name>"), is_dangerous=True),
        TuiMenuAction("provider_migration", "1", "迁移到当前 Provider", "repair", tuple()),
        TuiMenuAction("desktop_repair", "2", "修复会话在 Desktop 中显示", "repair", tuple()),
        TuiMenuAction("browse_backups", "3", "浏览/恢复会话备份", "repair", ("list-backups",)),
        TuiMenuAction("clean_legacy", "4", "清理旧版无标记副本", "repair", ("clean-clones",), is_dangerous=True),
        TuiMenuAction("exit", "0", "退出", "system", tuple()),
    ]


def build_tui_menu_sections() -> List[TuiMenuSection]:
    return [
        TuiMenuSection("Session / Browse", "session", (Ansi.DIM, Ansi.CYAN)),
        TuiMenuSection("Bundle / Transfer", "bundle", (Ansi.DIM, Ansi.MAGENTA)),
        TuiMenuSection("Skills / Transfer", "skills", (Ansi.DIM, Ansi.BRIGHT_BLUE)),
        TuiMenuSection("Repair / Maintenance", "repair", (Ansi.DIM, Ansi.GREEN)),
    ]
