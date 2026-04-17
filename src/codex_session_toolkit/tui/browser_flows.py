"""Interactive browser flows extracted from the TUI app shell."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..errors import ToolkitError
from ..services.browse import get_project_session_summaries, get_session_summaries
from ..support import detect_machine_key, project_label_from_path, project_label_to_key
from .terminal import Ansi, ellipsize_middle, glyphs, read_key, render_box, style_text

if TYPE_CHECKING:
    from ..models import BundleSummary, SessionSummary
    from .app import ToolkitTuiApp


def open_project_session_browser(app: "ToolkitTuiApp") -> None:
    project_path = app._prompt_project_path(default=str(Path.cwd()))
    if not project_path:
        return

    filter_text = ""
    selected_index = 0
    pointer = glyphs().get("pointer", ">")

    while True:
        project_label = project_label_from_path(project_path) or "root"
        project_key = project_label_to_key(project_label)
        export_root_preview = (
            f"{app.context.bundle_root_label}/{detect_machine_key()}/project/{project_key}/<timestamp>"
        )
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

        selected_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
        subtitle = "↑/↓ 选择 · Enter 打开会话详情 · x 导出该项目全部会话 · / 搜索 · p 修改路径 · q 返回"
        box_width = app._print_branded_header("按项目路径查看并导出会话", subtitle)

        info_lines = [
            f"{style_text('项目名', Ansi.DIM)} : {project_label}",
            f"{style_text('项目路径', Ansi.DIM)} : {project_path}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('导出目录', Ansi.DIM)} : {export_root_preview}",
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        list_lines: list[str] = []
        if not entries:
            list_lines.append("这个项目路径下没有匹配会话。按 p 重新输入路径，或按 q 返回。")
        else:
            start = max(0, selected_index - 5)
            start = min(start, max(0, len(entries) - 10))
            end = min(len(entries), start + 10)
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
        for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            print(line)

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/x/\\/p/q]：").strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            if entries:
                selected_index = (selected_index - 1) % len(entries)
            continue
        if key in ("DOWN", "j", "J"):
            if entries:
                selected_index = (selected_index + 1) % len(entries)
            continue

        if key == "ENTER":
            if not entries:
                continue
            app._session_action_center(entries[selected_index])
            continue

        key_str = str(key).strip().lower()
        if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
            return
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
            continue
        if key_str == "p":
            new_project_path = app._prompt_project_path(default=project_path)
            if not new_project_path:
                continue
            project_path = new_project_path
            filter_text = ""
            selected_index = 0
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
        if key_str in {"d", " "} and entries:
            app._show_detail_panel("会话详情", app._session_detail_lines(entries[selected_index]))


def open_session_browser(app: "ToolkitTuiApp", *, mode: str) -> Optional["SessionSummary"]:
    filter_text = ""
    selected_index = 0
    pointer = glyphs().get("pointer", ">")

    while True:
        try:
            entries = get_session_summaries(app.paths, pattern=filter_text, limit=200)
        except ToolkitError as exc:
            app._show_detail_panel("读取会话失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
            return None

        selected_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
        subtitle = (
            "↑/↓ 选择 · Enter 打开导出面板 · / 搜索 · e 直接导出 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · d 查看详情 · q 返回"
        )
        box_width = app._print_branded_header(
            "浏览本机会话" if mode == "view" else "选择要导出的会话",
            subtitle,
        )

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('模式', Ansi.DIM)}   : {'浏览 / 直接操作' if mode == 'view' else '选择后导出'}",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配会话。按 / 修改搜索词，或按 q 返回。")
        else:
            start = max(0, selected_index - 5)
            start = min(start, max(0, len(entries) - 10))
            end = min(len(entries), start + 10)
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
        for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.MAGENTA)):
            print(line)

        key = read_key()
        if key is None:
            raw_prompt = "命令 [Enter/\\/e/d/q]：" if mode == "view" else "命令 [Enter/\\/d/q]："
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            if entries:
                selected_index = (selected_index - 1) % len(entries)
            continue
        if key in ("DOWN", "j", "J"):
            if entries:
                selected_index = (selected_index + 1) % len(entries)
            continue

        if key == "ENTER":
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._session_action_center(selected)
                continue
            return selected

        key_str = str(key).strip().lower()
        if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
            return None
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
        if key_str in {"d", " "} and entries:
            selected = entries[selected_index]
            app._show_detail_panel("会话详情", app._session_detail_lines(selected))


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

        selected_index = max(0, min(selected_index, len(entries) - 1)) if entries else 0
        subtitle = (
            "↑/↓ 选择 · Enter 打开导入面板 · / 搜索 · s 切换导出方式 · m 切换机器 · "
            "l 切换历史视图 · i 导入 · v 自动建目录 · d 查看详情 · q 返回"
            if mode == "view"
            else "↑/↓ 选择 · Enter 确认 · / 搜索 · s 切换导出方式 · m 切换机器 · "
            "l 切换历史视图 · d 查看详情 · q 返回"
        )
        box_width = app._print_branded_header(
            "浏览 Bundle" if mode == "view" else "选择要导入的 Bundle",
            subtitle,
        )

        info_lines = [
            f"{style_text('搜索词', Ansi.DIM)} : {filter_text or '（无）'}",
            f"{style_text('匹配数量', Ansi.DIM)} : {len(entries)}",
            f"{style_text('导出方式', Ansi.DIM)} : {snapshot.current_export_group_label}",
            f"{style_text('导出机器', Ansi.DIM)} : {snapshot.current_machine_label}",
            f"{style_text('历史视图', Ansi.DIM)} : {'每台机器每个会话仅显示最新一份 Bundle' if latest_only else '显示全部历史 Bundle'}",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        list_lines: list[str] = []
        if not entries:
            list_lines.append("没有匹配 Bundle。按 / 修改搜索词，按 s/m/l 切换视图，或按 q 返回。")
        else:
            start = max(0, selected_index - 5)
            start = min(start, max(0, len(entries) - 10))
            end = min(len(entries), start + 10)
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
        for line in render_box(list_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
            print(line)

        key = read_key()
        if key is None:
            raw_prompt = (
                "命令 [Enter/\\/s/m/l/i/v/d/q]："
                if mode == "view"
                else "命令 [Enter/\\/s/m/l/d/q]："
            )
            raw = input(raw_prompt).strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            if entries:
                selected_index = (selected_index - 1) % len(entries)
            continue
        if key in ("DOWN", "j", "J"):
            if entries:
                selected_index = (selected_index + 1) % len(entries)
            continue

        if key == "ENTER":
            if not entries:
                continue
            selected = entries[selected_index]
            if mode == "view":
                app._bundle_action_center(selected)
                continue
            return selected

        key_str = str(key).strip().lower()
        if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
            return None
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
            current_index = 0
            for idx, (candidate_key, _) in enumerate(snapshot.export_group_options):
                if candidate_key == export_group_filter:
                    current_index = idx
                    break
            export_group_filter = snapshot.export_group_options[(current_index + 1) % len(snapshot.export_group_options)][0]
            selected_index = 0
            continue
        if key_str == "m":
            current_index = 0
            for idx, (candidate_key, _) in enumerate(snapshot.machine_options):
                if candidate_key == machine_filter:
                    current_index = idx
                    break
            machine_filter = snapshot.machine_options[(current_index + 1) % len(snapshot.machine_options)][0]
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
        if key_str in {"d", " "} and entries:
            bundle = entries[selected_index]
            app._show_detail_panel("Bundle 详情", app._bundle_detail_lines(bundle), border_codes=(Ansi.DIM, Ansi.GREEN))


def select_batch_bundle_import_scope(app: "ToolkitTuiApp"):
    from .app import BatchBundleImportSelection

    pointer = glyphs().get("pointer", ">")
    machine_selected_index = 0

    while True:
        try:
            machine_options = app._bundle_machine_folder_options()
        except ToolkitError as exc:
            app._show_detail_panel("读取 Bundle 失败", [str(exc)], border_codes=(Ansi.DIM, Ansi.RED))
            return None

        machine_selected_index = max(0, min(machine_selected_index, len(machine_options) - 1)) if machine_options else 0
        box_width = app._print_branded_header(
            "选择设备文件夹",
            "↑/↓ 选择设备 · Enter 进入该设备的分类文件夹 · d 查看摘要 · q 返回",
        )

        info_lines = [
            f"{style_text('导出根目录', Ansi.DIM)} : {app.context.bundle_root_label}",
            f"{style_text('设备数量', Ansi.DIM)}   : {len(machine_options)}",
            f"{style_text('下一步', Ansi.DIM)}   : 进入设备后选择 desktop / active / cli / project / single",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        machine_lines: list[str] = []
        if not machine_options:
            machine_lines.append("当前没有可用的设备文件夹。")
        else:
            start = max(0, machine_selected_index - 5)
            start = min(start, max(0, len(machine_options) - 10))
            end = min(len(machine_options), start + 10)
            for idx in range(start, end):
                option = machine_options[idx]
                export_groups = " / ".join(option.export_groups) or "（无分类）"
                line = (
                    f"{pointer if idx == machine_selected_index else ' '} "
                    f"{option.machine_label} | {option.bundle_count} 个 Bundle | {export_groups}"
                )
                if idx == machine_selected_index:
                    machine_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                else:
                    machine_lines.append(line)
        for line in render_box(machine_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
            print(line)

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/d/q]：").strip()
            key = raw if raw else "ENTER"

        if key in ("UP", "k", "K"):
            if machine_options:
                machine_selected_index = (machine_selected_index - 1) % len(machine_options)
            continue
        if key in ("DOWN", "j", "J"):
            if machine_options:
                machine_selected_index = (machine_selected_index + 1) % len(machine_options)
            continue

        key_str = str(key).strip().lower()
        if key == "ENTER":
            if not machine_options:
                continue
            selected_machine = machine_options[machine_selected_index]
        elif key_str in {"q", "quit", "esc", "0"} or key == "ESC":
            return None
        elif key_str in {"d", " "} and machine_options:
            selected_machine = machine_options[machine_selected_index]
            app._show_detail_panel(
                "设备文件夹摘要",
                [
                    f"{style_text('设备', Ansi.DIM)}     : {selected_machine.machine_label}",
                    f"{style_text('路径', Ansi.DIM)}     : {app.context.bundle_root_label}/{selected_machine.machine_key or selected_machine.machine_label}",
                    f"{style_text('分类', Ansi.DIM)}     : {' / '.join(selected_machine.export_groups) or '（无）'}",
                    f"{style_text('Bundle 数', Ansi.DIM)} : {selected_machine.bundle_count}",
                ],
                border_codes=(Ansi.DIM, Ansi.GREEN),
            )
            continue
        else:
            continue

        category_selected_index = 0
        while True:
            category_options = app._bundle_category_folder_options(selected_machine.machine_key)
            category_selected_index = max(0, min(category_selected_index, len(category_options) - 1)) if category_options else 0
            box_width = app._print_branded_header(
                "选择分类文件夹",
                "↑/↓ 选择分类 · Enter 导入该分类文件夹 · d 查看摘要 · q 返回上一步",
            )

            info_lines = [
                f"{style_text('当前设备', Ansi.DIM)} : {selected_machine.machine_label}",
                f"{style_text('分类数量', Ansi.DIM)} : {len(category_options)}",
                f"{style_text('导入方式', Ansi.DIM)} : 选中分类后直接导入；若为 project，会继续选择项目文件夹",
            ]
            for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
                print(line)
            print("")

            category_lines: list[str] = []
            if not category_options:
                category_lines.append("这个设备文件夹下没有可导入的分类。按 q 返回。")
            else:
                start = max(0, category_selected_index - 5)
                start = min(start, max(0, len(category_options) - 10))
                end = min(len(category_options), start + 10)
                for idx in range(start, end):
                    option = category_options[idx]
                    line = (
                        f"{pointer if idx == category_selected_index else ' '} "
                        f"{option.export_group_label} | {option.bundle_count} 个 Bundle"
                    )
                    if idx == category_selected_index:
                        category_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    else:
                        category_lines.append(line)
            for line in render_box(category_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
                print(line)

            key = read_key()
            if key is None:
                raw = input("命令 [Enter/d/q]：").strip()
                key = raw if raw else "ENTER"

            if key in ("UP", "k", "K"):
                if category_options:
                    category_selected_index = (category_selected_index - 1) % len(category_options)
                continue
            if key in ("DOWN", "j", "J"):
                if category_options:
                    category_selected_index = (category_selected_index + 1) % len(category_options)
                continue

            key_str = str(key).strip().lower()
            if key == "ENTER":
                if not category_options:
                    continue
                selected_category = category_options[category_selected_index]
                if selected_category.export_group == "project":
                    project_selection = app._select_project_bundle_import_scope(
                        selected_machine=selected_machine,
                        selected_category=selected_category,
                    )
                    if not project_selection:
                        continue
                    return project_selection
                return BatchBundleImportSelection(
                    entries=selected_category.entries,
                    machine_filter=selected_machine.machine_key,
                    machine_label=selected_machine.machine_label,
                    export_group_filter=selected_category.export_group,
                    export_group_label=selected_category.export_group_label,
                    latest_only=False,
                )
            if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
                break
            if key_str in {"d", " "} and category_options:
                selected_category = category_options[category_selected_index]
                app._show_detail_panel(
                    "分类文件夹摘要",
                    [
                        f"{style_text('设备', Ansi.DIM)}     : {selected_machine.machine_label}",
                        f"{style_text('分类', Ansi.DIM)}     : {selected_category.export_group_label}",
                        f"{style_text('Bundle 数', Ansi.DIM)} : {selected_category.bundle_count}",
                        f"{style_text('分类路径', Ansi.DIM)} : "
                        f"{(selected_category.entries[0].bundle_dir.parents[2] if selected_category.entries and selected_category.export_group == 'project' else selected_category.entries[0].bundle_dir.parents[1]) if selected_category.entries else '（空）'}",
                    ],
                    border_codes=(Ansi.DIM, Ansi.GREEN),
                )
