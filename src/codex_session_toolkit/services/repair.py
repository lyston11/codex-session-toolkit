"""Desktop repair service."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime, timezone

from ..errors import ToolkitError
from ..models import OperationWarning, RepairResult
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.desktop_state import (
    build_threads_row,
    load_desktop_state_data,
    merge_workspace_root,
    prune_threads_rows,
    upsert_threads_rows,
    write_desktop_state_data,
)
from ..stores.history import first_history_messages
from ..stores.index import SessionIndexEntry, load_existing_index, write_session_index_entries
from ..stores.session_files import build_session_preview, iter_session_files
from ..stores.session_parser import parse_session_file
from ..support import backup_file, classify_session_kind, iso_to_epoch, nearest_existing_parent, normalize_iso


def repair_desktop(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    include_cli: bool = False,
    include_archived: bool = False,
) -> RepairResult:
    if not paths.code_dir.is_dir():
        raise ToolkitError(f"Missing Codex data directory: {paths.code_dir}")

    provider = detect_provider(paths, explicit=target_provider)
    backup_root = paths.code_dir / "repair_backups" / f"visibility-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backed_up: set[str] = set()
    warnings: list[OperationWarning] = []

    history_first_messages = first_history_messages(paths.history_file)
    existing_index = load_existing_index(paths.index_file)
    state_db = paths.latest_state_db()

    entries: list[dict] = []
    changed_sessions: list[str] = []
    skipped_sessions: list[str] = []
    workspace_candidates: "OrderedDict[str, bool]" = OrderedDict()
    desktop_retagged = 0
    cli_converted = 0

    for session_file in iter_session_files(paths, active_only=not include_archived):
        try:
            parsed_session = parse_session_file(session_file)
        except ToolkitError as exc:
            warnings.append(
                OperationWarning(
                    code="skipped_invalid_session_file",
                    path=str(session_file),
                    detail=str(exc),
                )
            )
            skipped_sessions.append(str(session_file))
            continue

        records = parsed_session.records
        session_meta = dict(parsed_session.session_meta)

        session_id = session_meta.get("id")
        if not isinstance(session_id, str) or not session_id:
            warnings.append(
                OperationWarning(
                    code="skipped_session_without_id",
                    path=str(session_file),
                )
            )
            skipped_sessions.append(str(session_file))
            continue

        source_name = session_meta.get("source", "")
        originator_name = session_meta.get("originator", "")
        session_kind = classify_session_kind(source_name, originator_name)
        desktop_like = session_kind == "desktop"
        convert_cli = include_cli and session_kind == "cli"

        updated_meta = dict(session_meta)
        changed = False

        if desktop_like and provider and updated_meta.get("model_provider") != provider:
            updated_meta["model_provider"] = provider
            changed = True
            desktop_retagged += 1

        if convert_cli:
            if updated_meta.get("source") != "vscode":
                updated_meta["source"] = "vscode"
                changed = True
            if updated_meta.get("originator") != "Codex Desktop":
                updated_meta["originator"] = "Codex Desktop"
                changed = True
            if provider and updated_meta.get("model_provider") != provider:
                updated_meta["model_provider"] = provider
                changed = True
            if changed:
                cli_converted += 1
            source_name = updated_meta.get("source", source_name)
            originator_name = updated_meta.get("originator", originator_name)
            session_kind = "desktop"
            desktop_like = True

        if changed:
            changed_sessions.append(str(session_file))
            if not dry_run:
                backup_file(paths.code_dir, backup_root, backed_up, session_file, enabled=True)
                with session_file.open("w", encoding="utf-8") as fh:
                    for raw, obj in records:
                        if not obj:
                            fh.write(raw)
                            continue
                        if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                            patched = dict(obj)
                            patched["payload"] = updated_meta
                            fh.write(json.dumps(patched, ensure_ascii=False, separators=(",", ":")) + "\n")
                        else:
                            fh.write(raw)

        session_meta = updated_meta
        created_iso = normalize_iso(str(session_meta.get("timestamp", ""))) or normalize_iso(parsed_session.last_timestamp)
        updated_iso = (
            normalize_iso(parsed_session.last_timestamp)
            or created_iso
            or existing_index.get(session_id, {}).get("updated_at")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        cwd = session_meta.get("cwd", "") if isinstance(session_meta.get("cwd", ""), str) else ""
        thread_name = _repair_thread_name(
            session_id=session_id,
            session_file=session_file,
            cwd=cwd,
            first_user_prompt=parsed_session.first_user_prompt,
            existing_index=existing_index,
            history_first_messages=history_first_messages,
        )
        first_user_message = history_first_messages.get(session_id) or parsed_session.first_user_prompt or thread_name
        if cwd:
            candidate = nearest_existing_parent(cwd) or cwd
            if candidate and candidate not in workspace_candidates:
                workspace_candidates[candidate] = True

        entries.append(
            {
                "id": session_id,
                "thread_name": thread_name,
                "updated_at": updated_iso,
                "session_file": session_file,
                "source": source_name,
                "originator": originator_name,
                "kind": session_kind,
                "cwd": cwd,
                "created_iso": created_iso or updated_iso,
                "updated_iso": updated_iso,
                "first_user_message": first_user_message,
                "parsed_session": parsed_session,
            }
        )

    entries.sort(key=lambda item: (iso_to_epoch(item["updated_at"]), item["id"]), reverse=True)

    if not dry_run:
        backup_file(paths.code_dir, backup_root, backed_up, paths.index_file, enabled=True)
        write_session_index_entries(
            paths.index_file,
            [
                SessionIndexEntry(
                    session_id=str(entry["id"]),
                    thread_name=str(entry["thread_name"]),
                    updated_at=str(entry["updated_at"]),
                )
                for entry in entries
            ],
        )

    state_data = load_desktop_state_data(paths.state_file)

    for root in workspace_candidates:
        merge_workspace_root(state_data, root)

    if not dry_run:
        backup_file(paths.code_dir, backup_root, backed_up, paths.state_file, enabled=True)
        write_desktop_state_data(paths.state_file, state_data)

    thread_rows = [
        build_threads_row(
            entry["session_file"],
            entry["session_file"],
            parsed_session=entry["parsed_session"],
            thread_name=str(entry["thread_name"]),
            updated_at=str(entry["updated_iso"]),
            first_user_message=str(entry["first_user_message"]),
            session_cwd=str(entry["cwd"]),
            session_source=str(entry["source"]),
            session_originator=str(entry["originator"]),
            session_kind=str(entry["kind"]),
            model_provider_override=provider,
        )
        for entry in entries
        if entry["kind"] == "desktop"
    ]

    threads_updated = 0
    threads_pruned = 0
    if state_db and state_db.exists():
        if not dry_run:
            backup_file(paths.code_dir, backup_root, backed_up, state_db, enabled=True)
        if not skipped_sessions:
            threads_pruned = prune_threads_rows(
                state_db,
                desired_session_ids={str(entry["id"]) for entry in entries if entry["kind"] == "desktop"},
                managed_roots=(paths.sessions_dir, paths.archived_sessions_dir),
                dry_run=dry_run,
            )
        threads_updated = upsert_threads_rows(state_db, thread_rows, dry_run=dry_run)

    return RepairResult(
        provider=provider,
        dry_run=dry_run,
        include_cli=include_cli,
        include_archived=include_archived,
        entries_scanned=len(entries),
        desktop_retagged=desktop_retagged,
        cli_converted=cli_converted,
        skipped_sessions=skipped_sessions,
        workspace_roots_count=len(state_data.get("active-workspace-roots", [])),
        threads_updated=threads_updated,
        threads_pruned=threads_pruned,
        backup_root=(None if dry_run else backup_root),
        changed_sessions=changed_sessions,
        warnings=warnings,
    )


def _repair_thread_name(
    *,
    session_id: str,
    session_file,
    cwd: str,
    first_user_prompt: str,
    existing_index: dict,
    history_first_messages: dict[str, str],
) -> str:
    existing_name = str(existing_index.get(session_id, {}).get("thread_name") or "").strip()
    if not _is_weak_thread_name(existing_name, session_id):
        return existing_name

    preview = build_session_preview(
        history_first_messages.get(session_id, ""),
        session_file,
        cwd,
        first_user_prompt=first_user_prompt,
    )
    if preview and not _is_weak_thread_name(preview, session_id):
        return preview
    return session_id


def _is_weak_thread_name(thread_name: str, session_id: str) -> bool:
    normalized = (thread_name or "").strip()
    return (
        not normalized
        or normalized == session_id
        or normalized == f"Imported {session_id}"
        or normalized.startswith("rollout-")
    )
