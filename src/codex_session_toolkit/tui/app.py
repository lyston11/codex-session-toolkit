"""
TUI application layer for the Codex Session Toolkit.

This module owns interactive menu composition, browser flows, and
action orchestration so the legacy entrypoint can stay focused on
argument compatibility and command dispatch.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..commands import run_cli as run_toolkit_cli
from ..errors import ToolkitError
from ..models import BundleSummary, GitHubSyncStatus, LocalSkillSummary, SessionBackupSummary, SessionSummary, SkillBundleSummary
from ..paths import CodexPaths
from .action_flows import execute_menu_action as _execute_menu_action_flow
from .action_flows import resolve_menu_action_request as _resolve_menu_action_request_flow
from .action_flows import run_action as _run_action_flow
from .bundle_flows import bundle_browser_snapshot as _bundle_browser_snapshot_flow
from .bundle_flows import bundle_category_folder_options as _bundle_category_folder_options_flow
from .bundle_flows import bundle_detail_lines as _bundle_detail_lines_flow
from .bundle_flows import bundle_machine_folder_options as _bundle_machine_folder_options_flow
from .bundle_flows import bundle_project_folder_options as _bundle_project_folder_options_flow
from .bundle_flows import default_target_project_path as _default_target_project_path_flow
from .bundle_flows import select_batch_bundle_import_scope as _select_batch_bundle_import_scope_flow
from .bundle_flows import select_project_bundle_import_scope as _select_project_bundle_import_scope_flow
from .browser_flows import open_bundle_browser as _open_bundle_browser_flow
from .browser_flows import open_local_skill_browser as _open_local_skill_browser_flow
from .browser_flows import open_project_session_browser as _open_project_session_browser_flow
from .browser_flows import open_session_backup_browser as _open_session_backup_browser_flow
from .browser_flows import open_session_browser as _open_session_browser_flow
from .browser_flows import open_skill_bundle_browser as _open_skill_bundle_browser_flow
from .github_flows import github_sync_status as _github_sync_status_flow
from .github_flows import github_sync_status_lines as _github_sync_status_lines_flow
from .github_flows import show_github_sync_status as _show_github_sync_status_flow
from .navigation_state import apply_home_key, apply_section_key, clamp_selected_index
from .prompt_flows import confirm_dangerous_action as _confirm_dangerous_action_flow
from .prompt_flows import confirm_toggle as _confirm_toggle_flow
from .prompt_flows import prompt_choice as _prompt_choice_flow
from .prompt_flows import prompt_desktop_repair_scope as _prompt_desktop_repair_scope_flow
from .prompt_flows import prompt_execution_mode as _prompt_execution_mode_flow
from .prompt_flows import prompt_value as _prompt_value_flow
from .prompt_flows import render_prompt_choice as _render_prompt_choice_flow
from .sync_prompts import github_sync_hint_lines as _github_sync_hint_lines_flow
from .terminal import (
    Ansi,
    align_line,
    app_logo_lines,
    clear_screen,
    ellipsize_middle,
    style_text,
    term_height,
    term_width,
    tui_width,
)
from .terminal_io import read_key
from ..support import normalize_project_path
from .ui_panels import bundle_action_center as _bundle_action_center_flow
from .ui_panels import render_home as _render_home_flow
from .ui_panels import render_section_page as _render_section_page_flow
from .ui_panels import session_action_center as _session_action_center_flow
from .ui_panels import show_detail_panel as _show_detail_panel_flow
from .ui_panels import tui_help_text as _tui_help_text_flow
from .menu_catalog import SECTION_NOTES, TUI_ACTION_NOTES, build_tui_menu_actions, build_tui_menu_sections
from .view_models import (
    BatchBundleImportSelection,
    BundleBrowserSnapshot,
    BundleCategoryFolderOption,
    BundleMachineFolderOption,
    BundleProjectFolderOption,
    ToolkitAppContext,
    TuiMenuAction,
    TuiMenuSection,
)

FIXED_THEME_LOGO_WIDTH = 100


def format_bundle_source_label(source_group: str) -> str:
    return {
        "all": "全部分类",
        "bundle": "bundle 分类",
        "desktop": "desktop 分类",
    }.get(source_group, source_group)


class ToolkitTuiApp:
    def __init__(self, context: ToolkitAppContext) -> None:
        self.context = context
        self.paths = CodexPaths()
        self.menu_actions = build_tui_menu_actions()
        self.menu_sections = build_tui_menu_sections()

    def _cli_preview(self, args: Sequence[str]) -> str:
        cmd = self.context.entry_command
        if args:
            cmd += " " + " ".join(args)
        return cmd

    def _screen_layout(self) -> Tuple[int, bool]:
        box_width = tui_width(term_width())
        return box_width, box_width >= 70

    def _screen_height(self) -> int:
        return max(12, term_height())

    def _fit_lines_to_screen(self, lines: List[str]) -> List[str]:
        max_rows = self._screen_height()
        if len(lines) <= max_rows:
            return lines

        visible_rows = max(6, max_rows - 1)
        trimmed = lines[:visible_rows]
        trimmed[-1] = style_text("... 窗口高度不足，内容已折叠；可放大终端窗口继续查看 ...", Ansi.DIM, Ansi.YELLOW)
        return trimmed

    def _section_tabs_line(self, selected_section_index: int, width: int) -> str:
        tabs: List[str] = []
        for pos, menu_section in enumerate(self.menu_sections):
            label = f"[{pos + 1}] {menu_section.title}"
            if pos == selected_section_index:
                tabs.append(style_text(label, Ansi.BOLD, self._section_color(menu_section)))
            else:
                tabs.append(style_text(label, Ansi.DIM))
        return ellipsize_middle("  ".join(tabs), width)

    def _actions_for_section(self, section_id: str) -> List[Tuple[int, TuiMenuAction]]:
        return [
            (idx, menu_action)
            for idx, menu_action in enumerate(self.menu_actions)
            if menu_action.section_id == section_id
        ]

    def _print_branded_header(self, title: str, subtitle: str = "") -> int:
        clear_screen()
        box_width, center = self._screen_layout()
        for line in app_logo_lines(max_width=FIXED_THEME_LOGO_WIDTH):
            print(align_line(line, box_width, center=center))
        print(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
        print(align_line(style_text(title, Ansi.DIM), box_width, center=center))
        if subtitle:
            print(align_line(style_text(subtitle, Ansi.DIM), box_width, center=center))
        print("")
        return box_width

    def _run_toolkit(self, cli_args: List[str]) -> int:
        try:
            return int(run_toolkit_cli(cli_args))
        except ToolkitError as exc:
            print(style_text(str(exc), Ansi.RED))
            return 1

    def _action_color(self, menu_action: TuiMenuAction) -> str:
        if menu_action.is_dangerous and not menu_action.is_dry_run:
            return Ansi.RED
        if menu_action.is_dry_run:
            return Ansi.YELLOW
        if menu_action.section_id == "skills":
            return Ansi.BRIGHT_BLUE
        if menu_action.section_id == "bundle":
            return Ansi.MAGENTA
        if menu_action.section_id == "github":
            return Ansi.YELLOW
        if menu_action.section_id == "repair":
            return Ansi.GREEN
        if menu_action.action_id == "exit":
            return Ansi.DIM
        return Ansi.CYAN

    def _action_notes(self, menu_action: TuiMenuAction) -> List[str]:
        return TUI_ACTION_NOTES.get(menu_action.action_id, [])

    def _section_color(self, menu_section: TuiMenuSection) -> str:
        if menu_section.section_id == "skills":
            return Ansi.BRIGHT_BLUE
        if menu_section.section_id == "bundle":
            return Ansi.MAGENTA
        if menu_section.section_id == "github":
            return Ansi.YELLOW
        if menu_section.section_id == "repair":
            return Ansi.GREEN
        return Ansi.CYAN

    def _section_notes(self, menu_section: TuiMenuSection) -> List[str]:
        return SECTION_NOTES.get(menu_section.section_id, [])

    def _github_sync_status(self, *, check_remote: bool = False) -> GitHubSyncStatus:
        return _github_sync_status_flow(self, check_remote=check_remote)

    def _github_sync_status_lines(self, status: Optional[GitHubSyncStatus] = None) -> List[str]:
        return _github_sync_status_lines_flow(self, status)

    def _show_github_sync_status(self) -> None:
        return _show_github_sync_status_flow(self)

    def _github_sync_hint_lines(self, *, force: bool = False) -> List[str]:
        return _github_sync_hint_lines_flow(self, force=force)

    def _session_detail_lines(self, summary: SessionSummary) -> List[str]:
        return [
            f"{style_text('Session ID', Ansi.DIM)} : {summary.session_id}",
            f"{style_text('类型', Ansi.DIM)}      : {summary.kind}",
            f"{style_text('范围', Ansi.DIM)}      : {summary.scope}",
            f"{style_text('会话名称', Ansi.DIM)}  : {summary.thread_name or '（无，使用预览兜底）'}",
            f"{style_text('Provider', Ansi.DIM)}  : {summary.model_provider or '-'}",
            f"{style_text('路径', Ansi.DIM)}      : {summary.path}",
            f"{style_text('工作目录', Ansi.DIM)}  : {summary.cwd or '（空）'}",
            f"{style_text('兜底预览', Ansi.DIM)}  : {summary.preview or '（无）'}",
        ]

    def _session_backup_detail_lines(self, backup: SessionBackupSummary) -> List[str]:
        target_state = "存在" if backup.target_exists else "缺失"
        kind_label = "恢复前安全备份" if backup.backup_kind == "restore-safety" else "覆盖前本地备份"
        return [
            f"{style_text('Session ID', Ansi.DIM)} : {backup.session_id}",
            f"{style_text('范围', Ansi.DIM)}      : {backup.scope}",
            f"{style_text('备份类型', Ansi.DIM)}  : {kind_label}",
            f"{style_text('备份时间', Ansi.DIM)}  : {backup.backup_time_label}",
            f"{style_text('当前文件', Ansi.DIM)}  : {target_state}",
            f"{style_text('Provider', Ansi.DIM)}  : {backup.model_provider or '-'}",
            f"{style_text('类型', Ansi.DIM)}      : {backup.kind}",
            f"{style_text('备份路径', Ansi.DIM)}  : {backup.backup_path}",
            f"{style_text('恢复目标', Ansi.DIM)}  : {backup.target_path}",
            f"{style_text('工作目录', Ansi.DIM)}  : {backup.cwd or '（空）'}",
            f"{style_text('预览', Ansi.DIM)}      : {backup.preview or '（无）'}",
        ]

    def _prompt_project_path(self, *, default: str = "") -> Optional[str]:
        answer = self._prompt_value(
            title="按项目路径查看并导出会话",
            prompt_label="输入项目路径",
            help_lines=[
                "可直接粘贴项目根目录路径。",
                "会匹配 cwd 等于该路径，或位于该路径之下的全部会话。",
                "如果输入的是文件路径，已存在时会自动回退到其所在目录。",
            ],
            default=default or str(Path.cwd()),
            allow_empty=False,
        )
        normalized = normalize_project_path(answer or "")
        return normalized or None

    def _open_project_session_browser(self) -> None:
        return _open_project_session_browser_flow(self)

    def _bundle_detail_lines(self, bundle: BundleSummary) -> List[str]:
        return _bundle_detail_lines_flow(self, bundle)

    def _local_skill_detail_lines(self, skill: LocalSkillSummary) -> List[str]:
        return [
            f"{style_text('Skill', Ansi.DIM)}      : {skill.name}",
            f"{style_text('来源根', Ansi.DIM)}     : {skill.source_root}",
            f"{style_text('类型', Ansi.DIM)}       : {skill.location_kind}",
            f"{style_text('相对目录', Ansi.DIM)}   : {skill.relative_dir}",
            f"{style_text('内容 Hash', Ansi.DIM)}  : {skill.content_hash or '-'}",
            f"{style_text('路径', Ansi.DIM)}       : {skill.skill_dir}",
        ]

    def _skill_bundle_detail_lines(self, bundle: SkillBundleSummary) -> List[str]:
        lines = [
            f"{style_text('导出时间', Ansi.DIM)} : {bundle.exported_at or '-'}",
            f"{style_text('来源机器', Ansi.DIM)} : {bundle.source_machine or bundle.source_machine_key or '-'}",
            f"{style_text('导出分组', Ansi.DIM)} : {bundle.export_group or '-'}",
            f"{style_text('Skill 数', Ansi.DIM)} : {bundle.bundled_skill_count}/{bundle.skill_count}",
            f"{style_text('路径', Ansi.DIM)}     : {bundle.bundle_dir}",
        ]
        if bundle.skills:
            lines.append(f"{style_text('包含', Ansi.DIM)}     : {', '.join(bundle.skills)}")
        return lines

    def _bundle_browser_snapshot(
        self,
        *,
        filter_text: str,
        machine_filter: str,
        export_group_filter: str,
        latest_only: bool,
        source_group: str = "all",
        limit: int = 240,
    ) -> Tuple[BundleBrowserSnapshot, str, str]:
        return _bundle_browser_snapshot_flow(
            self,
            filter_text=filter_text,
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
            latest_only=latest_only,
            source_group=source_group,
            limit=limit,
        )

    def _bundle_machine_folder_options(self) -> List[BundleMachineFolderOption]:
        return _bundle_machine_folder_options_flow(self)

    def _bundle_category_folder_options(self, machine_key: str) -> List[BundleCategoryFolderOption]:
        return _bundle_category_folder_options_flow(self, machine_key)

    def _bundle_project_folder_options(self, entries: List[BundleSummary]) -> List[BundleProjectFolderOption]:
        return _bundle_project_folder_options_flow(self, entries)

    def _default_target_project_path(self, project_option: BundleProjectFolderOption) -> str:
        return _default_target_project_path_flow(self, project_option)

    def _select_project_bundle_import_scope(
        self,
        *,
        selected_machine: BundleMachineFolderOption,
        selected_category: BundleCategoryFolderOption,
    ) -> Optional[BatchBundleImportSelection]:
        return _select_project_bundle_import_scope_flow(
            self,
            selected_machine=selected_machine,
            selected_category=selected_category,
        )

    def _prompt_value(
        self,
        *,
        title: str,
        prompt_label: str,
        help_lines: List[str],
        default: str = "",
        allow_empty: bool = True,
    ) -> Optional[str]:
        return _prompt_value_flow(
            self,
            title=title,
            prompt_label=prompt_label,
            help_lines=help_lines,
            default=default,
            allow_empty=allow_empty,
        )

    def _confirm_toggle(
        self,
        *,
        title: str,
        question: str,
        yes_label: str,
        no_label: str,
        default_yes: bool = False,
    ) -> bool:
        return _confirm_toggle_flow(
            self,
            title=title,
            question=question,
            yes_label=yes_label,
            no_label=no_label,
            default_yes=default_yes,
        )

    def _render_prompt_choice(
        self,
        *,
        title: str,
        prompt_label: str,
        help_lines: List[str],
        choices: Sequence[Tuple[str, str]],
        selected_index: int,
        allow_cancel: bool = True,
    ) -> None:
        return _render_prompt_choice_flow(
            self,
            title=title,
            prompt_label=prompt_label,
            help_lines=help_lines,
            choices=choices,
            selected_index=selected_index,
            allow_cancel=allow_cancel,
        )

    def _prompt_choice(
        self,
        *,
        title: str,
        prompt_label: str,
        help_lines: List[str],
        choices: Sequence[Tuple[str, str]],
        default: str = "",
        allow_cancel: bool = True,
    ) -> Optional[str]:
        return _prompt_choice_flow(
            self,
            title=title,
            prompt_label=prompt_label,
            help_lines=help_lines,
            choices=choices,
            default=default,
            allow_cancel=allow_cancel,
        )

    def _prompt_execution_mode(
        self,
        *,
        title: str,
        default_dry_run: bool = False,
    ) -> Optional[bool]:
        return _prompt_execution_mode_flow(
            self,
            title=title,
            default_dry_run=default_dry_run,
        )

    def _prompt_desktop_repair_scope(self) -> Optional[bool]:
        return _prompt_desktop_repair_scope_flow(self)

    def _show_detail_panel(
        self,
        title: str,
        lines: List[str],
        *,
        border_codes: Optional[Tuple[str, ...]] = None,
    ) -> None:
        return _show_detail_panel_flow(
            self,
            title,
            lines,
            border_codes=border_codes,
        )

    def _session_action_center(self, summary: SessionSummary) -> None:
        return _session_action_center_flow(self, summary)

    def _bundle_action_center(self, bundle: BundleSummary) -> None:
        return _bundle_action_center_flow(self, bundle)

    def _open_session_browser(self, *, mode: str) -> Optional[SessionSummary]:
        return _open_session_browser_flow(self, mode=mode)

    def _open_session_backup_browser(self, *, mode: str) -> Optional[SessionBackupSummary]:
        return _open_session_backup_browser_flow(self, mode=mode)

    def _open_bundle_browser(self, *, mode: str, source_group: str = "all") -> Optional[BundleSummary]:
        return _open_bundle_browser_flow(self, mode=mode, source_group=source_group)

    def _open_local_skill_browser(self, *, mode: str) -> Optional[LocalSkillSummary]:
        return _open_local_skill_browser_flow(self, mode=mode)

    def _open_skill_bundle_browser(self, *, mode: str) -> Optional[SkillBundleSummary]:
        return _open_skill_bundle_browser_flow(self, mode=mode)

    def _select_batch_bundle_import_scope(self) -> Optional[BatchBundleImportSelection]:
        return _select_batch_bundle_import_scope_flow(self)

    def _resolve_menu_action_request(self, menu_action: TuiMenuAction) -> Tuple[Optional[str], Optional[List[str]]]:
        return _resolve_menu_action_request_flow(self, menu_action)

    def _tui_help_text(self) -> None:
        return _tui_help_text_flow(self)

    def _render_home(self, selected_section_index: int) -> None:
        return _render_home_flow(self, selected_section_index)

    def _render_section_page(self, section_index: int, action_offset: int) -> None:
        return _render_section_page_flow(self, section_index, action_offset)

    def _execute_menu_action(self, chosen_action: TuiMenuAction) -> None:
        return _execute_menu_action_flow(self, chosen_action)

    def _run_action(
        self,
        action_name: str,
        cli_args: Sequence[str],
        *,
        dry_run: bool,
        runner: Callable[[], int],
        danger: bool,
        preview_cmd: Optional[str] = None,
        use_progress: bool = False,
    ) -> None:
        return _run_action_flow(
            self,
            action_name,
            cli_args,
            dry_run=dry_run,
            runner=runner,
            danger=danger,
            preview_cmd=preview_cmd,
            use_progress=use_progress,
        )

    def _confirm_dangerous_action(
        self,
        cli_args: Sequence[str],
        *,
        title: str = "危险操作确认",
        subtitle: str = "该操作会删除文件，且无法恢复。",
        warning: str = "Clean 会删除旧版无标记副本文件。",
        impact: str = "旧版无标记 clone 文件",
    ) -> bool:
        return _confirm_dangerous_action_flow(
            self,
            cli_args,
            title=title,
            subtitle=subtitle,
            warning=warning,
            impact=impact,
        )

    def run(self) -> int:
        selected_section = 0
        current_view = "home"
        section_action_offsets = {
            menu_section.section_id: 0
            for menu_section in self.menu_sections
        }
        last_size = (term_width(), term_height())
        sys.stdout.write("\033[?1049h\033[H")
        sys.stdout.flush()
        try:
            clear_screen()
            while True:
                if current_view == "home":
                    self._render_home(selected_section)
                else:
                    current_section = self.menu_sections[selected_section]
                    current_offset = section_action_offsets[current_section.section_id]
                    self._render_section_page(selected_section, current_offset)

                key = read_key(timeout_ms=200)
                current_size = (term_width(), term_height())
                if current_size != last_size:
                    last_size = current_size
                    continue
                if key is None:
                    continue

                if current_view == "home":
                    transition = apply_home_key(
                        key,
                        selected_section_index=selected_section,
                        section_count=len(self.menu_sections),
                    )
                    selected_section = transition.selected_section_index
                    current_view = transition.current_view
                    if transition.exit_requested:
                        return 0
                    if transition.show_help:
                        clear_screen()
                        self._tui_help_text()
                    continue

                current_section = self.menu_sections[selected_section]
                section_actions = self._actions_for_section(current_section.section_id)
                if not section_actions:
                    current_view = "home"
                    continue

                current_offset = clamp_selected_index(
                    section_action_offsets[current_section.section_id],
                    len(section_actions),
                )
                section_action_offsets[current_section.section_id] = current_offset

                selected_action = section_actions[current_offset][1]
                transition = apply_section_key(
                    key,
                    selected_section_index=selected_section,
                    section_count=len(self.menu_sections),
                    action_offset=current_offset,
                    action_count=len(section_actions),
                )
                selected_section = transition.selected_section_index
                current_view = transition.current_view
                section_action_offsets[current_section.section_id] = transition.action_offset

                if transition.exit_requested:
                    return 0
                if transition.show_help:
                    clear_screen()
                    self._tui_help_text()
                    continue
                if transition.execute_selected:
                    self._execute_menu_action(selected_action)
                    continue
                if transition.matched_hotkey:
                    matched_action = next(
                        (
                            menu_action
                            for _, menu_action in section_actions
                            if menu_action.hotkey == transition.matched_hotkey
                        ),
                        None,
                    )
                    if matched_action is not None:
                        self._execute_menu_action(matched_action)
        finally:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


def run_tui(context: ToolkitAppContext) -> int:
    return ToolkitTuiApp(context).run()
