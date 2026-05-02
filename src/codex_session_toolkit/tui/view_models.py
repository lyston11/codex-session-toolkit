"""TUI-specific view models."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import List, Tuple

from .. import APP_COMMAND
from ..models import BundleSummary


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


_LEGACY_MENU_EXPORTS = {
    "SECTION_NOTES",
    "TUI_ACTION_NOTES",
    "build_tui_menu_actions",
    "build_tui_menu_sections",
    "tui_action_section",
}

__all__ = [
    "BatchBundleImportSelection",
    "BundleBrowserSnapshot",
    "BundleCategoryFolderOption",
    "BundleMachineFolderOption",
    "BundleProjectFolderOption",
    "ToolkitAppContext",
    "TuiMenuAction",
    "TuiMenuSection",
    *_LEGACY_MENU_EXPORTS,
]


def __getattr__(name: str):
    if name in _LEGACY_MENU_EXPORTS:
        value = getattr(import_module(".menu_catalog", package=__package__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
