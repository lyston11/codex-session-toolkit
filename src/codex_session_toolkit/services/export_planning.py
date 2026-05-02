"""Batch export planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import BatchExportResult, OperationWarning
from ..paths import CodexPaths
from ..stores.session_files import collect_session_ids_for_kind, collect_session_ids_for_project
from ..support import (
    build_batch_export_root,
    build_machine_bundle_root,
    build_project_export_root,
    detect_machine_key,
    detect_machine_label,
    normalize_bundle_root,
    normalize_project_path,
    project_label_from_path,
    project_label_to_key,
)


@dataclass(frozen=True)
class BatchExportPlan:
    summary_label: str
    bundle_root: Path
    export_root: Path
    machine_root: Path
    source_machine: str
    source_machine_key: str
    dry_run: bool
    active_only: bool
    session_kind: str
    session_ids: list[str]
    manifest_name: str
    manifest_metadata: dict[str, object]
    export_group: str
    selection_label: str = ""
    selection_path: str = ""

    @property
    def manifest_file(self) -> Path:
        return self.export_root / self.manifest_name

    def manifest_metadata_for_successes(self, success_count: int) -> dict[str, object]:
        return {
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **self.manifest_metadata,
            "count": success_count,
        }

    def result(
        self,
        *,
        success_ids: list[str] | None = None,
        failed_exports: list[tuple[str, str]] | None = None,
        manifest_file: Optional[Path] = None,
        total_skills_bundled: int = 0,
        warnings: list[OperationWarning] | None = None,
    ) -> BatchExportResult:
        return BatchExportResult(
            summary_label=self.summary_label,
            bundle_root=self.bundle_root,
            export_root=self.export_root,
            machine_root=self.machine_root,
            source_machine=self.source_machine,
            source_machine_key=self.source_machine_key,
            dry_run=self.dry_run,
            active_only=self.active_only,
            session_kind=self.session_kind,
            session_ids=self.session_ids,
            success_ids=success_ids or [],
            failed_exports=failed_exports or [],
            manifest_file=manifest_file,
            selection_label=self.selection_label,
            selection_path=self.selection_path,
            export_group=self.export_group,
            total_skills_bundled=total_skills_bundled,
            warnings=warnings or [],
        )


def build_session_kind_export_plan(
    paths: CodexPaths,
    *,
    session_kind: str,
    bundle_root: Path,
    dry_run: bool,
    active_only: bool,
    manifest_stem: str,
    summary_label: str,
    archive_group: str,
) -> BatchExportPlan:
    machine_key = detect_machine_key()
    return BatchExportPlan(
        summary_label=summary_label,
        bundle_root=bundle_root,
        export_root=build_batch_export_root(bundle_root, archive_group, machine_key),
        machine_root=build_machine_bundle_root(bundle_root, machine_key),
        source_machine=detect_machine_label(),
        source_machine_key=machine_key,
        dry_run=dry_run,
        active_only=active_only,
        session_kind=session_kind,
        session_ids=collect_session_ids_for_kind(paths, session_kind=session_kind, active_only=active_only),
        manifest_name=f"_{manifest_stem}_export_manifest.txt",
        manifest_metadata={
            "session_kind": session_kind,
            "active_only": 1 if active_only else 0,
        },
        export_group=archive_group,
    )


def build_project_export_plan(
    paths: CodexPaths,
    project_path: str,
    *,
    bundle_root: Optional[Path],
    dry_run: bool,
    active_only: bool,
) -> BatchExportPlan:
    normalized_project_path = normalize_project_path(project_path)
    if not normalized_project_path:
        raise ToolkitError("Project path cannot be empty.")

    project_label = project_label_from_path(normalized_project_path) or "root"
    project_key = project_label_to_key(project_label)
    resolved_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    machine_key = detect_machine_key()
    return BatchExportPlan(
        summary_label=f"项目 {project_label}",
        bundle_root=resolved_bundle_root,
        export_root=build_project_export_root(resolved_bundle_root, project_key, machine_key),
        machine_root=build_machine_bundle_root(resolved_bundle_root, machine_key),
        source_machine=detect_machine_label(),
        source_machine_key=machine_key,
        dry_run=dry_run,
        active_only=active_only,
        session_kind="project",
        session_ids=collect_session_ids_for_project(
            paths,
            project_path=normalized_project_path,
            active_only=active_only,
        ),
        manifest_name="_project_export_manifest.txt",
        manifest_metadata={
            "project_label": project_label,
            "project_path": normalized_project_path,
            "active_only": 1 if active_only else 0,
        },
        export_group="project",
        selection_label=project_label,
        selection_path=normalized_project_path,
    )
