"""Stable high-level public API for the toolkit."""

from __future__ import annotations

from .commands import create_parser, main, run_cli
from .errors import ToolkitError
from .models import (
    BatchExportResult,
    BatchImportResult,
    BundleSummary,
    BundleValidationResult,
    CleanupResult,
    CloneFileResult,
    CloneRunResult,
    ExportResult,
    GitHubConnectResult,
    GitHubPullResult,
    GitHubSyncResult,
    GitHubSyncStatus,
    ImportResult,
    RepairResult,
    SessionBackupDeleteResult,
    SessionBackupRestoreResult,
    SessionBackupSummary,
    SessionSummary,
    ValidationReport,
)
from .paths import CodexPaths
from .presenters.reports import (
    print_batch_export_result,
    print_batch_import_result,
    print_bundle_rows,
    print_cleanup_result,
    print_clone_file_result,
    print_clone_run_result,
    print_export_result,
    print_github_connect_result,
    print_github_pull_result,
    print_github_sync_result,
    print_import_result,
    print_repair_result,
    print_session_backup_delete_result,
    print_session_backup_restore_result,
    print_session_backup_rows,
    print_session_rows,
    print_validation_report,
)
from .services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles
from .services.backups import delete_session_backup, list_session_backups, restore_session_backup
from .services.clone import cleanup_clones, clone_to_provider
from .services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_project_sessions, export_session
from .services.importing import import_desktop_all, import_session
from .services.github_sync import connect_bundles_to_github, get_github_sync_status, pull_bundles_from_github, sync_bundles_to_github
from .services.provider import detect_provider
from .services.repair import repair_desktop


def list_sessions(paths: CodexPaths, *, pattern: str = "", limit: int = 30) -> int:
    return print_session_rows(get_session_summaries(paths, pattern=pattern, limit=max(1, limit)))


def list_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: int = 30,
    source_group: str = "all",
) -> int:
    return print_bundle_rows(
        get_bundle_summaries(
            paths,
            pattern=pattern,
            limit=max(1, limit),
            source_group=source_group,
        )
    )


def list_project_sessions(
    paths: CodexPaths,
    *,
    project_path: str,
    pattern: str = "",
    limit: int = 30,
) -> int:
    return print_session_rows(
        get_project_session_summaries(
            paths,
            project_path=project_path,
            pattern=pattern,
            limit=max(1, limit),
        )
    )


__all__ = [
    "BatchExportResult",
    "BatchImportResult",
    "BundleSummary",
    "BundleValidationResult",
    "CleanupResult",
    "CloneFileResult",
    "CloneRunResult",
    "CodexPaths",
    "ExportResult",
    "GitHubConnectResult",
    "GitHubPullResult",
    "GitHubSyncResult",
    "GitHubSyncStatus",
    "ImportResult",
    "RepairResult",
    "SessionBackupDeleteResult",
    "SessionBackupRestoreResult",
    "SessionBackupSummary",
    "SessionSummary",
    "ToolkitError",
    "ValidationReport",
    "cleanup_clones",
    "clone_to_provider",
    "create_parser",
    "delete_session_backup",
    "detect_provider",
    "export_active_desktop_all",
    "export_cli_all",
    "export_desktop_all",
    "export_project_sessions",
    "export_session",
    "get_bundle_summaries",
    "get_project_session_summaries",
    "get_session_summaries",
    "import_desktop_all",
    "import_session",
    "list_bundles",
    "list_project_sessions",
    "list_session_backups",
    "list_sessions",
    "main",
    "print_batch_export_result",
    "print_batch_import_result",
    "print_bundle_rows",
    "print_cleanup_result",
    "print_clone_file_result",
    "print_clone_run_result",
    "print_export_result",
    "print_github_connect_result",
    "print_github_pull_result",
    "print_github_sync_result",
    "print_import_result",
    "print_repair_result",
    "print_session_backup_delete_result",
    "print_session_backup_restore_result",
    "print_session_backup_rows",
    "print_session_rows",
    "print_validation_report",
    "repair_desktop",
    "restore_session_backup",
    "run_cli",
    "connect_bundles_to_github",
    "get_github_sync_status",
    "pull_bundles_from_github",
    "sync_bundles_to_github",
    "validate_bundles",
]
