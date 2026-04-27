"""Pure bundle-browser state helpers for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Sequence, Tuple

from ..models import BundleSummary
from ..stores.bundle_layout import EXPORT_GROUP_ORDER, bundle_export_group_label
from ..support import default_local_project_target, project_label_to_key


@dataclass(frozen=True)
class BundleFilterState:
    machine_options: List[Tuple[str, str]]
    export_group_options: List[Tuple[str, str]]
    normalized_machine_filter: str
    normalized_export_group_filter: str
    current_machine_label: str
    current_export_group_label: str


@dataclass(frozen=True)
class BundleMachineOptionState:
    machine_key: str
    machine_label: str
    bundle_count: int
    export_groups: Tuple[str, ...]


@dataclass(frozen=True)
class BundleCategoryOptionState:
    export_group: str
    export_group_label: str
    bundle_count: int
    entries: List[BundleSummary]


@dataclass(frozen=True)
class BundleProjectOptionState:
    project_key: str
    project_label: str
    project_path: str
    bundle_count: int
    entries: List[BundleSummary]
    local_status: str
    local_status_label: str
    local_target_path: str


def build_bundle_filter_state(
    entries: Sequence[BundleSummary],
    *,
    machine_filter: str,
    export_group_filter: str,
) -> BundleFilterState:
    machine_options = [("", "全部机器")]
    seen_machine_keys = {""}
    for bundle in entries:
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
            for bundle in entries
        ):
            export_group_options.append((export_group, bundle_export_group_label(export_group)))
            seen_export_groups.add(export_group)

    for bundle in entries:
        export_group = bundle.export_group or ""
        if not export_group or export_group in seen_export_groups:
            continue
        if normalized_machine_filter and bundle.source_machine_key != normalized_machine_filter:
            continue
        export_group_options.append((export_group, bundle.export_group_label or bundle_export_group_label(export_group)))
        seen_export_groups.add(export_group)

    normalized_export_group_filter = export_group_filter if export_group_filter in seen_export_groups else ""
    return BundleFilterState(
        machine_options=machine_options,
        export_group_options=export_group_options,
        normalized_machine_filter=normalized_machine_filter,
        normalized_export_group_filter=normalized_export_group_filter,
        current_machine_label=next(
            (label for key, label in machine_options if key == normalized_machine_filter),
            "全部机器",
        ),
        current_export_group_label=next(
            (label for key, label in export_group_options if key == normalized_export_group_filter),
            "全部导出方式",
        ),
    )


def build_machine_folder_options(entries: Sequence[BundleSummary]) -> List[BundleMachineOptionState]:
    grouped: dict[str, dict[str, object]] = {}
    for bundle in entries:
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
        BundleMachineOptionState(
            machine_key=machine_key,
            machine_label=str(payload["label"]),
            bundle_count=int(payload["count"]),
            export_groups=tuple(group for group in EXPORT_GROUP_ORDER if group in payload["groups"]),
        )
        for machine_key, payload in grouped.items()
    ]


def build_category_folder_options(entries: Sequence[BundleSummary]) -> List[BundleCategoryOptionState]:
    grouped: dict[str, List[BundleSummary]] = {}
    for bundle in entries:
        grouped.setdefault(bundle.export_group, []).append(bundle)

    ordered_groups = [group for group in EXPORT_GROUP_ORDER if group in grouped]
    ordered_groups.extend(group for group in grouped if group not in ordered_groups)
    return [
        BundleCategoryOptionState(
            export_group=export_group,
            export_group_label=bundle_export_group_label(export_group),
            bundle_count=len(grouped[export_group]),
            entries=grouped[export_group],
        )
        for export_group in ordered_groups
    ]


def build_project_folder_options(
    entries: Sequence[BundleSummary],
    *,
    local_target_resolver: Callable[[str, str], Tuple[str, str]] = default_local_project_target,
) -> List[BundleProjectOptionState]:
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
    project_options: List[BundleProjectOptionState] = []
    for project_key in ordered_keys:
        project_label = str(grouped[project_key]["label"])
        project_path = str(grouped[project_key]["path"])
        local_target_path, local_status = local_target_resolver(project_label, project_path)
        local_status_label = {
            "same_path": "原路径可用",
            "same_name": "同名项目可用",
        }.get(local_status, "本机未找到")
        project_options.append(
            BundleProjectOptionState(
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
