"""Bundle browser and import-selection helpers extracted from the TUI app shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

from ..errors import ToolkitError
from ..models import BundleSummary
from ..stores.bundle_scanner import collect_known_bundle_summaries, latest_distinct_bundle_summaries
from ..support import normalize_project_path
from .bundle_state import (
    build_bundle_filter_state,
    build_category_folder_options,
    build_machine_folder_options,
    build_project_folder_options,
)
from .navigation_state import apply_picker_key, clamp_selected_index, selection_window
from .terminal import Ansi, ellipsize_middle, glyphs, read_key, render_box, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp


def bundle_detail_lines(app: "ToolkitTuiApp", bundle: BundleSummary) -> List[str]:
    lines = [
        f"{style_text('Session ID', Ansi.DIM)} : {bundle.session_id}",
        f"{style_text('导出机器', Ansi.DIM)}  : {bundle.source_machine or '（旧布局）'}",
        f"{style_text('导出方式', Ansi.DIM)}  : {bundle.export_group_label or '（未识别）'}",
        f"{style_text('导出时间', Ansi.DIM)}  : {bundle.exported_at or '（空）'}",
        f"{style_text('Bundle 路径', Ansi.DIM)}: {bundle.bundle_dir}",
        f"{style_text('会话类型', Ansi.DIM)}  : {bundle.session_kind or '（空）'}",
        f"{style_text('工作目录', Ansi.DIM)}  : {bundle.session_cwd or '（空）'}",
        f"{style_text('标题', Ansi.DIM)}      : {bundle.thread_name or '（无标题）'}",
        f"{style_text('Rollout 路径', Ansi.DIM)} : {bundle.relative_path or '（空）'}",
    ]
    if bundle.project_label or bundle.project_key:
        lines.append(f"{style_text('项目文件夹', Ansi.DIM)} : {bundle.project_label or bundle.project_key}")
    if bundle.project_path:
        lines.append(f"{style_text('项目原路径', Ansi.DIM)} : {bundle.project_path}")
    if bundle.has_skills_manifest:
        lines.append(f"{style_text('Skills', Ansi.DIM)}       : 已打包 {bundle.bundled_skill_count} / 已使用 {bundle.used_skill_count}")
    return lines


def bundle_browser_snapshot(
    app: "ToolkitTuiApp",
    *,
    filter_text: str,
    machine_filter: str,
    export_group_filter: str,
    latest_only: bool,
    source_group: str = "all",
    limit: int = 240,
) -> Tuple[object, str, str]:
    from .app import BundleBrowserSnapshot

    all_entries = collect_known_bundle_summaries(
        app.paths,
        pattern="",
        limit=None,
        source_group=source_group,
    )
    filter_state = build_bundle_filter_state(
        all_entries,
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
    )
    entries = collect_known_bundle_summaries(
        app.paths,
        pattern=filter_text,
        limit=limit,
        source_group=source_group,
        machine_filter=filter_state.normalized_machine_filter,
        export_group_filter=filter_state.normalized_export_group_filter,
    )
    if latest_only:
        entries = latest_distinct_bundle_summaries(entries)

    return (
        BundleBrowserSnapshot(
            entries=entries,
            machine_options=filter_state.machine_options,
            export_group_options=filter_state.export_group_options,
            current_machine_label=filter_state.current_machine_label,
            current_export_group_label=filter_state.current_export_group_label,
        ),
        filter_state.normalized_machine_filter,
        filter_state.normalized_export_group_filter,
    )


def bundle_machine_folder_options(app: "ToolkitTuiApp") -> List[object]:
    from .app import BundleMachineFolderOption

    summaries = collect_known_bundle_summaries(app.paths, pattern="", limit=None, source_group="all")
    return [
        BundleMachineFolderOption(
            machine_key=option.machine_key,
            machine_label=option.machine_label,
            bundle_count=option.bundle_count,
            export_groups=option.export_groups,
        )
        for option in build_machine_folder_options(summaries)
    ]


def bundle_category_folder_options(app: "ToolkitTuiApp", machine_key: str) -> List[object]:
    from .app import BundleCategoryFolderOption

    summaries = collect_known_bundle_summaries(
        app.paths,
        pattern="",
        limit=None,
        source_group="all",
        machine_filter=machine_key,
    )
    return [
        BundleCategoryFolderOption(
            export_group=option.export_group,
            export_group_label=option.export_group_label,
            bundle_count=option.bundle_count,
            entries=option.entries,
        )
        for option in build_category_folder_options(summaries)
    ]


def bundle_project_folder_options(app: "ToolkitTuiApp", entries: List[BundleSummary]) -> List[object]:
    from .app import BundleProjectFolderOption

    return [
        BundleProjectFolderOption(
            project_key=option.project_key,
            project_label=option.project_label,
            project_path=option.project_path,
            bundle_count=option.bundle_count,
            entries=option.entries,
            local_status=option.local_status,
            local_status_label=option.local_status_label,
            local_target_path=option.local_target_path,
        )
        for option in build_project_folder_options(entries)
    ]


def default_target_project_path(app: "ToolkitTuiApp", project_option: object) -> str:
    return getattr(project_option, "local_target_path", "")


def select_project_bundle_import_scope(
    app: "ToolkitTuiApp",
    *,
    selected_machine: object,
    selected_category: object,
) -> Optional[object]:
    from .app import BatchBundleImportSelection

    pointer = glyphs().get("pointer", ">")
    project_selected_index = 0

    while True:
        project_options = app._bundle_project_folder_options(selected_category.entries)
        project_selected_index = clamp_selected_index(project_selected_index, len(project_options))
        box_width = app._print_branded_header(
            "选择项目文件夹",
            "↑/↓ 选择项目 · Enter 设置本机项目路径并导入 · d 查看摘要 · q 返回上一步",
        )

        info_lines = [
            f"{style_text('当前设备', Ansi.DIM)} : {selected_machine.machine_label}",
            f"{style_text('当前分类', Ansi.DIM)} : {selected_category.export_group_label}",
            f"{style_text('项目数量', Ansi.DIM)} : {len(project_options)}",
            f"{style_text('导入方式', Ansi.DIM)} : 先看本机匹配状态，再把会话 cwd 映射到目标项目路径",
        ]
        for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")

        project_lines: List[str] = []
        if not project_options:
            project_lines.append("这个设备的 project 分类下没有可导入的项目文件夹。按 q 返回。")
        else:
            start, end = selection_window(len(project_options), project_selected_index, 10)
            for idx in range(start, end):
                option = project_options[idx]
                line = (
                    f"{pointer if idx == project_selected_index else ' '} "
                    f"{option.project_label} | {option.bundle_count} 个 Bundle | {option.local_status_label}"
                )
                if idx == project_selected_index:
                    project_lines.append(style_text(line, Ansi.BOLD, Ansi.CYAN))
                    if option.project_path:
                        project_lines.append(
                            "  "
                            + style_text(
                                ellipsize_middle(option.project_path, max(10, box_width - 10)),
                                Ansi.DIM,
                            )
                        )
                    if option.local_target_path:
                        project_lines.append(
                            "  "
                            + style_text(
                                ellipsize_middle(
                                    f"默认导入到：{option.local_target_path}",
                                    max(10, box_width - 10),
                                ),
                                Ansi.DIM,
                            )
                        )
                else:
                    project_lines.append(line)
        for line in render_box(project_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.GREEN)):
            print(line)

        key = read_key()
        if key is None:
            raw = input("命令 [Enter/d/q]：").strip()
            key = raw if raw else "ENTER"

        transition = apply_picker_key(
            key,
            selected_index=project_selected_index,
            item_count=len(project_options),
        )
        project_selected_index = transition.selected_index

        if transition.confirm_selected:
            if not project_options:
                continue
            selected_project = project_options[project_selected_index]
            target_project_path = app._prompt_value(
                title=f"导入项目 {selected_project.project_label}",
                prompt_label="输入本机目标项目路径",
                help_lines=[
                    f"导出项目文件夹：{selected_project.project_label}",
                    f"原项目路径：{selected_project.project_path or '（未记录）'}",
                    f"本机匹配状态：{selected_project.local_status_label}",
                    f"默认目标路径：{selected_project.local_target_path or '（未设置）'}",
                    "导入时会把这个项目下所有会话的 cwd 映射到新的本机路径。",
                ],
                default=app._default_target_project_path(selected_project),
                allow_empty=False,
            )
            normalized_target_path = normalize_project_path(target_project_path or "")
            if not normalized_target_path:
                continue
            return BatchBundleImportSelection(
                entries=selected_project.entries,
                machine_filter=selected_machine.machine_key,
                machine_label=selected_machine.machine_label,
                export_group_filter=selected_category.export_group,
                export_group_label=selected_category.export_group_label,
                latest_only=False,
                project_filter=selected_project.project_key,
                project_label=selected_project.project_label,
                project_source_path=selected_project.project_path,
                target_project_path=normalized_target_path,
            )
        if transition.exit_requested:
            return None
        if transition.show_detail and project_options:
            selected_project = project_options[project_selected_index]
            app._show_detail_panel(
                "项目文件夹摘要",
                [
                    f"{style_text('设备', Ansi.DIM)}       : {selected_machine.machine_label}",
                    f"{style_text('分类', Ansi.DIM)}       : {selected_category.export_group_label}",
                    f"{style_text('项目文件夹', Ansi.DIM)} : {selected_project.project_label}",
                    f"{style_text('项目原路径', Ansi.DIM)} : {selected_project.project_path or '（未记录）'}",
                    f"{style_text('本机状态', Ansi.DIM)}   : {selected_project.local_status_label}",
                    f"{style_text('默认导入到', Ansi.DIM)} : {selected_project.local_target_path or '（未设置）'}",
                    f"{style_text('Bundle 数', Ansi.DIM)}  : {selected_project.bundle_count}",
                ],
                border_codes=(Ansi.DIM, Ansi.GREEN),
            )


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

        machine_selected_index = clamp_selected_index(machine_selected_index, len(machine_options))
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
            start, end = selection_window(len(machine_options), machine_selected_index, 10)
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

        transition = apply_picker_key(
            key,
            selected_index=machine_selected_index,
            item_count=len(machine_options),
        )
        machine_selected_index = transition.selected_index

        if transition.confirm_selected:
            if not machine_options:
                continue
            selected_machine = machine_options[machine_selected_index]
        elif transition.exit_requested:
            return None
        elif transition.show_detail and machine_options:
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
            category_selected_index = clamp_selected_index(category_selected_index, len(category_options))
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
                start, end = selection_window(len(category_options), category_selected_index, 10)
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

            transition = apply_picker_key(
                key,
                selected_index=category_selected_index,
                item_count=len(category_options),
            )
            category_selected_index = transition.selected_index

            if transition.confirm_selected:
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
            if transition.exit_requested:
                break
            if transition.show_detail and category_options:
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
