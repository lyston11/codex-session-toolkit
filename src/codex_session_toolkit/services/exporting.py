"""Bundle export services."""

from __future__ import annotations

import shlex
import shutil
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import BatchExportResult, ExportResult, OperationWarning
from ..paths import CodexPaths
from ..stores.history import collect_history_lines_for_session, first_history_text
from ..stores.session_files import (
    collect_session_ids_for_project,
    collect_session_ids_for_kind,
    extract_last_timestamp,
    extract_session_field_from_file,
    find_session_file,
)
from ..support import (
    build_batch_export_root,
    build_machine_bundle_root,
    build_project_export_root,
    build_single_export_root,
    classify_session_kind,
    detect_machine_key,
    detect_machine_label,
    normalize_bundle_root,
    normalize_project_path,
    project_label_from_path,
    project_label_to_key,
)
from ..validation import normalize_relative_path, validate_jsonl_file, validate_session_id
from ..stores.skills import (
    SKILLS_DIR_NAME,
    SKILLS_MANIFEST_FILENAME,
    bundle_skills,
    parse_skills_from_session,
    write_skills_manifest,
)


def export_session(
    paths: CodexPaths,
    session_id: str,
    *,
    bundle_root: Optional[Path] = None,
    skills_mode: str = "best-effort",
) -> ExportResult:
    session_id = validate_session_id(session_id)
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    if bundle_root is None:
        base_bundle_root = normalize_bundle_root(paths, None, paths.default_bundle_root)
        bundle_root = build_single_export_root(base_bundle_root, machine_key)
    else:
        bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    session_file = find_session_file(paths, session_id)
    if not session_file:
        raise ToolkitError(f"Session not found: {session_id}")

    try:
        relative_path = session_file.relative_to(paths.code_dir)
    except ValueError as exc:
        raise ToolkitError(f"Unexpected session path: {session_file}") from exc

    final_bundle_dir = bundle_root / session_id
    stage_root = Path(tempfile.mkdtemp(prefix=f".{session_id}.tmp.", dir=str(bundle_root)))
    stage_bundle_dir = stage_root / session_id
    old_bundle_backup: Optional[Path] = None

    try:
        bundle_codex_dir = stage_bundle_dir / "codex"
        bundle_history = stage_bundle_dir / "history.jsonl"
        manifest_file = stage_bundle_dir / "manifest.env"

        (bundle_codex_dir / relative_path.parent).mkdir(parents=True, exist_ok=True)

        bundled_session = bundle_codex_dir / relative_path
        shutil.copy2(session_file, bundled_session)
        validate_jsonl_file(bundled_session, "Bundled session file", "session", session_id)

        history_lines = collect_history_lines_for_session(paths.history_file, session_id)
        bundle_history.parent.mkdir(parents=True, exist_ok=True)
        with bundle_history.open("w", encoding="utf-8") as fh:
            fh.writelines(history_lines)
        validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

        first_prompt = first_history_text(history_lines)
        session_cwd = extract_session_field_from_file("cwd", session_file)
        session_source = extract_session_field_from_file("source", session_file)
        session_originator = extract_session_field_from_file("originator", session_file)
        session_kind = classify_session_kind(session_source, session_originator)
        last_updated = extract_last_timestamp(session_file) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        skills_bundled_count = 0
        skills_available_count = 0
        skills_manifest_path = None
        warnings: list[OperationWarning] = []

        if skills_mode != "skip":
            try:
                raw_manifest = parse_skills_from_session(session_file)
                skills_available_count = raw_manifest.available_skill_count
                if raw_manifest.skills:
                    bundle_result = bundle_skills(raw_manifest, stage_bundle_dir)
                    bundled_manifest = bundle_result.manifest
                    skills_bundled_count = bundled_manifest.bundled_skill_count
                    bundle_warnings = [
                        _with_session_id(warning, session_id)
                        for warning in bundle_result.warnings
                    ]
                    if bundle_warnings and skills_mode == "strict":
                        raise ToolkitError(_format_export_warning(bundle_warnings[0]))
                    warnings.extend(bundle_warnings)
                    if skills_bundled_count > 0 or skills_available_count > 0:
                        skills_manifest_path = write_skills_manifest(bundled_manifest, stage_bundle_dir)
            except ToolkitError:
                raise
            except OSError as exc:
                shutil.rmtree(stage_bundle_dir / SKILLS_DIR_NAME, ignore_errors=True)
                (stage_bundle_dir / SKILLS_MANIFEST_FILENAME).unlink(missing_ok=True)
                skills_bundled_count = 0
                skills_manifest_path = None
                warning = OperationWarning(
                    code="export_skills_failed",
                    session_id=session_id,
                    path=str(stage_bundle_dir),
                    detail=str(exc),
                )
                if skills_mode == "strict":
                    raise ToolkitError(_format_export_warning(warning)) from exc
                warnings.append(warning)

        manifest_data = OrderedDict(
            SESSION_ID=session_id,
            RELATIVE_PATH=normalize_relative_path(str(relative_path)),
            EXPORTED_AT=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            UPDATED_AT=last_updated,
            THREAD_NAME=first_prompt[:80],
            SESSION_CWD=session_cwd,
            SESSION_SOURCE=session_source,
            SESSION_ORIGINATOR=session_originator,
            SESSION_KIND=session_kind,
            EXPORT_MACHINE=machine_label,
            EXPORT_MACHINE_KEY=machine_key,
        )
        with manifest_file.open("w", encoding="utf-8") as fh:
            for key, value in manifest_data.items():
                fh.write(f"{key}={shlex.quote(value)}\n")

        if final_bundle_dir.exists():
            old_bundle_backup = bundle_root / f".{session_id}.bak.{int(datetime.now().timestamp())}"
            final_bundle_dir.rename(old_bundle_backup)

        stage_bundle_dir.rename(final_bundle_dir)
        shutil.rmtree(stage_root, ignore_errors=True)

        if old_bundle_backup and old_bundle_backup.exists():
            shutil.rmtree(old_bundle_backup, ignore_errors=True)

        return ExportResult(
            session_id=session_id,
            bundle_dir=final_bundle_dir,
            relative_path=normalize_relative_path(str(relative_path)),
            session_kind=session_kind,
            session_cwd=session_cwd,
            source_machine=machine_label,
            source_machine_key=machine_key,
            skills_bundled_count=skills_bundled_count,
            skills_available_count=skills_available_count,
            skills_manifest_path=(
                final_bundle_dir / SKILLS_MANIFEST_FILENAME
                if skills_manifest_path is not None
                else None
            ),
            warnings=warnings,
        )
    except Exception:
        if old_bundle_backup and old_bundle_backup.exists() and not final_bundle_dir.exists():
            old_bundle_backup.rename(final_bundle_dir)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def export_sessions_for_kind(
    paths: CodexPaths,
    *,
    session_kind: str,
    bundle_root: Path,
    dry_run: bool,
    active_only: bool,
    manifest_stem: str,
    summary_label: str,
    archive_group: str,
    skills_mode: str = "best-effort",
) -> BatchExportResult:
    session_ids = collect_session_ids_for_kind(paths, session_kind=session_kind, active_only=active_only)
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    machine_root = build_machine_bundle_root(bundle_root, machine_key)
    export_root = build_batch_export_root(bundle_root, archive_group, machine_key)

    if dry_run:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=True,
            active_only=active_only,
            session_kind=session_kind,
            session_ids=session_ids,
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
            export_group=archive_group,
        )

    if not session_ids:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=False,
            active_only=active_only,
            session_kind=session_kind,
            session_ids=[],
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
            export_group=archive_group,
        )

    export_root.mkdir(parents=True, exist_ok=True)
    success_ids: list[str] = []
    failed_exports: list[tuple[str, str]] = []
    total_skills_bundled = 0
    warnings: list[OperationWarning] = []

    for session_id in session_ids:
        try:
            result = export_session(paths, session_id, bundle_root=export_root, skills_mode=skills_mode)
            success_ids.append(session_id)
            total_skills_bundled += result.skills_bundled_count
            warnings.extend(result.warnings)
        except (ToolkitError, OSError) as exc:
            failed_exports.append((session_id, str(exc)))

    manifest_file = export_root / f"_{manifest_stem}_export_manifest.txt"
    with manifest_file.open("w", encoding="utf-8") as fh:
        fh.write(f"# exported_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        fh.write(f"# session_kind={session_kind}\n")
        fh.write(f"# active_only={1 if active_only else 0}\n")
        fh.write(f"# count={len(success_ids)}\n")
        for session_id in success_ids:
            fh.write(session_id + "\n")

    return BatchExportResult(
        summary_label=summary_label,
        bundle_root=bundle_root,
        export_root=export_root,
        machine_root=machine_root,
        source_machine=machine_label,
        source_machine_key=machine_key,
        dry_run=False,
        active_only=active_only,
        session_kind=session_kind,
        session_ids=session_ids,
        success_ids=success_ids,
        failed_exports=failed_exports,
        manifest_file=manifest_file,
        export_group=archive_group,
        total_skills_bundled=total_skills_bundled,
        warnings=warnings,
    )


def export_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    active_only: bool = False,
    skills_mode: str = "best-effort",
) -> BatchExportResult:
    return export_sessions_for_kind(
        paths,
        session_kind="desktop",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_bundle_root),
        dry_run=dry_run,
        active_only=active_only,
        manifest_stem=("active_desktop" if active_only else "desktop"),
        summary_label=("Active Desktop" if active_only else "Desktop"),
        archive_group=("active" if active_only else "desktop"),
        skills_mode=skills_mode,
    )


def export_active_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    skills_mode: str = "best-effort",
) -> BatchExportResult:
    return export_desktop_all(paths, bundle_root=bundle_root, dry_run=dry_run, active_only=True, skills_mode=skills_mode)


def export_cli_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    skills_mode: str = "best-effort",
) -> BatchExportResult:
    return export_sessions_for_kind(
        paths,
        session_kind="cli",
        bundle_root=normalize_bundle_root(paths, bundle_root, paths.default_bundle_root),
        dry_run=dry_run,
        active_only=False,
        manifest_stem="cli",
        summary_label="CLI",
        archive_group="cli",
        skills_mode=skills_mode,
    )


def export_project_sessions(
    paths: CodexPaths,
    project_path: str,
    *,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
    active_only: bool = False,
    skills_mode: str = "best-effort",
) -> BatchExportResult:
    normalized_project_path = normalize_project_path(project_path)
    if not normalized_project_path:
        raise ToolkitError("Project path cannot be empty.")

    project_label = project_label_from_path(normalized_project_path) or "root"
    project_key = project_label_to_key(project_label)
    resolved_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    session_ids = collect_session_ids_for_project(
        paths,
        project_path=normalized_project_path,
        active_only=active_only,
    )
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    machine_root = build_machine_bundle_root(resolved_bundle_root, machine_key)
    export_root = build_project_export_root(resolved_bundle_root, project_key, machine_key)
    summary_label = f"项目 {project_label}"

    if dry_run:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=resolved_bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=True,
            active_only=active_only,
            session_kind="project",
            session_ids=session_ids,
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
            selection_label=project_label,
            selection_path=normalized_project_path,
            export_group="project",
        )

    if not session_ids:
        return BatchExportResult(
            summary_label=summary_label,
            bundle_root=resolved_bundle_root,
            export_root=export_root,
            machine_root=machine_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            dry_run=False,
            active_only=active_only,
            session_kind="project",
            session_ids=[],
            success_ids=[],
            failed_exports=[],
            manifest_file=None,
            selection_label=project_label,
            selection_path=normalized_project_path,
            export_group="project",
        )

    export_root.mkdir(parents=True, exist_ok=True)
    success_ids: list[str] = []
    failed_exports: list[tuple[str, str]] = []
    total_skills_bundled = 0
    warnings: list[OperationWarning] = []

    for session_id in session_ids:
        try:
            result = export_session(paths, session_id, bundle_root=export_root, skills_mode=skills_mode)
            success_ids.append(session_id)
            total_skills_bundled += result.skills_bundled_count
            warnings.extend(result.warnings)
        except (ToolkitError, OSError) as exc:
            failed_exports.append((session_id, str(exc)))

    manifest_file = export_root / "_project_export_manifest.txt"
    with manifest_file.open("w", encoding="utf-8") as fh:
        fh.write(f"# exported_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        fh.write(f"# project_label={project_label}\n")
        fh.write(f"# project_path={normalized_project_path}\n")
        fh.write(f"# active_only={1 if active_only else 0}\n")
        fh.write(f"# count={len(success_ids)}\n")
        for session_id in success_ids:
            fh.write(session_id + "\n")

    return BatchExportResult(
        summary_label=summary_label,
        bundle_root=resolved_bundle_root,
        export_root=export_root,
        machine_root=machine_root,
        source_machine=machine_label,
        source_machine_key=machine_key,
        dry_run=False,
        active_only=active_only,
        session_kind="project",
        session_ids=session_ids,
        success_ids=success_ids,
        failed_exports=failed_exports,
        manifest_file=manifest_file,
        selection_label=project_label,
        selection_path=normalized_project_path,
        export_group="project",
        total_skills_bundled=total_skills_bundled,
        warnings=warnings,
    )


def _with_session_id(warning: OperationWarning, session_id: str) -> OperationWarning:
    return OperationWarning(
        code=warning.code,
        session_id=session_id,
        path=warning.path,
        related_path=warning.related_path,
        detail=warning.detail,
        name=warning.name,
        source_root=warning.source_root,
        relative_dir=warning.relative_dir,
    )


def _format_export_warning(warning: OperationWarning) -> str:
    if warning.code == "skill_not_bundled":
        detail = f": {warning.detail}" if warning.detail else ""
        return (
            "Custom skill not bundled: "
            f"{warning.name} ({warning.source_root}/{warning.relative_dir}){detail}"
        )
    if warning.code == "bundle_skill_failed":
        return (
            "Failed to bundle custom skill: "
            f"{warning.name} ({warning.source_root}/{warning.relative_dir}): {warning.detail}"
        )
    if warning.code == "export_skills_failed":
        return f"Failed to export skills sidecar for session {warning.session_id}: {warning.detail}"
    return warning.detail or warning.code
