"""Bundle import services."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import BatchImportResult, ImportResult
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.bundle_layout import (
    LEGACY_MACHINE_KEY,
    bundle_export_group_label,
)
from ..stores.bundle_repository import (
    resolve_bundle_dir,
    resolve_known_bundle_dir,
)
from ..stores.bundle_scanner import (
    collect_bundle_summaries,
    latest_distinct_bundle_summaries,
)
from ..stores.desktop_state import ensure_desktop_workspace_root, prepare_session_for_import, upsert_threads_table
from ..stores.index import load_existing_index, upsert_session_index
from ..stores.session_files import extract_last_timestamp, extract_session_field_from_file
from ..support import (
    classify_session_kind,
    iso_to_epoch,
    nearest_existing_parent,
    normalize_bundle_root,
    normalize_project_path,
    project_filter_to_key,
    remap_session_cwd_to_project,
    restrict_to_local_bundle_workspace,
)
from ..validation import (
    load_manifest,
    normalize_updated_at,
    validate_jsonl_file,
    validate_relative_path,
    validate_session_id,
)
from ..stores.skills import (
    read_skills_manifest,
    restore_skills,
    write_batch_skills_restore_report,
)


def import_session(
    paths: CodexPaths,
    input_value: str,
    *,
    bundle_root: Optional[Path] = None,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
    desktop_visible: bool = False,
    session_cwd_override: str = "",
    skills_mode: str = "best-effort",
) -> ImportResult:
    input_path = Path(input_value).expanduser()
    resolved_from_session_id = False
    if input_path.is_dir():
        bundle_dir = restrict_to_local_bundle_workspace(paths, input_path, "Bundle directory")
    else:
        if bundle_root is not None:
            normalized_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
            bundle_dir = resolve_bundle_dir(normalized_bundle_root, input_value)
        else:
            bundle_dir = resolve_known_bundle_dir(
                paths,
                input_value,
                source_group=source_group,
                machine_filter=machine_filter,
                export_group_filter=export_group_filter,
            )
        resolved_from_session_id = True

    manifest_file = bundle_dir / "manifest.env"
    bundle_history = bundle_dir / "history.jsonl"
    if not manifest_file.is_file():
        raise ToolkitError(f"Missing manifest: {manifest_file}")

    manifest = load_manifest(manifest_file)
    session_id = validate_session_id(manifest["SESSION_ID"])
    relative_path = validate_relative_path(manifest["RELATIVE_PATH"], session_id)

    if not Path(input_value).expanduser().is_dir() and input_value != session_id:
        raise ToolkitError(f"Manifest session id does not match requested session id: {session_id}")

    relative_path_obj = Path(*relative_path.split("/"))
    source_session = bundle_dir / "codex" / relative_path_obj
    target_session = paths.code_dir / relative_path_obj

    validate_jsonl_file(source_session, "Bundled session file", "session", session_id)
    if bundle_history.exists():
        validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

    session_cwd = manifest.get("SESSION_CWD", "") or extract_session_field_from_file("cwd", source_session)
    session_source = manifest.get("SESSION_SOURCE", "") or extract_session_field_from_file("source", source_session)
    session_originator = manifest.get("SESSION_ORIGINATOR", "") or extract_session_field_from_file("originator", source_session)
    session_kind = manifest.get("SESSION_KIND", "") or classify_session_kind(session_source, session_originator)
    updated_at = normalize_updated_at(manifest.get("UPDATED_AT", ""), source_session, extract_last_timestamp(source_session))
    thread_name = manifest.get("THREAD_NAME", "")

    state_db = paths.latest_state_db()
    desktop_env = paths.state_file.exists() or state_db is not None
    target_desktop_model_provider = detect_provider(paths) if desktop_env else ""
    auto_desktop_compat = session_kind == "cli" and desktop_env

    prepared_fd, prepared_path = tempfile.mkstemp(prefix="codex-import-session.")
    warnings: list[str] = []
    created_workspace_dir = False
    backup_path = None
    rollout_action = "created"
    try:
        os.close(prepared_fd)
        Path(prepared_path).unlink(missing_ok=True)
        prepared_source_session = Path(prepared_path)
        prepare_session_for_import(
            source_session,
            prepared_source_session,
            auto_desktop_compat=auto_desktop_compat,
            session_kind=session_kind,
            target_desktop_model_provider=target_desktop_model_provider,
            session_cwd_override=session_cwd_override,
        )
        validate_jsonl_file(prepared_source_session, "Prepared session file", "session", session_id)

        import_mode = "native"
        if auto_desktop_compat and session_kind == "cli":
            session_source = "vscode"
            session_originator = "Codex Desktop"
            session_kind = "desktop"
            import_mode = "desktop-compatible"

        target_session.parent.mkdir(parents=True, exist_ok=True)
        existing_index = load_existing_index(paths.index_file)
        prepared_bytes = prepared_source_session.read_bytes()
        effective_updated_at = updated_at
        if target_session.exists():
            existing_bytes = target_session.read_bytes()
            existing_updated_at = normalize_updated_at("", target_session, extract_last_timestamp(target_session))
            existing_epoch = iso_to_epoch(existing_updated_at)
            imported_epoch = iso_to_epoch(updated_at)

            if existing_bytes == prepared_bytes:
                rollout_action = "unchanged"
                effective_updated_at = existing_updated_at or updated_at
            elif existing_epoch and existing_epoch >= imported_epoch:
                rollout_action = "preserved_newer_local"
                effective_updated_at = existing_updated_at
                warnings.append(
                    "Warning: local session is newer than imported bundle; preserved local rollout and merged history only."
                )
            else:
                backup_path = target_session.with_name(target_session.name + f".bak.{int(time.time())}")
                shutil.copy2(target_session, backup_path)
                shutil.copy2(prepared_source_session, target_session)
                rollout_action = "overwritten"
        else:
            shutil.copy2(prepared_source_session, target_session)
            rollout_action = "created"

        effective_session_file = target_session
        session_cwd = extract_session_field_from_file("cwd", effective_session_file) or session_cwd
        session_source = extract_session_field_from_file("source", effective_session_file) or session_source
        session_originator = extract_session_field_from_file("originator", effective_session_file) or session_originator
        session_kind = classify_session_kind(session_source, session_originator)
        effective_updated_at = normalize_updated_at(
            effective_updated_at,
            effective_session_file,
            extract_last_timestamp(effective_session_file),
        )

        if session_cwd and not Path(session_cwd).is_dir():
            if desktop_visible:
                Path(session_cwd).mkdir(parents=True, exist_ok=True)
                created_workspace_dir = True
            else:
                warnings.append(f"Warning: missing workspace directory: {session_cwd}")

        paths.history_file.parent.mkdir(parents=True, exist_ok=True)
        paths.history_file.touch(exist_ok=True)
        existing_history_lines = set(paths.history_file.read_text(encoding="utf-8").splitlines())
        if bundle_history.exists():
            with bundle_history.open("r", encoding="utf-8") as fh_in, paths.history_file.open("a", encoding="utf-8") as fh_out:
                for raw in fh_in:
                    stripped = raw.rstrip("\n")
                    if not stripped or stripped in existing_history_lines:
                        continue
                    fh_out.write(raw if raw.endswith("\n") else raw + "\n")
                    existing_history_lines.add(stripped)

        effective_thread_name = (
            existing_index.get(session_id, {}).get("thread_name")
            if rollout_action == "preserved_newer_local"
            else thread_name or existing_index.get(session_id, {}).get("thread_name")
        )
        upsert_session_index(
            paths.index_file,
            session_id,
            effective_thread_name or f"Imported {session_id}",
            effective_updated_at,
        )

        desktop_registered = False
        desktop_registration_target = ""
        if session_cwd:
            if Path(session_cwd).is_dir():
                desktop_registration_target = session_cwd
            else:
                desktop_registration_target = nearest_existing_parent(session_cwd)
                if desktop_registration_target and desktop_registration_target != session_cwd:
                    warnings.append(
                        "Warning: exact workspace directory is missing, using existing parent for Desktop registration: "
                        f"{desktop_registration_target}"
                    )
        if desktop_registration_target:
            desktop_registered = ensure_desktop_workspace_root(desktop_registration_target, paths.state_file)

        thread_row_upserted = bool(
            state_db and upsert_threads_table(
                state_db,
                effective_session_file,
                bundle_history,
                target_session,
                session_id=session_id,
                thread_name=effective_thread_name or thread_name,
                updated_at=effective_updated_at,
                session_cwd=session_cwd,
                session_source=session_source,
                session_originator=session_originator,
                session_kind=session_kind,
                classify_session_kind=classify_session_kind,
            )
        )

        skills_restored_count = 0
        skills_already_present_count = 0
        skills_conflict_skipped_count = 0
        skills_missing_count = 0

        if skills_mode != "skip":
            try:
                skills_manifest = read_skills_manifest(bundle_dir)
                if skills_manifest is not None and skills_manifest.skills:
                    restore_results = restore_skills(
                        skills_manifest, bundle_dir, paths.home,
                        skills_mode=skills_mode,
                    )
                    for rr in restore_results:
                        if rr.status == "restored":
                            skills_restored_count += 1
                        elif rr.status == "already_present":
                            skills_already_present_count += 1
                        elif rr.status == "conflict_skipped":
                            skills_conflict_skipped_count += 1
                        elif rr.status == "missing":
                            skills_missing_count += 1
                            warnings.append(f"Missing skill: {rr.name} ({rr.source_root}/{rr.relative_dir})")
            except ToolkitError:
                raise
            except Exception:
                pass

        return ImportResult(
            session_id=session_id,
            bundle_dir=bundle_dir,
            relative_path=relative_path,
            import_mode=import_mode,
            rollout_action=rollout_action,
            session_kind=session_kind,
            session_cwd=session_cwd,
            desktop_registered=desktop_registered,
            desktop_registration_target=desktop_registration_target,
            thread_row_upserted=thread_row_upserted,
            target_desktop_model_provider=target_desktop_model_provider,
            resolved_from_session_id=resolved_from_session_id,
            created_workspace_dir=created_workspace_dir,
            backup_path=backup_path,
            warnings=warnings,
            skills_restored_count=skills_restored_count,
            skills_already_present_count=skills_already_present_count,
            skills_conflict_skipped_count=skills_conflict_skipped_count,
            skills_missing_count=skills_missing_count,
        )
    finally:
        Path(prepared_path).unlink(missing_ok=True)


def import_desktop_all(
    paths: CodexPaths,
    *,
    bundle_root: Optional[Path] = None,
    machine_filter: str = "",
    export_group_filter: str = "",
    project_filter: str = "",
    target_project_path: str = "",
    latest_only: bool = False,
    desktop_visible: bool = False,
    skills_mode: str = "best-effort",
) -> BatchImportResult:
    bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_desktop_bundle_root)
    if not bundle_root.is_dir():
        raise ToolkitError(f"Missing bundle root: {bundle_root}")

    normalized_project_filter = project_filter_to_key(project_filter)
    normalized_target_project_path = normalize_project_path(target_project_path)
    resolved_export_group_filter = export_group_filter
    if normalized_project_filter:
        if resolved_export_group_filter and resolved_export_group_filter != "project":
            raise ToolkitError("Project filter can only be used with project bundle imports.")
        resolved_export_group_filter = "project"
    if normalized_target_project_path and not normalized_project_filter:
        raise ToolkitError("Target project path requires a project filter.")

    bundle_summaries = collect_bundle_summaries(
        bundle_root,
        source_group="all",
        machine_filter=machine_filter,
        export_group_filter=resolved_export_group_filter,
        limit=None,
    )
    if normalized_project_filter:
        bundle_summaries = [summary for summary in bundle_summaries if summary.project_key == normalized_project_filter]
    if latest_only:
        bundle_summaries = latest_distinct_bundle_summaries(bundle_summaries)

    if normalized_target_project_path and desktop_visible and not Path(normalized_target_project_path).is_dir():
        Path(normalized_target_project_path).mkdir(parents=True, exist_ok=True)

    bundle_dirs = [summary.bundle_dir for summary in bundle_summaries]
    success_dirs: list[Path] = []
    failed_imports: list[tuple[Path, str]] = []
    total_skills_restored = 0
    total_skills_conflict_skipped = 0
    skills_restore_report_path = None
    for summary in bundle_summaries:
        try:
            session_cwd_override = ""
            if normalized_target_project_path and summary.export_group == "project":
                session_cwd_override = remap_session_cwd_to_project(
                    summary.session_cwd,
                    summary.project_path,
                    normalized_target_project_path,
                )
            result = import_session(
                paths,
                str(summary.bundle_dir),
                bundle_root=bundle_root,
                desktop_visible=desktop_visible,
                session_cwd_override=session_cwd_override,
                skills_mode=skills_mode,
            )
            success_dirs.append(summary.bundle_dir)
            total_skills_restored += result.skills_restored_count + result.skills_already_present_count
            total_skills_conflict_skipped += result.skills_conflict_skipped_count
        except Exception as exc:
            failed_imports.append((summary.bundle_dir, str(exc)))

    machine_label = ""
    if machine_filter:
        matching_machine = next(
            (
                summary.source_machine
                for summary in bundle_summaries
                if (summary.source_machine_key or LEGACY_MACHINE_KEY) == machine_filter
            ),
            "",
        )
        machine_label = matching_machine or machine_filter

    export_group_label = bundle_export_group_label(resolved_export_group_filter) if resolved_export_group_filter else ""
    project_label = ""
    project_source_path = ""
    if normalized_project_filter:
        for summary in bundle_summaries:
            if summary.project_key != normalized_project_filter:
                continue
            project_label = summary.project_label or summary.project_key
            project_source_path = summary.project_path
            break

    return BatchImportResult(
        bundle_root=bundle_root,
        desktop_visible=desktop_visible,
        bundle_dirs=bundle_dirs,
        success_dirs=success_dirs,
        failed_imports=failed_imports,
        machine_filter=machine_filter,
        machine_label=machine_label,
        export_group_filter=resolved_export_group_filter,
        export_group_label=export_group_label,
        latest_only=latest_only,
        project_filter=normalized_project_filter,
        project_label=project_label,
        project_source_path=project_source_path,
        target_project_path=normalized_target_project_path,
        total_skills_restored=total_skills_restored,
        total_skills_conflict_skipped=total_skills_conflict_skipped,
        skills_restore_report_path=skills_restore_report_path,
    )
