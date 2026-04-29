"""Interactive browser flows extracted from the TUI app shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..errors import ToolkitError
from ..services.browse import get_project_session_summaries, get_session_summaries
from ..support import detect_machine_key, project_label_from_path, project_label_to_key
from .navigation_state import (
    apply_list_key,
    clamp_selected_index,
    cycle_option_key,
    selection_window,
)
from .terminal import Ansi, align_line, app_logo_lines, ellipsize_middle, glyphs, render_box, style_text
from .terminal_io import read_key

if TYPE_CHECKING:
    from ..models import BundleSummary, SessionSummary
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
    pointer = glyphs().get("pointer", ">")
    entries: list["SessionSummary"] = []
    needs_reload = True

    while True:
        project_label = project_label_from_path(project_path) or "root"
        project_key = project_label_to_key(project_label)
        export_root_preview = (
            f"{app.context.bundle_root_label}/{detect_machine_key()}/project/{project_key}/<timestamp>"
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
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = "↑/↓ 选择 · Enter 打开会话详情 · x 导出该项目全部会话 · / 搜索 · p 修改路径 · q 返回"

        info_lines = [
            f"{style_text('项目名', Ansi.DIM)} : {project_label}",
            f"{style_text('项目路径', Ansi.DIM)} : {project_path}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
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
                preview = summary.preview or summary.path.name
                line = (
                    f"{pointer if idx == selected_index else ' '} "
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
            raw = input("命令 [Enter/x/\\/p/q]：").strip()
            key = raw if raw else "ENTER"

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            app._session_action_center(entries[selected_index])
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
            needs_reload = True
            continue
        if key_str == "p":
            new_project_path = app._prompt_project_path(default=project_path)
            if not new_project_path:
                continue
            project_path = new_project_path
            filter_text = ""
            selected_index = 0
            needs_reload = True
            continue
        if key_str == "x":
            if not entries:
                app._show_detail_panel(
                    "项目会话导出",
                    ["当前项目路径下没有匹配会话，无法执行批量导出。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            dry_run = app._prompt_execution_mode(
                title=f"导出项目 {project_label} 下的全部会话",
                default_dry_run=False,
            )
            if dry_run is None:
                continue
            cli_args = ["export-project"]
            if dry_run:
                cli_args.append("--dry-run")
            cli_args.append(project_path)
            action_name = f"导出项目 {project_label} 下的 {len(entries)} 个会话为 Bundle"
            if dry_run:
                action_name += "（Dry-run）"
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda args=cli_args: app._run_toolkit(list(args)),
                danger=False,
            )
            continue


def open_session_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SessionSummary"]:
    filter_text = ""
    selected_index = 0
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
            needs_reload = False

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · Enter 打开导出面板 · / 搜索 · e 直接导出 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "浏览本机会话" if mode == "view" else "选择要导出的会话"

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('模式', Ansi.DIM)}   : {'浏览 / 直接操作' if mode == 'view' else '选择后导出'}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配会话。按 / 修改搜索词，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                summary = entries[idx]
                preview = summary.preview or summary.path.name
                line = (
                    f"{pointer if idx == selected_index else ' '} "
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
            raw_prompt = "命令 [Enter/\\/e/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._session_action_center(selected)
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
                title="浏览本机会话" if mode == "view" else "选择要导出的会话",
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 session_id / 标题 / provider / 路径 / cwd 搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            needs_reload = True
            continue
        if key_str == "e" and entries and mode == "view":
            selected = entries[selected_index]
            app._run_action(
                f"导出会话 {selected.session_id} 为 Bundle",
                ["export", selected.session_id],
                dry_run=False,
                runner=lambda sid=selected.session_id: app._run_toolkit(["export", sid]),
                danger=False,
            )
            continue


def open_bundle_browser(app: "ToolkitTuiApp", *, mode: str, source_group: str = "all") -> Optional["BundleSummary"]:
    filter_text = ""
    selected_index = 0
    export_group_filter = ""
    machine_filter = ""
    latest_only = False
    pointer = glyphs().get("pointer", ">")

    while True:
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

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · Enter 打开导入面板 · / 搜索 · s 切换导出方式 · m 切换机器 · "
            "l 切换历史视图 · i 导入 · v 自动建目录 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · s 切换导出方式 · m 切换机器 · "
            "l 切换历史视图 · d 查看详情 · q 返回"
        )
        title = "浏览 Bundle" if mode == "view" else "选择要导入的 Bundle"

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('导出方式', Ansi.DIM)} : {snapshot.current_export_group_label}",
            f"{style_text('导出机器', Ansi.DIM)} : {snapshot.current_machine_label}",
            f"{style_text('历史视图', Ansi.DIM)} : {'每台机器每个会话仅显示最新一份 Bundle' if latest_only else '显示全部历史 Bundle'}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Bundle。按 / 修改搜索词，按 s/m/l 切换视图，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                bundle = entries[idx]
                title_text = bundle.thread_name or "（无标题）"
                machine_label = bundle.source_machine or "旧布局"
                time_label = (bundle.exported_at or bundle.updated_at or "-")[:19]
                line = (
                    f"{pointer if idx == selected_index else ' '} "
                    f"{bundle.session_id} | {machine_label} | {bundle.export_group_label or '（未识别）'} | "
                    f"{time_label} | {title_text}"
                )
                if idx == selected_index:
                    list_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    detail_line = f"{bundle.session_kind or '-'} | {bundle.session_cwd or '（无工作目录）'}"
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
                "命令 [Enter/\\/s/m/l/i/v/d/q]："
                if mode == "view"
                else "命令 [Enter/\\/s/m/l/d/q]："
            )
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        transition = apply_list_key(key, selected_index=selected_index, item_count=len(entries))
        selected_index = transition.selected_index
        if transition.confirm_selected:
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._bundle_action_center(selected)
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
                title="浏览 Bundle" if mode == "view" else "选择要导入的 Bundle",
                prompt_label="输入搜索词",
                help_lines=[
                    "可按 session_id / 标题 / 导出方式 / 机器 / kind / cwd / 路径搜索。",
                    "留空表示不搜索。",
                ],
                allow_empty=True,
            )
            filter_text = new_filter or ""
            selected_index = 0
            continue
        if key_str == "s":
            export_group_filter = cycle_option_key(snapshot.export_group_options, export_group_filter)
            selected_index = 0
            continue
        if key_str == "m":
            machine_filter = cycle_option_key(snapshot.machine_options, machine_filter)
            selected_index = 0
            continue
        if key_str == "l":
            latest_only = not latest_only
            selected_index = 0
            continue
        if key_str == "i" and entries and mode == "view":
            bundle = entries[selected_index]
            app._run_action(
                f"导入 Bundle {bundle.session_id} 为会话",
                ["import", str(bundle.bundle_dir)],
                dry_run=False,
                runner=lambda path=str(bundle.bundle_dir): app._run_toolkit(["import", path]),
                danger=False,
            )
            continue
        if key_str == "v" and entries and mode == "view":
            bundle = entries[selected_index]
            app._run_action(
                f"导入 Bundle {bundle.session_id} 为会话（自动创建目录）",
                ["import", "--desktop-visible", str(bundle.bundle_dir)],
                dry_run=False,
                runner=lambda path=str(bundle.bundle_dir): app._run_toolkit(["import", "--desktop-visible", path]),
                danger=False,
            )
            continue
