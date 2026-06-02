"""Interactive browser flows extracted from the TUI app shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..errors import ToolkitError
from ..services.backups import list_session_backups
from ..services.browse import get_project_session_summaries, get_session_summaries
from ..services.bundle_management import delete_bundle_summaries
from ..services.clone import list_migrated_original_sessions
from ..services.skills_transfer import list_local_skills, list_skill_bundles
from ..support import default_local_project_target, detect_machine_key, project_label_from_path, project_label_to_key
from .action_flows import build_bundle_import_cli_args
from .navigation_state import (
    apply_list_key,
    clamp_selected_index,
    cycle_option_key,
    selection_window,
)
from .terminal import Ansi, align_line, app_logo_lines, ellipsize_middle, glyphs, render_box, style_text
from .terminal_io import read_key

if TYPE_CHECKING:
    from ..models import BundleSummary, LocalSkillSummary, MigratedOriginalSessionSummary, SessionBackupSummary, SessionSummary, SkillBundleSummary
    from .app import ToolkitTuiApp


def render_browser_frame(
    app: "ToolkitTuiApp",
    *,
    title: str,
    subtitle: str,
    info_lines: list[str],
    list_lines: list[str],
    list_border_codes: tuple[str, ...],
    box_width: int,
    center: bool,
) -> None:
    output_lines: list[str] = []
    for line in app_logo_lines(max_width=100):
        output_lines.append(align_line(line, box_width, center=center))
    output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    output_lines.append(align_line(style_text(title, Ansi.DIM), box_width, center=center))
    if subtitle:
        output_lines.append(align_line(style_text(subtitle, Ansi.DIM), box_width, center=center))
    output_lines.append("")

    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        output_lines.append(line)
    output_lines.append("")

    for line in render_box(list_lines, width=box_width, border_codes=list_border_codes):
        output_lines.append(line)

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    visible_lines = app._fit_lines_to_screen(output_lines)
    full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()


def open_project_session_browser(app: "ToolkitTuiApp") -> None:
    project_path = app._prompt_project_path(default=str(Path.cwd()))
    if not project_path:
        return

    filter_text = ""
    selected_index = 0
    selected_session_ids: set[str] = set()
    pointer = glyphs().get("pointer", ">")
    entries: list["SessionSummary"] = []
    needs_reload = True

    while True:
        project_label = project_label_from_path(project_path) or "root"
        project_key = project_label_to_key(project_label)
        export_root_preview = (
            f"{app.context.bundle_root_label}/{detect_machine_key()}/sessions/project/{project_key}/<timestamp>"
        )
        if needs_reload:
            try:
                entries = get_project_session_summaries(
                    app.paths,
                    project_path=project_path,
                    pattern=filter_text,
                    limit=200,
                )
            except ToolkitError as exc:
                app._show_detail_panel("读取项目会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return
            selected_session_ids.intersection_update({entry.session_id for entry in entries})
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · e 导出选中/当前 · a 选中全部 · / 搜索 · p 修改路径 · q 返回"

        info_lines = [
            f"{style_text('项目名', Ansi.DIM)} : {project_label}",
            f"{style_text('项目路径', Ansi.DIM)} : {project_path}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_session_ids)}",
            f"{style_text('导出目录', Ansi.DIM)} : {export_root_preview}",
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("这个项目路径下没有匹配会话。按 p 重新输入路径，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                summary = entries[idx]
                preview = summary.thread_name or summary.preview or summary.path.name
                marker = "[x]" if summary.session_id in selected_session_ids else "[ ]"
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker} "
                    f"{summary.session_id} | {summary.kind}/{summary.scope} | {preview}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    extra_parts: list[str] = []
                    if summary.cwd:
                        extra_parts.append(summary.cwd)
                    if summary.model_provider:
                        extra_parts.append(summary.model_provider)
                    if extra_parts:
                        list_lines.append(
                            "  "
                            + style_text(
                                ellipsize_middle(" · ".join(extra_parts), max(10, box_width - 10)),
                                Ansi.DIM,
                            )
                        )
                else:
                    list_lines.append(line)
        render_browser_frame(
            app,
            title="按项目路径查看并导出会话",
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.MAGENTA),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/空格/e/a/\\/d/p/q]：").strip()
            key = raw if raw else "ENTER"

        if key == " " and entries:
            _toggle_selected_session(selected_session_ids, entries[selected_index])
            continue

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries), detail_keys=("d",))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            app._show_detail_panel("会话详情", app._session_detail_lines(entries[selected_index]))
            continue
        if transition.exit_requested:
            return
        if transition.show_detail and entries:
            app._show_detail_panel("会话详情", app._session_detail_lines(entries[selected_index]))
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title="按项目路径查看并导出会话",
                prompt_label="输入搜索词",
                help_lines=[
                    "只在当前项目路径匹配到的会话中搜索。",
                    "可按 session_id / 预览 / provider / cwd / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "p":
            new_project_path = app._prompt_project_path(default=project_path)
            if not new_project_path:
                continue
            project_path = new_project_path
            filter_text = ""
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "e" and entries:
            selected_entries = _selected_or_current_sessions(entries, selected_index, selected_session_ids)
            _run_selected_session_export(app, selected_entries)
            selected_session_ids.clear()
            continue
        if key_str == "a":
            all_entries = _all_project_session_entries_for_current_filter(
                app,
                project_path=project_path,
                filter_text=filter_text,
            )
            _select_matching_sessions(app, selected_session_ids, all_entries, empty_title="项目会话选择")
            if all_entries:
                entries = all_entries
            continue


def open_session_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SessionSummary"]:
    filter_text = ""
    selected_index = 0
    selected_session_ids: set[str] = set()
    pointer = glyphs().get("pointer", ">")
    entries: list["SessionSummary"] = []
    needs_reload = True

    while True:
        if needs_reload:
            try:
                entries = get_session_summaries(app.paths, pattern=filter_text, limit=200)
            except ToolkitError as exc:
                app._show_detail_panel("读取会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return None
            selected_session_ids.intersection_update({entry.session_id for entry in entries})
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · / 搜索 · e 导出选中/当前 · a 选中全部 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "浏览并导出会话" if mode == "view" else "选择要导出的会话"

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_session_ids)}",
            f"{style_text('模式', Ansi.DIM)}   : {'浏览 / 直接操作' if mode == 'view' else '选择后导出'}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配会话。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                summary = entries[idx]
                preview = summary.thread_name or summary.preview or summary.path.name
                marker = "[x]" if summary.session_id in selected_session_ids else "[ ]"
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker if mode == 'view' else ''} "
                    f"{summary.session_id} | {summary.kind}/{summary.scope} | {preview}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    extra_parts: list[str] = []
                    if summary.cwd:
                        extra_parts.append(summary.cwd)
                    if summary.model_provider:
                        extra_parts.append(summary.model_provider)
                    if extra_parts:
                        list_lines.append(
                            "  "
                            + style_text(
                                ellipsize_middle(" · ".join(extra_parts), max(10, box_width - 10)),
                                Ansi.DIM,
                            )
                        )
                else:
                    list_lines.append(line)
        render_browser_frame(
            app,
            title=title,
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.MAGENTA),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw_prompt = "命令 [Enter/空格/e/a/\\/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key == " " and mode == "view" and entries:
            _toggle_selected_session(selected_session_ids, entries[selected_index])
            continue

        transition = apply_list_key(
            key,
            selected_index=selected_index,
            item_count=len(entries),
            detail_keys=("d",),
        )
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._show_detail_panel("会话详情", app._session_detail_lines(selected))
                continue
            return selected
        if transition.exit_requested:
            return None
        if transition.show_detail and entries:
            selected = entries[selected_index]
            app._show_detail_panel("会话详情", app._session_detail_lines(selected))
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title=title,
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 session_id / 标题 / provider / 路径 / cwd 搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "e" and entries and mode == "view":
            selected_entries = _selected_or_current_sessions(entries, selected_index, selected_session_ids)
            _run_selected_session_export(app, selected_entries)
            selected_session_ids.clear()
            continue
        if key_str == "a" and mode == "view":
            all_entries = _all_session_entries_for_current_filter(app, filter_text=filter_text)
            _select_matching_sessions(app, selected_session_ids, all_entries, empty_title="会话选择")
            if all_entries:
                entries = all_entries
            continue


def _toggle_selected_session(selected_session_ids: set[str], summary: "SessionSummary") -> None:
    if summary.session_id in selected_session_ids:
        selected_session_ids.remove(summary.session_id)
    else:
        selected_session_ids.add(summary.session_id)


def _selected_or_current_sessions(
    entries: list["SessionSummary"],
    selected_index: int,
    selected_session_ids: set[str],
) -> list["SessionSummary"]:
    selected_entries = [
        entry
        for entry in entries
        if entry.session_id in selected_session_ids
    ]
    if not selected_entries and entries:
        selected_entries = [entries[selected_index]]
    return selected_entries


def _all_session_entries_for_current_filter(
    app: "ToolkitTuiApp",
    *,
    filter_text: str,
    archived_only: bool = False,
) -> list["SessionSummary"]:
    try:
        return get_session_summaries(
            app.paths,
            pattern=filter_text,
            limit=None,
            archived_only=archived_only,
        )
    except ToolkitError as exc:
        app._show_detail_panel("读取会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
        return []


def _all_project_session_entries_for_current_filter(
    app: "ToolkitTuiApp",
    *,
    project_path: str,
    filter_text: str,
) -> list["SessionSummary"]:
    try:
        return get_project_session_summaries(
            app.paths,
            project_path=project_path,
            pattern=filter_text,
            limit=None,
        )
    except ToolkitError as exc:
        app._show_detail_panel("读取项目会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
        return []


def _select_matching_sessions(
    app: "ToolkitTuiApp",
    selected_session_ids: set[str],
    entries: list["SessionSummary"],
    *,
    empty_title: str,
) -> None:
    if not entries:
        app._show_detail_panel(
            empty_title,
            ["当前没有匹配会话。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    for entry in entries:
        selected_session_ids.add(entry.session_id)


def _run_selected_session_export(app: "ToolkitTuiApp", summaries: list["SessionSummary"]) -> None:
    if not summaries:
        app._show_detail_panel(
            "导出会话",
            ["当前没有可导出的会话。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    session_ids = [summary.session_id for summary in summaries]
    count = len(session_ids)
    cli_args = ["export", *session_ids]
    action_name = (
        f"导出会话 {session_ids[0]} 为 Bundle"
        if count == 1
        else f"导出 {count} 个会话为 Bundle"
    )
    app._run_action(
        action_name,
        cli_args,
        dry_run=False,
        runner=lambda args=cli_args: app._run_toolkit(args),
        danger=False,
    )


def open_archived_session_browser(app: "ToolkitTuiApp") -> None:
    filter_text = ""
    selected_index = 0
    selected_session_ids: set[str] = set()
    pointer = glyphs().get("pointer", ">")
    entries: list["SessionSummary"] = []
    needs_reload = True

    while True:
        if needs_reload:
            try:
                entries = get_session_summaries(
                    app.paths,
                    pattern=filter_text,
                    limit=None,
                    archived_only=True,
                )
            except ToolkitError as exc:
                app._show_detail_panel("读取归档会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return
            selected_session_ids.intersection_update({entry.session_id for entry in entries})
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = "↑/↓ 选择 · 空格勾选 · Enter/d 预览 · / 搜索 · x 删除选中/当前 · a 选中全部 · q 返回"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('归档数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_session_ids)}",
            f"{style_text('目录', Ansi.DIM)}     : {app.paths.archived_sessions_dir}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配归档会话。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                summary = entries[idx]
                title = summary.thread_name or "（未命名）"
                marker = "[x]" if summary.session_id in selected_session_ids else "[ ]"
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker} "
                    f"{summary.session_id} | {summary.kind} | {title}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    extra_parts: list[str] = []
                    if summary.preview and summary.preview != title:
                        extra_parts.append(f"预览：{summary.preview}")
                    if summary.cwd:
                        extra_parts.append(f"目录：{summary.cwd}")
                    if summary.model_provider:
                        extra_parts.append(summary.model_provider)
                    extra_parts.append(str(summary.path))
                    list_lines.append(
                        "  "
                        + style_text(
                            ellipsize_middle(" · ".join(extra_parts), max(10, box_width - 10)),
                            Ansi.DIM,
                        )
                    )
                else:
                    list_lines.append(line)

        render_browser_frame(
            app,
            title="删除归档会话",
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.GREEN),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/空格/\\/x/a/d/q]：").strip()
            key = raw if raw else "ENTER"

        if key == " " and entries:
            selected = entries[selected_index]
            if selected.session_id in selected_session_ids:
                selected_session_ids.remove(selected.session_id)
            else:
                selected_session_ids.add(selected.session_id)
            continue

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries), detail_keys=("d",))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if entries:
                app._show_detail_panel(
                    "归档会话预览",
                    _archived_session_preview_lines(entries[selected_index]),
                    border_codes=(Ansi.DIM, Ansi.GREEN),
                )
            continue
        if transition.exit_requested:
            return
        if transition.show_detail and entries:
            app._show_detail_panel(
                "归档会话预览",
                _archived_session_preview_lines(entries[selected_index]),
                border_codes=(Ansi.DIM, Ansi.GREEN),
            )
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title="删除归档会话",
                prompt_label="输入搜索词",
                help_lines=[
                    "只在归档会话中搜索。",
                    "可按 session_id / 标题 / provider / cwd / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "x" and entries:
            selected_ids = [entry.session_id for entry in entries if entry.session_id in selected_session_ids]
            if not selected_ids:
                selected_ids = [entries[selected_index].session_id]
            cli_args = ["delete-archived-sessions", *selected_ids]
            count = len(selected_ids)
            selected_paths = [str(entry.path) for entry in entries if entry.session_id in set(selected_ids)]
            warning = (
                f"将删除归档会话 {selected_ids[0]}。"
                if count == 1
                else f"将删除已勾选的 {count} 个归档会话。"
            )
            impact = selected_paths[0] if count == 1 and selected_paths else f"{count} 个归档会话"
            if not app._confirm_dangerous_action(
                cli_args,
                title="删除归档会话确认",
                subtitle="该操作会删除选中的归档会话文件。",
                warning=warning,
                impact=impact,
            ):
                continue
            app._run_action(
                f"删除归档会话 {selected_ids[0]}" if count == 1 else f"删除 {count} 个归档会话",
                cli_args,
                dry_run=False,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=True,
            )
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "a":
            all_entries = _all_session_entries_for_current_filter(
                app,
                filter_text=filter_text,
                archived_only=True,
            )
            _select_matching_sessions(app, selected_session_ids, all_entries, empty_title="归档会话选择")
            if all_entries:
                entries = all_entries
            continue


def open_migrated_original_session_browser(app: "ToolkitTuiApp") -> None:
    filter_text = ""
    selected_index = 0
    selected_session_ids: set[str] = set()
    pointer = glyphs().get("pointer", ">")
    entries: list["MigratedOriginalSessionSummary"] = []
    needs_reload = True

    while True:
        if needs_reload:
            try:
                entries = _migrated_original_entries_for_filter(app, filter_text)
            except ToolkitError as exc:
                app._show_detail_panel("读取旧 Provider 会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return
            selected_session_ids.intersection_update({entry.session_id for entry in entries})
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = "↑/↓ 选择 · 空格勾选 · Enter/d 预览 · / 搜索 · x 删除选中/当前 · a 选中全部 · q 返回"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_session_ids)}",
            f"{style_text('当前 Provider', Ansi.DIM)} : {app.context.target_provider}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有可清理的旧 Provider 会话。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                summary = entries[idx]
                marker = "[x]" if summary.session_id in selected_session_ids else "[ ]"
                title = summary.preview or summary.path.name
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker} "
                    f"{summary.session_id} | {summary.model_provider or '-'} -> {summary.cloned_provider or app.context.target_provider} | {title}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    extra_parts = [
                        f"新副本：{summary.cloned_session_id}",
                    ]
                    if summary.cwd:
                        extra_parts.append(f"目录：{summary.cwd}")
                    extra_parts.append(str(summary.path))
                    list_lines.append(
                        "  "
                        + style_text(
                            ellipsize_middle(" · ".join(extra_parts), max(10, box_width - 10)),
                            Ansi.DIM,
                        )
                    )
                else:
                    list_lines.append(line)

        render_browser_frame(
            app,
            title="删除已复制的旧 Provider 会话",
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.RED),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/空格/\\/x/a/d/q]：").strip()
            key = raw if raw else "ENTER"

        if key == " " and entries:
            selected = entries[selected_index]
            if selected.session_id in selected_session_ids:
                selected_session_ids.remove(selected.session_id)
            else:
                selected_session_ids.add(selected.session_id)
            continue

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries), detail_keys=("d",))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if entries:
                app._show_detail_panel(
                    "旧 Provider 会话预览",
                    _migrated_original_preview_lines(entries[selected_index]),
                    border_codes=(Ansi.DIM, Ansi.RED),
                )
            continue
        if transition.exit_requested:
            return
        if transition.show_detail and entries:
            app._show_detail_panel(
                "旧 Provider 会话预览",
                _migrated_original_preview_lines(entries[selected_index]),
                border_codes=(Ansi.DIM, Ansi.RED),
            )
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title="删除已复制的旧 Provider 会话",
                prompt_label="输入搜索词",
                help_lines=[
                    "只搜索已经复制到当前 Provider 的旧 Provider 原始会话。",
                    "可按 session_id / 新副本 id / provider / cwd / 预览 / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "x" and entries:
            selected_ids = [entry.session_id for entry in entries if entry.session_id in selected_session_ids]
            if not selected_ids:
                selected_ids = [entries[selected_index].session_id]
            cli_args = ["delete-migrated-originals", *selected_ids]
            count = len(selected_ids)
            selected_paths = [str(entry.path) for entry in entries if entry.session_id in set(selected_ids)]
            warning = (
                f"将删除旧 Provider 原始会话 {selected_ids[0]}。"
                if count == 1
                else f"将删除已勾选的 {count} 个旧 Provider 原始会话。"
            )
            impact = selected_paths[0] if count == 1 and selected_paths else f"{count} 个旧 Provider 原始会话"
            if not app._confirm_dangerous_action(
                cli_args,
                title="删除旧 Provider 会话确认",
                subtitle="仅删除已经存在当前 Provider 副本的旧原始会话。",
                warning=warning,
                impact=impact,
            ):
                continue
            app._run_action(
                f"删除旧 Provider 会话 {selected_ids[0]}" if count == 1 else f"删除 {count} 个旧 Provider 会话",
                cli_args,
                dry_run=False,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=True,
            )
            selected_index = 0
            selected_session_ids.clear()
            needs_reload = True
            continue
        if key_str == "a":
            all_entries = _migrated_original_entries_for_filter(app, filter_text)
            if not all_entries:
                app._show_detail_panel(
                    "旧 Provider 会话选择",
                    ["当前没有匹配会话。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            for entry in all_entries:
                selected_session_ids.add(entry.session_id)
            entries = all_entries
            continue


def _migrated_original_entries_for_filter(
    app: "ToolkitTuiApp",
    filter_text: str,
) -> list["MigratedOriginalSessionSummary"]:
    entries = list_migrated_original_sessions(app.paths, target_provider=app.context.target_provider)
    needle = filter_text.strip()
    if not needle:
        return entries
    return [
        entry
        for entry in entries
        if needle
        in " ".join(
            [
                entry.session_id,
                entry.cloned_session_id,
                entry.model_provider,
                entry.cloned_provider,
                entry.kind,
                entry.cwd,
                entry.preview,
                str(entry.path),
                str(entry.cloned_path),
            ]
        )
    ]


def _migrated_original_preview_lines(summary: "MigratedOriginalSessionSummary") -> list[str]:
    return [
        f"{style_text('会话预览', Ansi.DIM)} : {summary.preview or '（无）'}",
        f"{style_text('旧 Session ID', Ansi.DIM)} : {summary.session_id}",
        f"{style_text('旧 Provider', Ansi.DIM)} : {summary.model_provider or '-'}",
        f"{style_text('新 Session ID', Ansi.DIM)} : {summary.cloned_session_id}",
        f"{style_text('新 Provider', Ansi.DIM)} : {summary.cloned_provider or '-'}",
        f"{style_text('类型', Ansi.DIM)}     : {summary.kind or '-'}",
        f"{style_text('工作目录', Ansi.DIM)} : {summary.cwd or '（空）'}",
        f"{style_text('旧会话文件', Ansi.DIM)} : {summary.path}",
        f"{style_text('新副本文件', Ansi.DIM)} : {summary.cloned_path}",
    ]


def _archived_session_preview_lines(summary: "SessionSummary") -> list[str]:
    return [
        f"{style_text('会话名称', Ansi.DIM)} : {summary.thread_name or '（未命名）'}",
        f"{style_text('会话预览', Ansi.DIM)} : {summary.preview or '（无）'}",
        f"{style_text('Session ID', Ansi.DIM)} : {summary.session_id}",
        f"{style_text('类型', Ansi.DIM)}     : {summary.kind}",
        f"{style_text('Provider', Ansi.DIM)} : {summary.model_provider or '-'}",
        f"{style_text('工作目录', Ansi.DIM)} : {summary.cwd or '（空）'}",
        f"{style_text('归档文件', Ansi.DIM)} : {summary.path}",
    ]


def open_session_backup_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SessionBackupSummary"]:
    filter_text = ""
    selected_index = 0
    pointer = glyphs().get("pointer", ">")
    entries: list["SessionBackupSummary"] = []
    needs_reload = True

    while True:
        if needs_reload:
            try:
                entries = list_session_backups(app.paths, pattern=filter_text, limit=200)
            except ToolkitError as exc:
                app._show_detail_panel("读取会话备份失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return None
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · Enter 查看详情 · / 搜索 · r 恢复当前 · x 删除当前 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "管理会话备份" if mode == "view" else "选择会话备份"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('备份来源', Ansi.DIM)} : 覆盖前保存的本地会话，以及恢复前自动生成的安全备份",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配会话备份。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                backup = entries[idx]
                line = (
                    f"{pointer if idx == selected_index else ' '} "
                    f"{backup.session_id} | {backup.scope} | {backup.preview or backup.backup_path.name}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    target_state = "当前文件存在" if backup.target_exists else "当前文件缺失"
                    detail_line = (
                        f"{backup.backup_time_label} | {target_state} | "
                        f"{backup.kind or '-'} | {backup.model_provider or '-'} | {_format_size(backup.size_bytes)}"
                    )
                    list_lines.append("  " + style_text(ellipsize_middle(detail_line, max(10, box_width - 10)), Ansi.DIM))
                else:
                    list_lines.append(line)

        render_browser_frame(
            app,
            title=title,
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.GREEN),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw_prompt = "命令 [Enter/\\/r/x/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._show_detail_panel(
                    "会话备份详情",
                    app._session_backup_detail_lines(selected),
                    border_codes=(Ansi.DIM, Ansi.GREEN),
                )
                continue
            return selected
        if transition.exit_requested:
            return None
        if transition.show_detail and entries:
            app._show_detail_panel(
                "会话备份详情",
                app._session_backup_detail_lines(entries[selected_index]),
                border_codes=(Ansi.DIM, Ansi.GREEN),
            )
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title=title,
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 session_id / provider / cwd / 预览 / 备份路径 / 目标路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            needs_reload = True
            continue
        if key_str == "x" and entries and mode == "view":
            selected = entries[selected_index]
            cli_args = ["delete-backup", str(selected.backup_path)]
            if not app._confirm_dangerous_action(
                cli_args,
                title="删除会话备份确认",
                subtitle="该操作只删除选中的 .bak.* 备份文件，不删除当前会话文件。",
                warning=f"将删除会话 {selected.session_id} 的这份备份。",
                impact=str(selected.backup_path),
            ):
                continue
            app._run_action(
                f"删除会话备份 {selected.session_id}",
                cli_args,
                dry_run=False,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=True,
            )
            selected_index = 0
            needs_reload = True
            continue
        if key_str == "r" and entries and mode == "view":
            selected = entries[selected_index]
            cli_args = ["restore-backup", str(selected.backup_path)]
            if not app._confirm_dangerous_action(
                cli_args,
                title="恢复会话备份确认",
                subtitle="该操作会用备份覆盖当前会话文件；覆盖前会再备份当前文件。",
                warning=f"将恢复会话 {selected.session_id} 的备份。",
                impact=f"{selected.backup_path} -> {selected.target_path}",
            ):
                continue
            app._run_action(
                f"恢复会话备份 {selected.session_id}",
                cli_args,
                dry_run=False,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=False,
            )
            selected_index = 0
            needs_reload = True
            continue


def open_bundle_browser(app: "ToolkitTuiApp", *, mode: str, source_group: str = "all") -> Optional["BundleSummary"]:
    browse_mode = mode in {"view", "browse"}
    import_mode = mode == "import"
    filter_text = ""
    selected_index = 0
    export_group_filter = ""
    machine_filter = ""
    latest_only = False
    selected_bundle_dirs: set[str] = set()
    pointer = glyphs().get("pointer", ">")
    snapshot = None
    entries: list["BundleSummary"] = []
    needs_reload = True

    while True:
        if needs_reload:
            try:
                snapshot, machine_filter, export_group_filter = app._bundle_browser_snapshot(
                    filter_text=filter_text,
                    machine_filter=machine_filter,
                    export_group_filter=export_group_filter,
                    latest_only=latest_only,
                    source_group=source_group,
                )
                entries = snapshot.entries
            except ToolkitError as exc:
                app._show_detail_panel("读取 Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
                return None
            visible_dir_keys = {_bundle_dir_key(entry) for entry in entries}
            selected_bundle_dirs.intersection_update(visible_dir_keys)
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        if browse_mode:
            subtitle = "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · x 删除选中/当前 · a 选中全部 · / 搜索 · s 类别 · m 机器 · l 历史 · q 返回"
            title = "浏览 Bundle"
        elif import_mode:
            subtitle = "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · i 导入选中/当前 · a 选中全部 · / 搜索 · s 类别 · m 机器 · l 历史 · q 返回"
            title = "导入 Bundle 为会话"
        else:
            subtitle = "↑/↓ 选择 · Enter 确认 · / 搜索 · s 类别 · m 来源机器 · l 历史范围 · d 查看详情 · q 返回"
            title = "选择要导入的 Bundle"

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_bundle_dirs)}",
            f"{style_text('Bundle 类别', Ansi.DIM)} : {snapshot.current_export_group_label}",
            f"{style_text('来源机器', Ansi.DIM)} : {snapshot.current_machine_label}",
            f"{style_text('历史范围', Ansi.DIM)} : {'每台机器每个会话仅显示最新一份' if latest_only else '显示全部历史'}",
        ]
        info_lines.extend(app._github_sync_hint_lines())

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Bundle。按 / 修改搜索词，按 s/m/l 修改筛选，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                bundle = entries[idx]
                title_text = bundle.thread_name or "（无标题）"
                machine_label = bundle.source_machine or "旧布局"
                source_label = _bundle_import_source_label(app, bundle)
                time_label = (bundle.exported_at or bundle.updated_at or "-")[:19]
                marker = "[x]" if _bundle_dir_key(bundle) in selected_bundle_dirs else "[ ]"
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker if browse_mode or import_mode else ''} "
                    f"{bundle.session_id} | {source_label} | {machine_label} | {bundle.export_group_label or '（未识别）'} | "
                    f"{time_label} | {title_text}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    detail_line = (
                        f"{bundle.session_kind or '-'} | {bundle.session_cwd or '（无工作目录）'} | "
                        f"{_bundle_relative_import_path(app, bundle)}"
                    )
                    list_lines.append("  " + style_text(ellipsize_middle(detail_line, max(10, box_width - 10)), Ansi.DIM))
                else:
                    list_lines.append(line)
        render_browser_frame(
            app,
            title=title,
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.GREEN),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw_prompt = (
                "命令 [Enter/空格/x/a/\\/s/m/l/d/q]："
                if browse_mode
                else "命令 [Enter/空格/i/a/\\/s/m/l/d/q]："
                if import_mode
                else "命令 [Enter/\\/s/m/l/d/q]："
            )
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key == " " and entries and (browse_mode or import_mode):
            _toggle_selected_bundle(selected_bundle_dirs, entries[selected_index])
            continue

        transition = apply_list_key(
            key,
            selected_index=selected_index,
            item_count=len(entries),
            detail_keys=("d",),
        )
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if browse_mode or import_mode:
                app._show_detail_panel("Bundle 详情", app._bundle_detail_lines(selected), border_codes=(Ansi.DIM, Ansi.GREEN))
                continue
            return selected
        if transition.exit_requested:
            return None
        if transition.show_detail and entries:
            bundle = entries[selected_index]
            app._show_detail_panel("Bundle 详情", app._bundle_detail_lines(bundle), border_codes=(Ansi.DIM, Ansi.GREEN))
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title=title,
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 session_id / 标题 / Bundle 类别 / 来源机器 / kind / cwd / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_bundle_dirs.clear()
            needs_reload = True
            continue
        if key_str == "s":
            export_group_filter = cycle_option_key(snapshot.export_group_options, export_group_filter)
            selected_index = 0
            selected_bundle_dirs.clear()
            needs_reload = True
            continue
        if key_str == "m":
            machine_filter = cycle_option_key(snapshot.machine_options, machine_filter)
            selected_index = 0
            selected_bundle_dirs.clear()
            needs_reload = True
            continue
        if key_str == "l":
            latest_only = not latest_only
            selected_index = 0
            selected_bundle_dirs.clear()
            needs_reload = True
            continue
        if key_str == "a" and (browse_mode or import_mode):
            all_entries = _all_bundle_entries_for_current_filters(
                app,
                filter_text=filter_text,
                machine_filter=machine_filter,
                export_group_filter=export_group_filter,
                latest_only=latest_only,
                source_group=source_group,
            )
            _select_matching_bundles(app, selected_bundle_dirs, all_entries)
            if all_entries:
                entries = all_entries
            continue
        if key_str == "x" and entries and browse_mode:
            selected_entries = _selected_or_current_bundles(entries, selected_index, selected_bundle_dirs)
            if _delete_selected_bundles(app, selected_entries):
                selected_bundle_dirs.clear()
                needs_reload = True
            continue
        if key_str == "i" and entries and import_mode:
            selected_entries = _selected_or_current_bundles(entries, selected_index, selected_bundle_dirs)
            _run_bundle_import(
                app,
                selected_entries,
            )
            selected_bundle_dirs.clear()
            continue

def _bundle_dir_key(bundle: "BundleSummary") -> str:
    try:
        return str(bundle.bundle_dir.resolve())
    except OSError:
        return str(bundle.bundle_dir.expanduser())


def _bundle_import_source_label(app: "ToolkitTuiApp", bundle: "BundleSummary") -> str:
    bundle_dir = bundle.bundle_dir.expanduser()
    local_workspace = getattr(app.paths, "local_bundle_workspace", None)
    legacy_workspace = getattr(app.paths, "legacy_session_bundle_workspace", None)
    legacy_bundle_root = getattr(app.paths, "legacy_bundle_root", None)
    legacy_desktop_root = getattr(app.paths, "legacy_desktop_bundle_root", None)
    if local_workspace is not None and _path_is_relative_to(bundle_dir, local_workspace.expanduser()):
        return "codex_bundles"
    if legacy_bundle_root is not None and _path_is_relative_to(bundle_dir, legacy_bundle_root.expanduser()):
        return "旧 bundles"
    if legacy_desktop_root is not None and _path_is_relative_to(bundle_dir, legacy_desktop_root.expanduser()):
        return "旧 desktop"
    if legacy_workspace is not None and _path_is_relative_to(bundle_dir, legacy_workspace.expanduser()):
        return "codex_sessions"
    return {
        "bundle": "Bundle 工作区",
        "desktop": "Desktop 导出区",
        "all": "Bundle",
    }.get(bundle.source_group, bundle.source_group or "未知来源")


def _bundle_relative_import_path(app: "ToolkitTuiApp", bundle: "BundleSummary") -> str:
    bundle_dir = bundle.bundle_dir.expanduser()
    root_attrs = [
        ("codex_bundles", "local_bundle_workspace"),
        ("codex_sessions", "legacy_session_bundle_workspace"),
        ("legacy bundles", "legacy_bundle_root"),
        ("legacy desktop", "legacy_desktop_bundle_root"),
    ]
    for label, attr_name in root_attrs:
        root = getattr(app.paths, attr_name, None)
        if root is None:
            continue
        try:
            relative = bundle_dir.relative_to(root.expanduser())
        except ValueError:
            continue
        return f"{label}/{relative.as_posix()}"
    return str(bundle_dir)


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _all_bundle_entries_for_current_filters(
    app: "ToolkitTuiApp",
    *,
    filter_text: str,
    machine_filter: str,
    export_group_filter: str,
    latest_only: bool,
    source_group: str,
) -> list["BundleSummary"]:
    try:
        snapshot, _, _ = app._bundle_browser_snapshot(
            filter_text=filter_text,
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
            latest_only=latest_only,
            source_group=source_group,
            limit=None,
        )
    except ToolkitError as exc:
        app._show_detail_panel("读取 Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
        return []
    return list(snapshot.entries)


def _toggle_selected_bundle(selected_bundle_dirs: set[str], bundle: "BundleSummary") -> None:
    bundle_key = _bundle_dir_key(bundle)
    if bundle_key in selected_bundle_dirs:
        selected_bundle_dirs.remove(bundle_key)
    else:
        selected_bundle_dirs.add(bundle_key)


def _selected_or_current_bundles(
    entries: list["BundleSummary"],
    selected_index: int,
    selected_bundle_dirs: set[str],
) -> list["BundleSummary"]:
    selected_entries = [
        entry
        for entry in entries
        if _bundle_dir_key(entry) in selected_bundle_dirs
    ]
    if not selected_entries and entries:
        selected_entries = [entries[selected_index]]
    return selected_entries


def _select_matching_bundles(
    app: "ToolkitTuiApp",
    selected_bundle_dirs: set[str],
    entries: list["BundleSummary"],
) -> None:
    if not entries:
        app._show_detail_panel(
            "Bundle 选择",
            ["当前没有匹配 Bundle。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    for entry in entries:
        selected_bundle_dirs.add(_bundle_dir_key(entry))


def _delete_selected_bundles(app: "ToolkitTuiApp", bundles: list["BundleSummary"]) -> bool:
    if not bundles:
        app._show_detail_panel(
            "删除 Bundle",
            ["当前没有可删除的 Bundle。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return False
    preview = [f"{bundle.session_id} | {_bundle_relative_import_path(app, bundle)}" for bundle in bundles[:8]]
    if len(bundles) > 8:
        preview.append(f"... 还有 {len(bundles) - 8} 个 Bundle")
    confirmed = app._confirm_dangerous_action(
        ["delete-bundles", *[str(bundle.bundle_dir) for bundle in bundles]],
        title="删除 Bundle",
        subtitle="该操作会删除本地 Bundle 目录，且无法恢复。",
        warning=f"将删除 {len(bundles)} 个本地 Bundle 目录。",
        impact="本地 Bundle 目录：" + "；".join(preview),
    )
    if not confirmed:
        return False

    results = delete_bundle_summaries(app.paths, bundles)
    deleted = [result for result in results if result.deleted]
    failed = [result for result in results if result.error]
    detail_lines = [
        f"已删除 Bundle：{len(deleted)}",
        f"失败：{len(failed)}",
    ]
    detail_lines.extend(f"- {result.session_id} | {result.bundle_dir}" for result in deleted[:12])
    if len(deleted) > 12:
        detail_lines.append(f"... 还有 {len(deleted) - 12} 个已删除")
    detail_lines.extend(f"[失败] {result.bundle_dir}: {result.error}" for result in failed)
    app._show_detail_panel("删除 Bundle 完成", detail_lines, border_codes=(Ansi.DIM, Ansi.GREEN))
    return True


def _run_bundle_import(
    app: "ToolkitTuiApp",
    bundles: list["BundleSummary"],
) -> None:
    if not bundles:
        app._show_detail_panel(
            "导入 Bundle",
            ["当前没有可导入的 Bundle。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    target_project_path = _prompt_bundle_import_target_project(app, bundles)
    if target_project_path is None:
        return
    create_question = "导入后会注册到 Desktop 左侧线程栏；如果工作目录缺失，是否自动创建"
    default_yes = False
    if target_project_path:
        if Path(target_project_path).exists():
            create_question = "导入后会注册到 Desktop 左侧线程栏；如果目标项目路径或其子目录缺失，是否自动创建"
        else:
            create_question = "导入后会注册到 Desktop 左侧线程栏；目标项目路径不存在，是否先创建后再导入"
            default_yes = True
    create_missing_workspace = app._confirm_toggle(
        title="导入 Bundle 为会话",
        question=create_question,
        yes_label="y",
        no_label="n",
        default_yes=default_yes,
    )
    if target_project_path:
        project_key = _common_project_key(bundles)
        selection = _bundle_import_selection(
            bundles,
            project_filter=project_key,
            target_project_path=target_project_path,
        )
    else:
        selection = _bundle_import_selection(bundles)
    cli_args = build_bundle_import_cli_args(selection, create_missing_workspace=create_missing_workspace)

    count = len(bundles)
    if count == 1:
        action_name = f"导入 Bundle {bundles[0].session_id} 为会话"
    else:
        action_name = f"导入 {count} 个 Bundle 为会话"
    action_name += "（显示到 Desktop）"
    if target_project_path:
        action_name += f"（项目：{_common_project_label(bundles)}）"
    if create_missing_workspace:
        action_name += "（自动创建目录）"

    app._run_action(
        action_name,
        cli_args,
        dry_run=False,
        runner=lambda args=cli_args: app._run_toolkit(args),
        danger=False,
    )


def _prompt_bundle_import_target_project(app: "ToolkitTuiApp", bundles: list["BundleSummary"]) -> Optional[str]:
    project_bundles = [bundle for bundle in bundles if bundle.export_group == "project"]
    if not project_bundles:
        return ""
    if len(project_bundles) != len(bundles):
        app._show_detail_panel(
            "导入 Bundle",
            ["project Bundle 需要单独导入。请先按 Bundle 类别筛选 project，或只勾选同一个项目。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return None
    project_keys = {bundle.project_key for bundle in project_bundles if bundle.project_key}
    if len(project_keys) != 1:
        app._show_detail_panel(
            "导入 Bundle",
            ["一次只能导入同一个项目文件夹下的 project Bundle。请先按 Bundle 类别/来源机器筛选，或只勾选同一个项目。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return None
    project_label = _common_project_label(project_bundles)
    project_path = next((bundle.project_path for bundle in project_bundles if bundle.project_path), "")
    default_target, local_status = default_local_project_target(project_label, project_path)
    local_status_label = {
        "same_path": "原路径可用",
        "same_name": "同名项目可用",
    }.get(local_status, "本机未找到")
    target_project_path = app._prompt_value(
        title=f"导入项目 {project_label}",
        prompt_label="输入本机目标项目路径",
        help_lines=[
            f"项目文件夹：{project_label}",
            f"原项目路径：{project_path or '（未记录）'}",
            f"本机匹配状态：{local_status_label}",
            f"默认目标路径：{default_target or '（未设置）'}",
            "导入时会把这些会话的 cwd 映射到新的本机路径。",
        ],
        default=default_target,
        allow_empty=False,
    )
    return target_project_path or None


def _common_project_key(bundles: list["BundleSummary"]) -> str:
    return next((bundle.project_key for bundle in bundles if bundle.project_key), "")


def _common_project_label(bundles: list["BundleSummary"]) -> str:
    return next((bundle.project_label for bundle in bundles if bundle.project_label), _common_project_key(bundles) or "project")


def _bundle_import_selection(
    bundles: list["BundleSummary"],
    *,
    project_filter: str = "",
    target_project_path: str = "",
):
    from .view_models import BatchBundleImportSelection

    return BatchBundleImportSelection(
        entries=bundles,
        machine_filter=_common_machine_key(bundles),
        machine_label=_common_machine_label(bundles),
        export_group_filter=_common_export_group(bundles),
        export_group_label=_common_export_group_label(bundles),
        latest_only=False,
        project_filter=project_filter,
        project_label=_common_project_label(bundles) if project_filter else "",
        project_source_path=next((bundle.project_path for bundle in bundles if bundle.project_path), ""),
        target_project_path=target_project_path,
    )


def _common_machine_key(bundles: list["BundleSummary"]) -> str:
    keys = {bundle.source_machine_key for bundle in bundles if bundle.source_machine_key}
    return next(iter(keys)) if len(keys) == 1 else ""


def _common_machine_label(bundles: list["BundleSummary"]) -> str:
    labels = {bundle.source_machine for bundle in bundles if bundle.source_machine}
    return next(iter(labels)) if len(labels) == 1 else ""


def _common_export_group(bundles: list["BundleSummary"]) -> str:
    groups = {bundle.export_group for bundle in bundles if bundle.export_group}
    return next(iter(groups)) if len(groups) == 1 else ""


def _common_export_group_label(bundles: list["BundleSummary"]) -> str:
    labels = {bundle.export_group_label for bundle in bundles if bundle.export_group_label}
    return next(iter(labels)) if len(labels) == 1 else ""


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def open_local_skill_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["LocalSkillSummary"]:
    filter_text = ""
    selected_index = 0
    include_system = False
    selected_skills: set[tuple[str, str]] = set()
    pointer = glyphs().get("pointer", ">")

    while True:
        try:
            entries = list_local_skills(
                app.paths,
                pattern=filter_text,
                include_system=include_system,
            )
        except ToolkitError as exc:
            app._show_detail_panel("读取本机 Skills 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
            return None
        visible_keys = {(entry.source_root, entry.relative_dir) for entry in entries}
        selected_skills.intersection_update(visible_keys)

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · / 搜索 · g 切换系统 Skills · e 导出选中/当前 · a 选中全部 · q 返回"
            if mode == "view"
            else (
                "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · / 搜索 · x 删除选中/当前 · a 选中全部 · q 返回"
                if mode == "delete"
                else "↑/↓ 选择 · Enter 确认 · / 搜索 · g 切换系统 Skills · d 查看详情 · q 返回"
            )
        )
        title = (
            "浏览本机 Skills"
            if mode == "view"
            else "删除本机 Skills"
            if mode == "delete"
            else "选择要导出的 Skill"
        )
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('显示范围', Ansi.DIM)} : {'自定义 + 系统/运行时 Skills' if include_system else '仅自定义 Skills'}",
        ]
        if mode in {"view", "delete"}:
            custom_count = sum(1 for entry in entries if entry.location_kind == "custom")
            info_lines.append(f"{style_text('自定义', Ansi.DIM)}   : {custom_count}")
            info_lines.append(f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_skills)}")
        info_lines.extend(app._github_sync_hint_lines())

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Skills。按 / 修改搜索词，按 g 切换显示范围，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                skill = entries[idx]
                marker = ""
                if mode in {"view", "delete"}:
                    skill_key = (skill.source_root, skill.relative_dir)
                    marker = "[x] " if skill_key in selected_skills else "[ ] "
                line = (
                    f"{pointer if idx == selected_index else ' '} "
                    f"{marker}{skill.name} | {skill.source_root}/{skill.location_kind} | {skill.relative_dir}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.BRIGHT_BLUE))
                    list_lines.append(
                        "  "
                        + style_text(
                            ellipsize_middle(str(skill.skill_dir), max(10, box_width - 10)),
                            Ansi.DIM,
                        )
                    )
                else:
                    list_lines.append(line)

        render_browser_frame(
            app,
            title=title,
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw_prompt = (
                "命令 [Enter/空格/\\/g/e/a/d/q]："
                if mode == "view"
                else "命令 [Enter/空格/\\/x/a/d/q]："
                if mode == "delete"
                else "命令 [Enter/\\/g/d/q]："
            )
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key == " " and mode in {"view", "delete"} and entries:
            selected = entries[selected_index]
            if selected.location_kind != "custom":
                app._show_detail_panel(
                    "Skill 选择",
                    ["系统/运行时 Skills 不能在这里选择。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            _toggle_selected_skill(selected_skills, selected)
            continue

        detail_keys = ("d",) if mode in {"view", "delete"} else ()
        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries), detail_keys=detail_keys)
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode in {"view", "delete"}:
                app._show_detail_panel(
                    "Skill 详情",
                    app._local_skill_detail_lines(selected),
                    border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
                )
                continue
            return selected
        if transition.exit_requested:
            return None
        if transition.show_detail and entries:
            app._show_detail_panel(
                "Skill 详情",
                app._local_skill_detail_lines(entries[selected_index]),
                border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
            )
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title=title,
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 Skill 名称 / 相对目录 / 来源根 / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_skills.clear()
            continue
        if key_str == "g" and mode != "delete":
            include_system = not include_system
            selected_index = 0
            selected_skills.clear()
            continue
        if key_str == "e" and entries and mode == "view":
            selected_entries = _selected_or_current_skills(entries, selected_index, selected_skills)
            if not selected_entries:
                app._show_detail_panel(
                    "导出 Skill",
                    ["系统/运行时 Skills 只记录元数据，不作为 standalone Skills Bundle 导出。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            _run_skill_export(app, selected_entries)
            selected_skills.clear()
            continue
        if key_str == "x" and entries and mode == "delete":
            selected_entries = [
                entry
                for entry in entries
                if (entry.source_root, entry.relative_dir) in selected_skills
            ]
            if not selected_entries:
                selected = entries[selected_index]
                if selected.location_kind != "custom":
                    app._show_detail_panel(
                        "删除 Skill",
                        ["系统/运行时 Skills 不能在这里删除。"],
                        border_codes=(Ansi.DIM, Ansi.YELLOW),
                    )
                    continue
                selected_entries = [selected]
            _confirm_and_delete_skills(app, selected_entries)
            selected_index = 0
            selected_skills.clear()
            continue
        if key_str == "a" and mode in {"view", "delete"}:
            all_entries = _all_local_skill_entries_for_current_filter(
                app,
                filter_text=filter_text,
                include_system=include_system,
            )
            _select_matching_skills(app, selected_skills, all_entries)
            if all_entries:
                entries = all_entries
            continue


def _toggle_selected_skill(selected_skills: set[tuple[str, str]], skill: "LocalSkillSummary") -> None:
    key = (skill.source_root, skill.relative_dir)
    if key in selected_skills:
        selected_skills.remove(key)
    else:
        selected_skills.add(key)


def _selected_or_current_skills(
    entries: list["LocalSkillSummary"],
    selected_index: int,
    selected_skills: set[tuple[str, str]],
) -> list["LocalSkillSummary"]:
    selected_entries = [
        entry
        for entry in entries
        if entry.location_kind == "custom" and (entry.source_root, entry.relative_dir) in selected_skills
    ]
    if not selected_entries and entries:
        current = entries[selected_index]
        if current.location_kind == "custom":
            selected_entries = [current]
    return selected_entries


def _all_local_skill_entries_for_current_filter(
    app: "ToolkitTuiApp",
    *,
    filter_text: str,
    include_system: bool,
) -> list["LocalSkillSummary"]:
    try:
        return list_local_skills(
            app.paths,
            pattern=filter_text,
            include_system=include_system,
        )
    except ToolkitError as exc:
        app._show_detail_panel("读取本机 Skills 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
        return []


def _select_matching_skills(
    app: "ToolkitTuiApp",
    selected_skills: set[tuple[str, str]],
    entries: list["LocalSkillSummary"],
) -> None:
    custom_entries = [entry for entry in entries if entry.location_kind == "custom"]
    if not custom_entries:
        app._show_detail_panel(
            "Skill 选择",
            ["当前没有匹配的自定义 Skills。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    for entry in custom_entries:
        selected_skills.add((entry.source_root, entry.relative_dir))


def _run_skill_export(
    app: "ToolkitTuiApp",
    skills: list["LocalSkillSummary"],
) -> None:
    cli_args = ["export-skills", *[str(skill.skill_dir) for skill in skills]]
    count = len(skills)
    if count == 1:
        action_name = f"导出 Skill {skills[0].relative_dir}"
    else:
        action_name = f"导出 {count} 个 Skills"
    app._run_action(
        action_name,
        cli_args,
        dry_run=False,
        runner=lambda args=cli_args: app._run_toolkit(args),
        danger=False,
    )


def _confirm_and_delete_skills(app: "ToolkitTuiApp", skills: list["LocalSkillSummary"]) -> None:
    count = len(skills)
    cli_args = ["delete-skill"]
    for skill in skills:
        cli_args.append(str(skill.skill_dir))
    warning = (
        f"将删除本机 Skill：{skills[0].source_root}/{skills[0].relative_dir}。"
        if count == 1
        else f"将删除已勾选的 {count} 个本机 Skills。"
    )
    impact = str(skills[0].skill_dir) if count == 1 else f"{count} 个自定义 Skills"
    if not app._confirm_dangerous_action(
        cli_args,
        title="删除 Skill 确认",
        subtitle="该操作会删除本机自定义 Skill 目录。",
        warning=warning,
        impact=impact,
    ):
        return
    app._run_action(
        f"删除本机 Skill {skills[0].relative_dir}" if count == 1 else f"删除 {count} 个本机 Skills",
        cli_args,
        dry_run=False,
        runner=lambda args=cli_args: app._run_toolkit(args),
        danger=True,
    )


def open_skill_bundle_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SkillBundleSummary"]:
    filter_text = ""
    selected_index = 0
    selected_bundle_dirs: set[str] = set()
    pointer = glyphs().get("pointer", ">")

    while True:
        try:
            entries = list_skill_bundles(app.paths, pattern=filter_text)
        except ToolkitError as exc:
            app._show_detail_panel("读取 Skills Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
            return None
        visible_dir_keys = {_skill_bundle_dir_key(entry) for entry in entries}
        selected_bundle_dirs.intersection_update(visible_dir_keys)

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · 空格勾选 · Enter/d 详情 · / 搜索 · i 导入选中/当前 · a 选中全部 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "浏览 Skills Bundle" if mode == "view" else "选择要导入的 Skills Bundle"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
        ]
        if mode == "view":
            info_lines.append(f"{style_text('已勾选', Ansi.DIM)}   : {len(selected_bundle_dirs)}")
        info_lines.extend(app._github_sync_hint_lines())

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Skills Bundle。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                bundle = entries[idx]
                names = ", ".join(bundle.skills[:3])
                if len(bundle.skills) > 3:
                    names += f", ... +{len(bundle.skills) - 3}"
                marker = "[x]" if _skill_bundle_dir_key(bundle) in selected_bundle_dirs else "[ ]"
                line = (
                    f"{pointer if idx == selected_index else ' '} {marker if mode == 'view' else ''} "
                    f"{bundle.exported_at or '-'} | {bundle.source_machine or '-'} | "
                    f"{bundle.bundled_skill_count}/{bundle.skill_count} | {names or '（空）'}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.BRIGHT_BLUE))
                    list_lines.append(
                        "  "
                        + style_text(
                            ellipsize_middle(str(bundle.bundle_dir), max(10, box_width - 10)),
                            Ansi.DIM,
                        )
                    )
                else:
                    list_lines.append(line)

        render_browser_frame(
            app,
            title=title,
            subtitle=subtitle,
            info_lines=info_lines,
            list_lines=list_lines,
            list_border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
            box_width=box_width,
            center=center,
        )

        key = read_key()
        if key is None:
            raw_prompt = "命令 [Enter/空格/\\/i/a/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key == " " and entries and mode == "view":
            _toggle_selected_skill_bundle(selected_bundle_dirs, entries[selected_index])
            continue

        transition = apply_list_key(
            key,
            selected_index=selected_index,
            item_count=len(entries),
            detail_keys=("d",) if mode == "view" else (),
        )
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._show_detail_panel(
                    "Skills Bundle 详情",
                    app._skill_bundle_detail_lines(selected),
                    border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
                )
                continue
            return selected
        if transition.exit_requested:
            return None
        if transition.show_detail and entries:
            app._show_detail_panel(
                "Skills Bundle 详情",
                app._skill_bundle_detail_lines(entries[selected_index]),
                border_codes=(Ansi.DIM, Ansi.BRIGHT_BLUE),
            )
            continue

        key_str = transition.matched_hotkey
        if key_str in {"/", "f"}:
            new_filter = app._prompt_value(
                title=title,
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 Skill 名称 / 来源机器 / Bundle 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            selected_bundle_dirs.clear()
            continue
        if key_str == "i" and entries and mode == "view":
            selected_entries = _selected_or_current_skill_bundles(entries, selected_index, selected_bundle_dirs)
            _run_skill_bundle_import(app, selected_entries)
            selected_bundle_dirs.clear()
            continue
        if key_str == "a" and mode == "view":
            all_entries = _all_skill_bundle_entries_for_current_filter(app, filter_text=filter_text)
            _select_matching_skill_bundles(app, selected_bundle_dirs, all_entries)
            if all_entries:
                entries = all_entries
            continue


def _skill_bundle_dir_key(bundle: "SkillBundleSummary") -> str:
    try:
        return str(bundle.bundle_dir.resolve())
    except OSError:
        return str(bundle.bundle_dir.expanduser())


def _toggle_selected_skill_bundle(selected_bundle_dirs: set[str], bundle: "SkillBundleSummary") -> None:
    bundle_key = _skill_bundle_dir_key(bundle)
    if bundle_key in selected_bundle_dirs:
        selected_bundle_dirs.remove(bundle_key)
    else:
        selected_bundle_dirs.add(bundle_key)


def _selected_or_current_skill_bundles(
    entries: list["SkillBundleSummary"],
    selected_index: int,
    selected_bundle_dirs: set[str],
) -> list["SkillBundleSummary"]:
    selected_entries = [
        entry
        for entry in entries
        if _skill_bundle_dir_key(entry) in selected_bundle_dirs
    ]
    if not selected_entries and entries:
        selected_entries = [entries[selected_index]]
    return selected_entries


def _all_skill_bundle_entries_for_current_filter(
    app: "ToolkitTuiApp",
    *,
    filter_text: str,
) -> list["SkillBundleSummary"]:
    try:
        return list_skill_bundles(app.paths, pattern=filter_text)
    except ToolkitError as exc:
        app._show_detail_panel("读取 Skills Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
        return []


def _select_matching_skill_bundles(
    app: "ToolkitTuiApp",
    selected_bundle_dirs: set[str],
    entries: list["SkillBundleSummary"],
) -> None:
    if not entries:
        app._show_detail_panel(
            "Skills Bundle 选择",
            ["当前没有匹配的 Skills Bundle。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return
    for entry in entries:
        selected_bundle_dirs.add(_skill_bundle_dir_key(entry))


def _run_skill_bundle_import(
    app: "ToolkitTuiApp",
    bundles: list["SkillBundleSummary"],
) -> None:
    cli_args = ["import-skill-bundle", *[str(bundle.bundle_dir) for bundle in bundles]]
    count = len(bundles)
    if count == 1:
        action_name = f"导入 Skills Bundle {bundles[0].bundle_dir.name}"
    else:
        action_name = f"导入 {count} 个 Skills Bundle"
    app._run_action(
        action_name,
        cli_args,
        dry_run=False,
        runner=lambda args=cli_args: app._run_toolkit(args),
        danger=False,
    )
