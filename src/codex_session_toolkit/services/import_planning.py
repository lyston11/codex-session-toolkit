"""Batch import selection planning."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..errors import ToolkitError
from ..models import BundleSummary
from ..paths import CodexPaths
from ..stores.bundle_layout import LEGACY_MACHINE_KEY, bundle_export_group_label
from ..stores.bundle_scanner import (
    collect_bundle_summaries,
    collect_known_bundle_summaries,
    latest_distinct_bundle_summaries,
)
from ..stores.bundle_repository import resolve_bundle_dir, resolve_known_bundle_dir
from ..support import (
    normalize_bundle_root,
    normalize_project_path,
    project_filter_to_key,
    remap_session_cwd_to_project,
    restrict_to_local_bundle_workspace,
)
from ..validation import load_manifest


@dataclass(frozen=True)
class BatchImportPlan:
    bundle_root: Path
    bundle_summaries: list[BundleSummary]
    machine_filter: str
    machine_label: str
    export_group_filter: str
    export_group_label: str
    latest_only: bool
    project_filter: str
    project_label: str
    project_source_path: str
    target_project_path: str
    skills_restore_report_candidate_path: Optional[Path]

    @property
    def bundle_dirs(self) -> list[Path]:
        return [summary.bundle_dir for summary in self.bundle_summaries]

    def session_cwd_override_for(self, summary: BundleSummary) -> str:
        if not self.target_project_path or summary.export_group != "project":
            return ""
        return remap_session_cwd_to_project(
            summary.session_cwd,
            summary.project_path,
            self.target_project_path,
        )


def build_batch_import_plan(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path],
    machine_filter: str,
    export_group_filter: str,
    project_filter: str,
    target_project_path: str,
    latest_only: bool,
    skills_mode: str,
) -> BatchImportPlan:
    default_bundle_root = normalize_bundle_root(paths, None, paths.default_desktop_bundle_root)
    resolved_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_desktop_bundle_root)
    _validate_batch_bundle_root(paths, resolved_bundle_root, default_bundle_root)

    normalized_project_filter = project_filter_to_key(project_filter)
    normalized_target_project_path = normalize_project_path(target_project_path)
    resolved_export_group_filter = _resolve_export_group_filter(
        export_group_filter,
        project_filter=normalized_project_filter,
        target_project_path=normalized_target_project_path,
    )

    bundle_summaries = _collect_batch_import_summaries(
        paths,
        bundle_root=resolved_bundle_root,
        default_bundle_root=default_bundle_root,
        machine_filter=machine_filter,
        export_group_filter=resolved_export_group_filter,
    )
    if normalized_project_filter:
        bundle_summaries = [
            summary
            for summary in bundle_summaries
            if summary.project_key == normalized_project_filter
        ]
    if latest_only:
        bundle_summaries = latest_distinct_bundle_summaries(bundle_summaries)

    return BatchImportPlan(
        bundle_root=resolved_bundle_root,
        bundle_summaries=bundle_summaries,
        machine_filter=machine_filter,
        machine_label=_machine_label_for_filter(bundle_summaries, machine_filter),
        export_group_filter=resolved_export_group_filter,
        export_group_label=bundle_export_group_label(resolved_export_group_filter) if resolved_export_group_filter else "",
        latest_only=latest_only,
        project_filter=normalized_project_filter,
        project_label=_project_label_for_filter(bundle_summaries, normalized_project_filter),
        project_source_path=_project_source_path_for_filter(bundle_summaries, normalized_project_filter),
        target_project_path=normalized_target_project_path,
        skills_restore_report_candidate_path=_skills_restore_report_candidate_path(
            paths,
            resolved_bundle_root,
            skills_mode=skills_mode,
        ),
    )


def build_selected_import_plan(
    paths: CodexPaths,
    input_values: list[str] | tuple[str, ...],
    *,
    bundle_root: Optional[Path],
    source_group: str,
    machine_filter: str,
    export_group_filter: str,
    project_filter: str,
    target_project_path: str,
    latest_only: bool,
    skills_mode: str,
) -> BatchImportPlan:
    if not input_values:
        raise ToolkitError("Bundle input is required.")

    default_bundle_root = normalize_bundle_root(paths, None, paths.default_desktop_bundle_root)
    resolved_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_desktop_bundle_root)
    _validate_batch_bundle_root(paths, resolved_bundle_root, default_bundle_root)

    normalized_project_filter = project_filter_to_key(project_filter)
    normalized_target_project_path = normalize_project_path(target_project_path)
    resolved_export_group_filter = _resolve_export_group_filter(
        export_group_filter,
        project_filter=normalized_project_filter,
        target_project_path=normalized_target_project_path,
    )

    available_summaries = _collect_batch_import_summaries(
        paths,
        bundle_root=resolved_bundle_root,
        default_bundle_root=default_bundle_root,
        machine_filter=machine_filter,
        export_group_filter=resolved_export_group_filter,
    )
    if normalized_project_filter:
        available_summaries = [
            summary
            for summary in available_summaries
            if summary.project_key == normalized_project_filter
        ]

    summaries_by_dir = {
        _bundle_dir_key(summary.bundle_dir): summary
        for summary in available_summaries
    }
    selected_summaries: list[BundleSummary] = []
    seen_dirs: set[str] = set()
    for input_value in input_values:
        bundle_dir = _resolve_selected_bundle_dir(
            paths,
            input_value,
            bundle_root=resolved_bundle_root,
            default_bundle_root=default_bundle_root,
            source_group=source_group,
            machine_filter=machine_filter,
            export_group_filter=resolved_export_group_filter,
        )
        dir_key = _bundle_dir_key(bundle_dir)
        if dir_key in seen_dirs:
            continue
        summary = summaries_by_dir.get(dir_key) or _fallback_bundle_summary(bundle_dir, source_group=source_group)
        if normalized_project_filter and summary.project_key != normalized_project_filter:
            continue
        selected_summaries.append(summary)
        seen_dirs.add(dir_key)

    if latest_only:
        selected_summaries = latest_distinct_bundle_summaries(selected_summaries)

    return BatchImportPlan(
        bundle_root=resolved_bundle_root,
        bundle_summaries=selected_summaries,
        machine_filter=machine_filter,
        machine_label=_machine_label_for_filter(selected_summaries, machine_filter),
        export_group_filter=resolved_export_group_filter,
        export_group_label=bundle_export_group_label(resolved_export_group_filter) if resolved_export_group_filter else "",
        latest_only=latest_only,
        project_filter=normalized_project_filter,
        project_label=_project_label_for_filter(selected_summaries, normalized_project_filter),
        project_source_path=_project_source_path_for_filter(selected_summaries, normalized_project_filter),
        target_project_path=normalized_target_project_path,
        skills_restore_report_candidate_path=_skills_restore_report_candidate_path(
            paths,
            resolved_bundle_root,
            skills_mode=skills_mode,
        ),
    )


def _validate_batch_bundle_root(paths: CodexPaths, bundle_root: Path, default_bundle_root: Path) -> None:
    if bundle_root.is_dir():
        return
    legacy_roots = (
        paths.legacy_session_bundle_root,
        paths.legacy_bundle_root,
        paths.legacy_desktop_bundle_root,
    )
    if bundle_root != default_bundle_root or not any(Path(root).expanduser().is_dir() for root in legacy_roots):
        raise ToolkitError(f"Missing bundle root: {bundle_root}")


def _resolve_export_group_filter(
    export_group_filter: str,
    *,
    project_filter: str,
    target_project_path: str,
) -> str:
    resolved_export_group_filter = export_group_filter
    if project_filter:
        if resolved_export_group_filter and resolved_export_group_filter != "project":
            raise ToolkitError("Project filter can only be used with project bundle imports.")
        resolved_export_group_filter = "project"
    if target_project_path and not project_filter:
        raise ToolkitError("Target project path requires a project filter.")
    return resolved_export_group_filter


def _collect_batch_import_summaries(
    paths: CodexPaths,
    *,
    bundle_root: Path,
    default_bundle_root: Path,
    machine_filter: str,
    export_group_filter: str,
) -> list[BundleSummary]:
    if bundle_root == default_bundle_root:
        return collect_known_bundle_summaries(
            paths,
            source_group="all",
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
            limit=None,
        )
    return collect_bundle_summaries(
        bundle_root,
        source_group="all",
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
        limit=None,
    )


def _resolve_selected_bundle_dir(
    paths: CodexPaths,
    input_value: str,
    *,
    bundle_root: Path,
    default_bundle_root: Path,
    source_group: str,
    machine_filter: str,
    export_group_filter: str,
) -> Path:
    input_path = Path(input_value).expanduser()
    if input_path.is_dir():
        return restrict_to_local_bundle_workspace(paths, input_path, "Bundle directory")
    if bundle_root != default_bundle_root:
        return resolve_bundle_dir(bundle_root, input_value)
    return resolve_known_bundle_dir(
        paths,
        input_value,
        source_group=source_group,
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
    )


def _bundle_dir_key(bundle_dir: Path) -> str:
    try:
        return str(bundle_dir.resolve())
    except OSError:
        return str(bundle_dir.expanduser())


def _fallback_bundle_summary(bundle_dir: Path, *, source_group: str) -> BundleSummary:
    manifest = load_manifest(bundle_dir / "manifest.env")
    return BundleSummary(
        source_group=source_group,
        session_id=manifest.get("SESSION_ID", ""),
        bundle_dir=bundle_dir,
        relative_path=manifest.get("RELATIVE_PATH", ""),
        updated_at=manifest.get("UPDATED_AT", ""),
        exported_at=manifest.get("EXPORTED_AT", ""),
        thread_name=manifest.get("THREAD_NAME", ""),
        session_cwd=manifest.get("SESSION_CWD", ""),
        session_kind=manifest.get("SESSION_KIND", ""),
    )


def _machine_label_for_filter(bundle_summaries: list[BundleSummary], machine_filter: str) -> str:
    if not machine_filter:
        return ""
    matching_machine = next(
        (
            summary.source_machine
            for summary in bundle_summaries
            if (summary.source_machine_key or LEGACY_MACHINE_KEY) == machine_filter
        ),
        "",
    )
    return matching_machine or machine_filter


def _project_label_for_filter(bundle_summaries: list[BundleSummary], project_filter: str) -> str:
    if not project_filter:
        return ""
    for summary in bundle_summaries:
        if summary.project_key == project_filter:
            return summary.project_label or summary.project_key
    return ""


def _project_source_path_for_filter(bundle_summaries: list[BundleSummary], project_filter: str) -> str:
    if not project_filter:
        return ""
    for summary in bundle_summaries:
        if summary.project_key == project_filter:
            return summary.project_path
    return ""


def _skills_restore_report_candidate_path(
    paths: CodexPaths,
    bundle_root: Path,
    *,
    skills_mode: str,
) -> Optional[Path]:
    if skills_mode == "skip":
        return None
    report_root = bundle_root if bundle_root.is_dir() else paths.legacy_session_bundle_root
    return report_root / f"_skills_restore_report.{int(time.time())}.{uuid4().hex}.json"
