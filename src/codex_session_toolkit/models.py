"""Shared data models and structured operation results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    scope: str
    path: Path
    preview: str
    kind: str
    cwd: str
    model_provider: str
    thread_name: str = ""


@dataclass(frozen=True)
class SessionBackupSummary:
    session_id: str
    scope: str
    backup_path: Path
    target_path: Path
    backup_kind: str
    backup_epoch: int
    backup_time_label: str
    size_bytes: int
    target_exists: bool
    preview: str
    kind: str
    cwd: str
    model_provider: str


@dataclass(frozen=True)
class BundleSummary:
    source_group: str
    session_id: str
    bundle_dir: Path
    relative_path: str
    updated_at: str
    exported_at: str
    thread_name: str
    session_cwd: str
    session_kind: str
    source_machine: str = ""
    source_machine_key: str = ""
    export_group: str = ""
    export_group_label: str = ""
    project_key: str = ""
    project_label: str = ""
    project_path: str = ""
    has_skills_manifest: bool = False
    bundled_skill_count: int = 0
    used_skill_count: int = 0


@dataclass(frozen=True)
class LocalSkillSummary:
    name: str
    source_root: str
    relative_dir: str
    skill_dir: Path
    location_kind: str
    content_hash: str = ""


@dataclass(frozen=True)
class SkillBundleSummary:
    bundle_dir: Path
    exported_at: str
    source_machine: str = ""
    source_machine_key: str = ""
    export_group: str = ""
    skill_count: int = 0
    bundled_skill_count: int = 0
    skills: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BundleValidationResult:
    source_group: str
    bundle_dir: Path
    session_id: str
    is_valid: bool
    message: str


@dataclass(frozen=True)
class CloneFileResult:
    action: str
    message: str
    new_file_path: Optional[Path] = None


@dataclass(frozen=True)
class CloneRunResult:
    provider: str
    dry_run: bool
    stats: Dict[str, int]
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CleanupResult:
    provider: str
    dry_run: bool
    files_checked: int
    files_to_delete: List[Path]
    deleted: List[Path] = field(default_factory=list)
    errors: List[Tuple[Path, str]] = field(default_factory=list)


@dataclass(frozen=True)
class OperationWarning:
    code: str
    session_id: str = ""
    path: str = ""
    related_path: str = ""
    detail: str = ""
    name: str = ""
    source_root: str = ""
    relative_dir: str = ""


@dataclass(frozen=True)
class ValidationReport:
    source_group: str
    results: List[BundleValidationResult]

    @property
    def valid_results(self) -> List[BundleValidationResult]:
        return [result for result in self.results if result.is_valid]

    @property
    def invalid_results(self) -> List[BundleValidationResult]:
        return [result for result in self.results if not result.is_valid]


@dataclass(frozen=True)
class ExportResult:
    session_id: str
    bundle_dir: Path
    relative_path: str
    session_kind: str
    session_cwd: str
    source_machine: str = ""
    source_machine_key: str = ""
    skills_bundled_count: int = 0
    skills_available_count: int = 0
    skills_manifest_path: Optional[Path] = None
    warnings: List[OperationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class SkillExportResult:
    bundle_dir: Path
    source_machine: str
    source_machine_key: str
    exported_count: int
    skipped_count: int = 0
    manifest_file: Optional[Path] = None
    warnings: List[OperationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class SkillImportResult:
    bundle_dir: Path
    restored_count: int = 0
    already_present_count: int = 0
    conflict_skipped_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    warnings: List[OperationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class SkillDeleteResult:
    name: str
    source_root: str
    relative_dir: str
    skill_dir: Path
    dry_run: bool
    deleted: bool = False


@dataclass(frozen=True)
class SessionBackupRestoreResult:
    session_id: str
    backup_path: Path
    target_path: Path
    dry_run: bool
    restored: bool = False
    current_backup_path: Optional[Path] = None


@dataclass(frozen=True)
class SessionBackupDeleteResult:
    session_id: str
    backup_path: Path
    target_path: Path
    dry_run: bool
    deleted: bool = False


@dataclass(frozen=True)
class BatchExportResult:
    summary_label: str
    bundle_root: Path
    export_root: Path
    machine_root: Path
    source_machine: str
    source_machine_key: str
    dry_run: bool
    active_only: bool
    session_kind: str
    session_ids: List[str]
    success_ids: List[str]
    failed_exports: List[Tuple[str, str]]
    manifest_file: Optional[Path] = None
    selection_label: str = ""
    selection_path: str = ""
    export_group: str = ""
    total_skills_bundled: int = 0
    warnings: List[OperationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class ImportResult:
    session_id: str
    bundle_dir: Path
    relative_path: str
    import_mode: str
    rollout_action: str
    session_kind: str
    session_cwd: str
    desktop_registered: bool
    desktop_registration_target: str
    thread_row_upserted: bool
    target_desktop_model_provider: str
    resolved_from_session_id: bool = False
    created_workspace_dir: bool = False
    backup_path: Optional[Path] = None
    warnings: List[OperationWarning] = field(default_factory=list)
    skills_restored_count: int = 0
    skills_already_present_count: int = 0
    skills_conflict_skipped_count: int = 0
    skills_missing_count: int = 0
    skills_failed_count: int = 0


@dataclass(frozen=True)
class BatchImportResult:
    bundle_root: Path
    desktop_visible: bool
    bundle_dirs: List[Path]
    success_dirs: List[Path]
    failed_imports: List[Tuple[Path, str]]
    machine_filter: str = ""
    machine_label: str = ""
    export_group_filter: str = ""
    export_group_label: str = ""
    latest_only: bool = False
    project_filter: str = ""
    project_label: str = ""
    project_source_path: str = ""
    target_project_path: str = ""
    total_skills_restored: int = 0
    total_skills_already_present: int = 0
    total_skills_conflict_skipped: int = 0
    total_skills_missing: int = 0
    total_skills_failed: int = 0
    skills_restore_report_path: Optional[Path] = None
    warnings: List[OperationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class RepairResult:
    provider: str
    dry_run: bool
    include_cli: bool
    include_archived: bool
    entries_scanned: int
    desktop_retagged: int
    cli_converted: int
    skipped_sessions: List[str]
    workspace_roots_count: int
    threads_updated: int
    threads_pruned: int
    backup_root: Optional[Path]
    changed_sessions: List[str]
    warnings: List[OperationWarning]
