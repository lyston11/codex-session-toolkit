"""Bundle import services."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import BatchImportResult, ImportResult, OperationWarning
from ..paths import CodexPaths
from ..services.import_planning import build_batch_import_plan
from ..services.provider import detect_provider
from ..services.skill_sidecars import restore_bundle_skills_sidecar
from ..stores.bundle_repository import (
    resolve_bundle_dir,
    resolve_known_bundle_dir,
)
from ..stores.desktop_state import (
    ensure_desktop_workspace_root,
    load_thread_metadata,
    prepare_session_for_import,
    upsert_threads_table,
)
from ..stores.history import first_history_text
from ..stores.index import is_weak_thread_name, load_existing_index, upsert_session_index
from ..stores.session_files import build_session_preview, extract_last_timestamp, extract_session_field_from_file
from ..stores.session_parser import parse_session_file
from ..support import (
    classify_session_kind,
    iso_to_epoch,
    nearest_existing_parent,
    normalize_bundle_root,
    restrict_to_local_bundle_workspace,
)
from ..validation import (
    load_manifest,
    normalize_updated_at,
    validate_jsonl_file,
    validate_relative_path,
    validate_session_id,
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
    skills_restore_report_path: Optional[Path] = None,
) -> ImportResult:
    bundle_dir, resolved_from_session_id = _resolve_import_bundle_dir(
        paths,
        input_value,
        bundle_root=bundle_root,
        source_group=source_group,
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
    )

    manifest_file = bundle_dir / "manifest.env"
    bundle_history = bundle_dir / "history.jsonl"
    if not manifest_file.is_file():
        raise ToolkitError(f"Missing manifest: {manifest_file}")

    manifest = load_manifest(manifest_file)
    session_id = validate_session_id(manifest["SESSION_ID"])
    relative_path = validate_relative_path(manifest["RELATIVE_PATH"], session_id)

    if resolved_from_session_id and input_value != session_id:
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
    manifest_first_user_message = manifest.get("FIRST_USER_MESSAGE", "")

    state_db = paths.latest_state_db()
    desktop_env = paths.state_file.exists() or state_db is not None
    target_desktop_model_provider = detect_provider(paths) if desktop_env else ""
    auto_desktop_compat = session_kind == "cli" and desktop_env
    existing_desktop_metadata = load_thread_metadata(state_db, session_ids={session_id}).get(session_id, {})
    existing_desktop_thread_name = str(existing_desktop_metadata.get("title") or "")

    prepared_fd, prepared_path = tempfile.mkstemp(prefix="codex-import-session.")
    warnings: list[OperationWarning] = []
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
                warnings.append(OperationWarning(code="local_newer_preserved", session_id=session_id))
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
                warnings.append(
                    OperationWarning(
                        code="missing_workspace_directory",
                        session_id=session_id,
                        path=session_cwd,
                    )
                )

        paths.history_file.parent.mkdir(parents=True, exist_ok=True)
        paths.history_file.touch(exist_ok=True)
        existing_history_lines = set(paths.history_file.read_text(encoding="utf-8").splitlines())
        bundle_history_preview = ""
        if bundle_history.exists():
            bundle_history_lines = bundle_history.read_text(encoding="utf-8").splitlines()
            bundle_history_preview = first_history_text(bundle_history_lines)
            with bundle_history.open("r", encoding="utf-8") as fh_in, paths.history_file.open("a", encoding="utf-8") as fh_out:
                for raw in fh_in:
                    stripped = raw.rstrip("\n")
                    if not stripped or stripped in existing_history_lines:
                        continue
                    fh_out.write(raw if raw.endswith("\n") else raw + "\n")
                    existing_history_lines.add(stripped)

        parsed_effective_session = parse_session_file(effective_session_file)
        recovered_thread_name = build_session_preview(
            bundle_history_preview,
            effective_session_file,
            session_cwd,
            first_user_prompt=parsed_effective_session.first_user_prompt,
        )
        existing_thread_name = existing_index.get(session_id, {}).get("thread_name", "")
        effective_thread_name = _select_import_thread_name(
            session_id,
            manifest_thread_name=thread_name,
            manifest_first_user_message=manifest_first_user_message,
            existing_desktop_thread_name=existing_desktop_thread_name,
            existing_index_thread_name=existing_thread_name,
            recovered_thread_name=recovered_thread_name,
            parsed_first_user_prompt=parsed_effective_session.first_user_prompt,
            bundle_history_preview=bundle_history_preview,
        )
        upsert_session_index(
            paths.index_file,
            session_id,
            effective_thread_name or session_id,
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
                        OperationWarning(
                            code="workspace_parent_used",
                            session_id=session_id,
                            path=session_cwd,
                            related_path=desktop_registration_target,
                        )
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
                first_user_message=manifest_first_user_message or parsed_effective_session.first_user_prompt,
                session_cwd=session_cwd,
                session_source=session_source,
                session_originator=session_originator,
                session_kind=session_kind,
                classify_session_kind=classify_session_kind,
            )
        )

        skills_restore_summary = restore_bundle_skills_sidecar(
            home=paths.home,
            bundle_dir=bundle_dir,
            session_id=session_id,
            skills_mode=skills_mode,
            report_path=skills_restore_report_path,
        )
        skills_restored_count = skills_restore_summary.restored_count
        skills_already_present_count = skills_restore_summary.already_present_count
        skills_conflict_skipped_count = skills_restore_summary.conflict_skipped_count
        skills_missing_count = skills_restore_summary.missing_count
        skills_failed_count = skills_restore_summary.failed_count
        warnings.extend(skills_restore_summary.warnings)

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
            skills_failed_count=skills_failed_count,
        )
    finally:
        Path(prepared_path).unlink(missing_ok=True)


def _first_strong_thread_name(session_id: str, *candidates: object) -> str:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and not is_weak_thread_name(value, session_id):
            return value
    return ""


def _select_import_thread_name(
    session_id: str,
    *,
    manifest_thread_name: str,
    manifest_first_user_message: str,
    existing_desktop_thread_name: str,
    existing_index_thread_name: str,
    recovered_thread_name: str,
    parsed_first_user_prompt: str,
    bundle_history_preview: str,
) -> str:
    manifest_title = str(manifest_thread_name or "").strip()
    first_message = (
        str(manifest_first_user_message or "").strip()
        or str(parsed_first_user_prompt or "").strip()
        or str(bundle_history_preview or "").strip()
    )

    if (
        manifest_title
        and not is_weak_thread_name(manifest_title, session_id)
        and (not first_message or manifest_title != first_message)
    ):
        return manifest_title

    existing_title = _first_strong_thread_name(
        session_id,
        existing_desktop_thread_name,
        existing_index_thread_name,
    )
    if existing_title:
        return existing_title

    return _first_strong_thread_name(session_id, manifest_title, recovered_thread_name)


def _resolve_import_bundle_dir(
    paths: CodexPaths,
    input_value: str,
    *,
    bundle_root: Optional[Path],
    source_group: str,
    machine_filter: str,
    export_group_filter: str,
) -> tuple[Path, bool]:
    input_path = Path(input_value).expanduser()
    if input_path.is_dir():
        return restrict_to_local_bundle_workspace(paths, input_path, "Bundle directory"), False

    if bundle_root is not None:
        normalized_bundle_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
        return resolve_bundle_dir(normalized_bundle_root, input_value), True

    return (
        resolve_known_bundle_dir(
            paths,
            input_value,
            source_group=source_group,
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
        ),
        True,
    )


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
    plan = build_batch_import_plan(
        paths,
        bundle_root=bundle_root,
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
        project_filter=project_filter,
        target_project_path=target_project_path,
        latest_only=latest_only,
        skills_mode=skills_mode,
    )

    if plan.target_project_path and desktop_visible and not Path(plan.target_project_path).is_dir():
        Path(plan.target_project_path).mkdir(parents=True, exist_ok=True)

    success_dirs: list[Path] = []
    failed_imports: list[tuple[Path, str]] = []
    total_skills_restored = 0
    total_skills_already_present = 0
    total_skills_conflict_skipped = 0
    total_skills_missing = 0
    total_skills_failed = 0
    skills_restore_report_path = None
    warnings: list[OperationWarning] = []
    for summary in plan.bundle_summaries:
        try:
            result = import_session(
                paths,
                str(summary.bundle_dir),
                bundle_root=plan.bundle_root,
                desktop_visible=desktop_visible,
                session_cwd_override=plan.session_cwd_override_for(summary),
                skills_mode=skills_mode,
                skills_restore_report_path=plan.skills_restore_report_candidate_path,
            )
            success_dirs.append(summary.bundle_dir)
            total_skills_restored += result.skills_restored_count
            total_skills_already_present += result.skills_already_present_count
            total_skills_conflict_skipped += result.skills_conflict_skipped_count
            total_skills_missing += result.skills_missing_count
            total_skills_failed += result.skills_failed_count
            warnings.extend(result.warnings)
        except (ToolkitError, OSError) as exc:
            failed_imports.append((summary.bundle_dir, str(exc)))

    if (
        plan.skills_restore_report_candidate_path is not None
        and plan.skills_restore_report_candidate_path.is_file()
    ):
        skills_restore_report_path = plan.skills_restore_report_candidate_path

    return BatchImportResult(
        bundle_root=plan.bundle_root,
        desktop_visible=desktop_visible,
        bundle_dirs=plan.bundle_dirs,
        success_dirs=success_dirs,
        failed_imports=failed_imports,
        machine_filter=plan.machine_filter,
        machine_label=plan.machine_label,
        export_group_filter=plan.export_group_filter,
        export_group_label=plan.export_group_label,
        latest_only=latest_only,
        project_filter=plan.project_filter,
        project_label=plan.project_label,
        project_source_path=plan.project_source_path,
        target_project_path=plan.target_project_path,
        total_skills_restored=total_skills_restored,
        total_skills_already_present=total_skills_already_present,
        total_skills_conflict_skipped=total_skills_conflict_skipped,
        total_skills_missing=total_skills_missing,
        total_skills_failed=total_skills_failed,
        skills_restore_report_path=skills_restore_report_path,
        warnings=warnings,
    )
