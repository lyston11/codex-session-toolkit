"""Bundle browser and import-selection helpers extracted from the TUI app shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

from ..models import BundleSummary
from ..stores.bundle_layout import EXPORT_GROUP_ORDER, bundle_export_group_label
from ..stores.bundle_scanner import collect_known_bundle_summaries, latest_distinct_bundle_summaries
from ..support import default_local_project_target, normalize_project_path, project_label_to_key
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
    machine_options = [("", "全部机器")]
    seen_machine_keys = {""}
    for bundle in all_entries:
        machine_key = bundle.source_machine_key or ""
        if machine_key in seen_machine_keys:
            continue
        machine_options.append((machine_key, bundle.source_machine or machine_key))
        seen_machine_keys.add(machine_key)

    normalized_machine_filter = machine_filter if machine_filter in seen_machine_keys else ""

    export_group_options = [("", "全部导出方式")]
    seen_export_groups = {""}
    for export_group in EXPORT_GROUP_ORDER:
        if export_group in seen_export_groups:
            continue
        if any(
            bundle.export_group == export_group
            and (not normalized_machine_filter or bundle.source_machine_key == normalized_machine_filter)
            for bundle in all_entries
        ):
            export_group_options.append((export_group, bundle_export_group_label(export_group)))
            seen_export_groups.add(export_group)
    for bundle in all_entries:
        export_group = bundle.export_group or ""
        if not export_group or export_group in seen_export_groups:
            continue
        if normalized_machine_filter and bundle.source_machine_key != normalized_machine_filter:
            continue
        export_group_options.append((export_group, bundle.export_group_label or bundle_export_group_label(export_group)))
        seen_export_groups.add(export_group)

    normalized_export_group_filter = export_group_filter if export_group_filter in seen_export_groups else ""
    entries = collect_known_bundle_summaries(
        app.paths,
        pattern=filter_text,
        limit=limit,
        source_group=source_group,
        machine_filter=normalized_machine_filter,
        export_group_filter=normalized_export_group_filter,
    )
    if latest_only:
        entries = latest_distinct_bundle_summaries(entries)

    return (
        BundleBrowserSnapshot(
            entries=entries,
            machine_options=machine_options,
            export_group_options=export_group_options,
            current_machine_label=next(
                (label for key, label in machine_options if key == normalized_machine_filter),
                "全部机器",
            ),
            current_export_group_label=next(
                (label for key, label in export_group_options if key == normalized_export_group_filter),
                "全部导出方式",
            ),
        ),
        normalized_machine_filter,
        normalized_export_group_filter,
    )


def bundle_machine_folder_options(app: "ToolkitTuiApp") -> List[object]:
    from .app import BundleMachineFolderOption

    summaries = collect_known_bundle_summaries(app.paths, pattern="", limit=None, source_group="all")
    grouped: dict[str, dict[str, object]] = {}
    for bundle in summaries:
        machine_key = bundle.source_machine_key or ""
        machine_label = bundle.source_machine or "旧布局"
        if machine_key not in grouped:
            grouped[machine_key] = {
                "label": machine_label,
                "count": 0,
                "groups": [],
            }
        grouped[machine_key]["count"] = int(grouped[machine_key]["count"]) + 1
        groups = grouped[machine_key]["groups"]
        if isinstance(groups, list) and bundle.export_group and bundle.export_group not in groups:
            groups.append(bundle.export_group)

    return [
        BundleMachineFolderOption(
            machine_key=machine_key,
            machine_label=str(payload["label"]),
            bundle_count=int(payload["count"]),
            export_groups=tuple(group for group in EXPORT_GROUP_ORDER if group in payload["groups"]),
        )
        for machine_key, payload in grouped.items()
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
    grouped: dict[str, List[BundleSummary]] = {}
    for bundle in summaries:
        grouped.setdefault(bundle.export_group, []).append(bundle)

    ordered_groups = [group for group in EXPORT_GROUP_ORDER if group in grouped]
    ordered_groups.extend(group for group in grouped if group not in ordered_groups)
    return [
        BundleCategoryFolderOption(
            export_group=export_group,
            export_group_label=bundle_export_group_label(export_group),
            bundle_count=len(grouped[export_group]),
            entries=grouped[export_group],
        )
        for export_group in ordered_groups
    ]


def bundle_project_folder_options(app: "ToolkitTuiApp", entries: List[BundleSummary]) -> List[object]:
    from .app import BundleProjectFolderOption

    grouped: dict[str, dict[str, object]] = {}
    for bundle in entries:
        project_key = bundle.project_key or project_label_to_key(bundle.project_label or bundle.bundle_dir.parents[1].name)
        if not project_key:
            continue
        if project_key not in grouped:
            grouped[project_key] = {
                "label": bundle.project_label or project_key,
                "path": bundle.project_path,
                "entries": [],
            }
        payload = grouped[project_key]
        if bundle.project_label and not payload["label"]:
            payload["label"] = bundle.project_label
        if bundle.project_path and not payload["path"]:
            payload["path"] = bundle.project_path
        project_entries = payload["entries"]
        if isinstance(project_entries, list):
            project_entries.append(bundle)

    ordered_keys = sorted(
        grouped,
        key=lambda key: (str(grouped[key]["label"]).lower(), key.lower()),
    )
    project_options: List[object] = []
    for project_key in ordered_keys:
        project_label = str(grouped[project_key]["label"])
        project_path = str(grouped[project_key]["path"])
        local_target_path, local_status = default_local_project_target(project_label, project_path)
        local_status_label = {
            "same_path": "原路径可用",
            "same_name": "同名项目可用",
        }.get(local_status, "本机未找到")
        project_options.append(
            BundleProjectFolderOption(
                project_key=project_key,
                project_label=project_label,
                project_path=project_path,
                bundle_count=len(grouped[project_key]["entries"]),
                entries=list(grouped[project_key]["entries"]),
                local_status=local_status,
                local_status_label=local_status_label,
                local_target_path=local_target_path,
            )
        )
    return project_options


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
        project_selected_index = max(0, min(project_selected_index, len(project_options) - 1)) if project_options else 0
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
            start = max(0, project_selected_index - 5)
            start = min(start, max(0, len(project_options) - 10))
            end = min(len(project_options), start + 10)
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

        if key in ("UP", "k", "K"):
            if project_options:
                project_selected_index = (project_selected_index - 1) % len(project_options)
            continue
        if key in ("DOWN", "j", "J"):
            if project_options:
                project_selected_index = (project_selected_index + 1) % len(project_options)
            continue

        key_str = str(key).strip().lower()
        if key == "ENTER":
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
        if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
            return None
        if key_str in {"d", " "} and project_options:
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
