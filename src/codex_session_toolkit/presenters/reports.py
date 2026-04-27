"""CLI presentation helpers for structured service results."""

from __future__ import annotations

import sys

from ..models import (
    BatchExportResult,
    BatchImportResult,
    BundleSummary,
    CleanupResult,
    CloneFileResult,
    CloneRunResult,
    ExportResult,
    ImportResult,
    OperationWarning,
    RepairResult,
    SessionSummary,
    ValidationReport,
)


def print_session_rows(rows: list[SessionSummary]) -> int:
    if not rows:
        print("No matching sessions found.")
        return 0

    for summary in rows:
        print(
            f"{summary.session_id} | {summary.kind} | {summary.scope} | "
            f"{summary.model_provider or '-'} | {summary.path} | {summary.preview[:80]}"
        )
    return 0


def print_bundle_rows(rows: list[BundleSummary]) -> int:
    if not rows:
        print("No matching bundles found.")
        return 0

    for bundle in rows:
        updated = bundle.updated_at or bundle.exported_at or "-"
        title = bundle.thread_name or "（无标题）"
        print(
            f"{bundle.session_id} | {bundle.export_group_label or bundle.export_group or '-'} | {bundle.source_machine or '-'} | {bundle.session_kind or '-'} | "
            f"{updated} | {bundle.bundle_dir} | {title[:80]}"
        )
    return 0


def _format_operation_warning(warning: OperationWarning) -> str:
    if warning.code == "local_newer_preserved":
        return "Warning: local session is newer than imported bundle; preserved local rollout and merged history only."
    if warning.code == "missing_workspace_directory":
        return f"Warning: missing workspace directory: {warning.path}"
    if warning.code == "workspace_parent_used":
        return (
            "Warning: exact workspace directory is missing, using existing parent for Desktop registration: "
            f"{warning.related_path}"
        )
    if warning.code == "invalid_skills_manifest":
        return f"Warning: invalid skills manifest: {warning.path}"
    if warning.code == "missing_skill":
        return f"Missing skill: {warning.name} ({warning.source_root}/{warning.relative_dir})"
    if warning.code == "restore_skills_failed":
        return f"Warning: failed to restore skills from {warning.path}: {warning.detail}"
    if warning.code == "skipped_invalid_session_file":
        return f"Skipped invalid session file: {warning.detail}"
    if warning.code == "skipped_session_without_id":
        return f"Skipped session without payload.id: {warning.path}"
    return warning.detail or warning.code


def print_validation_report(report: ValidationReport, *, verbose: bool = False) -> int:
    print(f"Bundle source filter: {report.source_group}")
    print(f"Bundle directories scanned: {len(report.results)}")
    print(f"Valid bundles: {len(report.valid_results)}")
    print(f"Invalid bundles: {len(report.invalid_results)}")
    sys.stdout.flush()

    if verbose:
        for result in report.valid_results:
            print(f"[OK] [{result.source_group}] {result.session_id} | {result.bundle_dir}")

    if report.invalid_results:
        print("Bundle validation completed with failures.", file=sys.stderr)
        print("Invalid bundle directories:", file=sys.stderr)
        for result in report.invalid_results:
            print(f"[{result.source_group}] {result.bundle_dir}", file=sys.stderr)
            print(f"  session_id: {result.session_id}", file=sys.stderr)
            print(f"  reason: {result.message}", file=sys.stderr)
        return 1
    return 0


def print_clone_file_result(result: CloneFileResult) -> int:
    print(result.message)
    return 0 if result.action != "error" else 1


def print_clone_run_result(result: CloneRunResult) -> int:
    print("\nScanning candidates...")
    for message in result.messages:
        print(f"[+] {message}")
    for message in result.errors:
        print(f"[!] {message}", file=sys.stderr)

    print("\n==============================")
    print("Summary:")
    print(f"  Target Provider: {result.provider}")
    print(f"  Cloned (New):    {result.stats.get('cloned', 0)}")
    print(f"  Skipped (Target):{result.stats.get('skipped_target', 0)} (already on target provider)")
    print(f"  Skipped (Done):  {result.stats.get('skipped_exists', 0)} (already cloned earlier)")
    print(f"  Errors:          {result.stats.get('error', 0)}")
    print("==============================")

    if result.dry_run:
        print("\nThis was a DRY RUN. No files were created.")
    return 0


def print_cleanup_result(result: CleanupResult) -> int:
    print("Scanning for unmarked clones to clean up...")
    print(f"Scanned {result.files_checked} files. Found {len(result.files_to_delete)} unmarked clones.")

    if result.dry_run:
        for target_path in result.files_to_delete:
            print(f"[DRY-RUN] Would delete: {target_path}")
    else:
        for target_path in result.deleted:
            print(f"[Deleted] {target_path}")
        for target_path, reason in result.errors:
            print(f"[Error] Deleting {target_path}: {reason}", file=sys.stderr)

    print("\nCleanup scan complete.")
    return 1 if result.errors else 0


def print_export_result(result: ExportResult) -> int:
    for warning in result.warnings:
        print(warning, file=sys.stderr)
    print(f"Exported {result.session_id}")
    print(f"Source machine: {result.source_machine or result.source_machine_key or '-'}")
    print(f"Bundle: {result.bundle_dir}")
    print(f"Session file: {result.relative_path}")
    print(f"Session kind: {result.session_kind or 'unknown'}")
    print(f"Session cwd: {result.session_cwd or 'unknown'}")
    if result.skills_available_count > 0:
        print(f"Skills available: {result.skills_available_count}")
        print(f"Skills bundled:   {result.skills_bundled_count}")
    if result.skills_manifest_path:
        print(f"Skills manifest:  {result.skills_manifest_path}")
    return 0


def print_batch_export_result(result: BatchExportResult) -> int:
    for warning in result.warnings:
        print(warning, file=sys.stderr)
    print(f"Bundle root: {result.bundle_root}")
    print(f"Machine folder: {result.machine_root}")
    print(f"Source machine: {result.source_machine or result.source_machine_key}")
    if result.export_group:
        print(f"Export group: {result.export_group}")
    if result.selection_label:
        print(f"Selection: {result.selection_label}")
    if result.selection_path:
        print(f"Selection path: {result.selection_path}")
    print(f"Export batch: {result.export_root}")
    print(f"Dry run: {'yes' if result.dry_run else 'no'}")
    print(f"Active only: {'yes' if result.active_only else 'no'}")
    print(f"Session kind filter: {result.session_kind}")
    print(f"{result.summary_label} sessions found: {len(result.session_ids)}")

    if result.dry_run:
        for session_id in result.session_ids:
            print(session_id)
        return 0

    if result.manifest_file is not None:
        print(f"Exported {result.summary_label} sessions: {len(result.success_ids)}")
        print(f"Manifest: {result.manifest_file}")

    if result.failed_exports:
        print("Batch export completed with partial failures.")
        sys.stdout.flush()
        print(f"Failed exports: {len(result.failed_exports)}", file=sys.stderr)
        for session_id, reason in result.failed_exports:
            print(session_id, file=sys.stderr)
            print(f"  reason: {reason}", file=sys.stderr)
        return 1
    return 0


def print_import_result(result: ImportResult) -> int:
    for warning in result.warnings:
        print(_format_operation_warning(warning), file=sys.stderr)
    if result.backup_path is not None:
        print(f"Backed up existing session file to {result.backup_path}")
    if result.resolved_from_session_id:
        print(f"Resolved bundle directory: {result.bundle_dir}")
    if result.created_workspace_dir:
        print(f"Created missing workspace directory: {result.session_cwd}", file=sys.stderr)

    print(f"Imported {result.session_id}")
    print(f"Session file: {result.relative_path}")
    print(f"Import mode: {result.import_mode}")
    print(f"Rollout action: {result.rollout_action}")
    print(f"Session kind: {result.session_kind or 'unknown'}")
    print(f"Workspace group: {result.session_cwd or 'unknown'}")
    print(f"Desktop workspace registered: {'yes' if result.desktop_registered else 'no'}")
    print(f"Desktop registration target: {result.desktop_registration_target or 'none'}")
    print(f"Threads table upserted: {'yes' if result.thread_row_upserted else 'no'}")
    if result.target_desktop_model_provider:
        print(f"Desktop model provider: {result.target_desktop_model_provider}")
    if result.skills_restored_count or result.skills_already_present_count or result.skills_conflict_skipped_count or result.skills_missing_count:
        print(f"Skills restored:          {result.skills_restored_count}")
        print(f"Skills already present:   {result.skills_already_present_count}")
        print(f"Skills conflict skipped:  {result.skills_conflict_skipped_count}")
        print(f"Skills missing:           {result.skills_missing_count}")
    return 0


def print_batch_import_result(result: BatchImportResult) -> int:
    for warning in result.warnings:
        prefix = f"{warning.session_id}: " if warning.session_id else ""
        print(prefix + _format_operation_warning(warning), file=sys.stderr)
    print(f"Bundle root: {result.bundle_root}")
    print(f"Desktop visible: {'yes' if result.desktop_visible else 'no'}")
    print(f"Machine filter: {result.machine_label or result.machine_filter or '全部机器'}")
    print(f"Export group filter: {result.export_group_label or result.export_group_filter or '全部导出方式'}")
    if result.project_label or result.project_filter:
        print(f"Project filter: {result.project_label or result.project_filter}")
    if result.project_source_path:
        print(f"Project source path: {result.project_source_path}")
    if result.target_project_path:
        print(f"Target project path: {result.target_project_path}")
    print(f"History view: {'仅最新' if result.latest_only else '全部历史'}")
    print(f"Bundle directories found: {len(result.bundle_dirs)}")
    print(f"Imported bundle directories: {len(result.success_dirs)}")
    if result.failed_imports:
        print("Batch import completed with partial failures.")
        sys.stdout.flush()
        print(f"Failed imports: {len(result.failed_imports)}", file=sys.stderr)
        for failed_dir, reason in result.failed_imports:
            print(str(failed_dir), file=sys.stderr)
            print(f"  reason: {reason}", file=sys.stderr)
        return 1
    if (
        result.total_skills_restored
        or result.total_skills_already_present
        or result.total_skills_conflict_skipped
    ):
        print(f"Total skills restored:          {result.total_skills_restored}")
        print(f"Total skills already present:   {result.total_skills_already_present}")
        print(f"Total skills conflict skipped:  {result.total_skills_conflict_skipped}")
    if result.skills_restore_report_path:
        print(f"Skills restore report: {result.skills_restore_report_path}")
    return 0


def print_repair_result(result: RepairResult) -> int:
    print(f"Target model provider: {result.provider}")
    print(f"Dry run: {'yes' if result.dry_run else 'no'}")
    print(f"Include CLI: {'yes' if result.include_cli else 'no'}")
    print(f"Valid session files scanned: {result.entries_scanned}")
    print(f"Desktop session files retagged: {result.desktop_retagged}")
    print(f"CLI session files converted: {result.cli_converted}")
    print(f"Skipped invalid session files: {len(result.skipped_sessions)}")
    print(f"Workspace roots active after repair: {result.workspace_roots_count}")
    print(f"Desktop thread rows upserted: {result.threads_updated}")
    if result.backup_root is not None:
        print(f"Backup directory: {result.backup_root}")

    if result.changed_sessions:
        print("Changed session files:")
        for path_str in result.changed_sessions[:20]:
            print(path_str)
        if len(result.changed_sessions) > 20:
            print(f"... and {len(result.changed_sessions) - 20} more")

    if result.warnings:
        print("Warnings:", file=sys.stderr)
        for warning in result.warnings:
            print(_format_operation_warning(warning), file=sys.stderr)
    return 0
