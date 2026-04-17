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
    ImportResult,
    RepairResult,
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
    print_import_result,
    print_repair_result,
    print_session_rows,
    print_validation_report,
)
from .services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles
from .services.clone import cleanup_clones, clone_to_provider
from .services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_project_sessions, export_session
from .services.importing import import_desktop_all, import_session
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
    "ImportResult",
    "RepairResult",
    "SessionSummary",
    "ToolkitError",
    "ValidationReport",
    "cleanup_clones",
    "clone_to_provider",
    "create_parser",
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
    "list_sessions",
    "main",
    "print_batch_export_result",
    "print_batch_import_result",
    "print_bundle_rows",
    "print_cleanup_result",
    "print_clone_file_result",
    "print_clone_run_result",
    "print_export_result",
    "print_import_result",
    "print_repair_result",
    "print_session_rows",
    "print_validation_report",
    "repair_desktop",
    "run_cli",
    "validate_bundles",
]
