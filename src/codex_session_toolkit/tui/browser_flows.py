"""Interactive browser flows extracted from the TUI app shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..errors import ToolkitError
from ..services.backups import list_session_backups
from ..services.browse import get_project_session_summaries, get_session_summaries
from ..services.skills_transfer import list_local_skills, list_skill_bundles
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
    from ..models import BundleSummary, LocalSkillSummary, SessionBackupSummary, SessionSummary, SkillBundleSummary
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
                preview = summary.thread_name or summary.preview or summary.path.name
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
                preview = summary.thread_name or summary.preview or summary.path.name
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
            "↑/↓ 选择 · Enter 查看详情 · / 搜索 · r 恢复选中备份 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "浏览/恢复会话备份" if mode == "view" else "选择会话备份"
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
            raw_prompt = "命令 [Enter/\\/r/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
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

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · Enter 查看详情 · / 搜索 · g 切换系统 Skills · e 导出选中 · r 删除选中 · x 导出全部 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · g 切换系统 Skills · d 查看详情 · q 返回"
        )
        title = "浏览本机 Skills" if mode == "view" else "选择要导出的 Skill"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('显示范围', Ansi.DIM)} : {'自定义 + 系统/运行时 Skills' if include_system else '仅自定义 Skills'}",
        ]

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Skills。按 / 修改搜索词，按 g 切换显示范围，或按 q 返回。")
        else:
            start, end = selection_window(len(entries), selected_index, 10)
            for idx in range(start, end):
                skill = entries[idx]
                line = (
                    f"{pointer if idx == selected_index else ' '} "
                    f"{skill.name} | {skill.source_root}/{skill.location_kind} | {skill.relative_dir}"
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
            raw_prompt = "命令 [Enter/\\/g/e/r/x/d/q]：" if mode == "view" else "命令 [Enter/\\/g/d/q]："
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
            continue
        if key_str == "g":
            include_system = not include_system
            selected_index = 0
            continue
        if key_str == "e" and entries and mode == "view":
            selected = entries[selected_index]
            if selected.location_kind != "custom":
                app._show_detail_panel(
                    "导出 Skill",
                    ["系统/运行时 Skills 只记录元数据，不作为 standalone Skills Bundle 导出。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            app._run_action(
                f"导出 Skill {selected.relative_dir}",
                ["export-skills", selected.relative_dir],
                dry_run=False,
                runner=lambda pattern=selected.relative_dir: app._run_toolkit(["export-skills", pattern]),
                danger=False,
            )
            continue
        if key_str == "r" and entries and mode == "view":
            selected = entries[selected_index]
            if selected.location_kind != "custom":
                app._show_detail_panel(
                    "删除 Skill",
                    ["系统/运行时 Skills 不能在这里删除。"],
                    border_codes=(Ansi.DIM, Ansi.YELLOW),
                )
                continue
            cli_args = ["delete-skill", selected.relative_dir, "--source-root", selected.source_root]
            if not app._confirm_dangerous_action(
                cli_args,
                warning=f"将删除本机 Skill：{selected.source_root}/{selected.relative_dir}。",
                impact=str(selected.skill_dir),
            ):
                continue
            app._run_action(
                f"删除本机 Skill {selected.relative_dir}",
                cli_args,
                dry_run=False,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=True,
            )
            selected_index = 0
            continue
        if key_str == "x" and mode == "view":
            app._run_action(
                "导出全部自定义 Skills",
                ["export-skills"],
                dry_run=False,
                runner=lambda: app._run_toolkit(["export-skills"]),
                danger=False,
            )
            continue


def open_skill_bundle_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SkillBundleSummary"]:
    filter_text = ""
    selected_index = 0
    pointer = glyphs().get("pointer", ">")

    while True:
        try:
            entries = list_skill_bundles(app.paths, pattern=filter_text)
        except ToolkitError as exc:
            app._show_detail_panel("读取 Skills Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
            return None

        selected_index = clamp_selected_index(selected_index, len(entries))
        box_width, center = app._screen_layout()
        subtitle = (
            "↑/↓ 选择 · Enter 查看详情 · / 搜索 · i 导入选中 · a 导入全部 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        title = "浏览 Skills Bundle" if mode == "view" else "选择要导入的 Skills Bundle"
        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
        ]

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
                line = (
                    f"{pointer if idx == selected_index else ' '} "
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
            raw_prompt = "命令 [Enter/\\/i/a/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
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
            continue
        if key_str == "i" and entries and mode == "view":
            selected = entries[selected_index]
            app._run_action(
                f"导入 Skills Bundle {selected.bundle_dir.name}",
                ["import-skill-bundle", str(selected.bundle_dir)],
                dry_run=False,
                runner=lambda path=str(selected.bundle_dir): app._run_toolkit(["import-skill-bundle", path]),
                danger=False,
            )
            continue
        if key_str == "a" and mode == "view":
            app._run_action(
                "批量导入 Skills Bundle",
                ["import-skill-bundles"],
                dry_run=False,
                runner=lambda: app._run_toolkit(["import-skill-bundles"]),
                danger=False,
            )
            continue
