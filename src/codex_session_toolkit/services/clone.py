"""Clone and cleanup services."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import CleanupResult, CloneFileResult, CloneRunResult, MigratedOriginalSessionDeleteResult, MigratedOriginalSessionSummary
from ..paths import CodexPaths
from ..services.provider import detect_provider
from ..stores.desktop_state import delete_thread_rows_by_session_ids
from ..stores.index import remove_session_index_entries
from ..stores.session_files import (
    build_session_preview,
    extract_timestamp_from_rollout_name,
    first_user_prompt_from_session,
    iter_session_files,
    parse_jsonl_records,
    read_session_payload,
    session_id_from_filename,
)


def build_clone_index(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    active_only: bool = True,
    quiet: bool = False,
) -> set[str]:
    provider = detect_provider(paths, explicit=target_provider)
    cloned_from_ids: set[str] = set()
    total_files = 0

    if not quiet:
        print("Building clone index...", end="", flush=True)

    for session_file in iter_session_files(paths, active_only=active_only):
        total_files += 1
        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        if payload.get("model_provider") != provider:
            continue

        origin_id = payload.get("cloned_from")
        if isinstance(origin_id, str) and origin_id:
            cloned_from_ids.add(origin_id)

    if not quiet:
        print(f" Done. Found {len(cloned_from_ids)} existing clones out of {total_files} files.")

    return cloned_from_ids


def clone_session_file(
    paths: CodexPaths,
    session_file: Path,
    *,
    target_provider: str = "",
    already_cloned_ids: Optional[set[str]] = None,
    dry_run: bool = False,
) -> CloneFileResult:
    session_file = Path(session_file).expanduser()
    provider = detect_provider(paths, explicit=target_provider)
    if already_cloned_ids is None:
        already_cloned_ids = build_clone_index(paths, target_provider=provider, quiet=True)

    try:
        records = parse_jsonl_records(session_file)
    except Exception as exc:
        return CloneFileResult("error", str(exc))

    if not records:
        return CloneFileResult("error", "Empty file")

    meta_index = -1
    session_meta: dict = {}
    for idx, (_, obj) in enumerate(records):
        if obj and obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
            meta_index = idx
            session_meta = dict(obj)
            break

    if meta_index < 0:
        return CloneFileResult("error", "Not a session file")

    payload = dict(session_meta["payload"])
    current_provider = payload.get("model_provider", "")
    current_id = payload.get("id")

    if not isinstance(current_id, str) or not current_id:
        return CloneFileResult("error", "Session id missing from session_meta")

    if current_provider == provider:
        return CloneFileResult("skipped_target", "Already on target provider")

    if current_id in already_cloned_ids:
        return CloneFileResult("skipped_exists", f"Already cloned (ID: {current_id})")

    new_id = str(uuid.uuid4())
    new_payload = dict(payload)
    new_payload["id"] = new_id
    new_payload["model_provider"] = provider
    new_payload["cloned_from"] = current_id
    new_payload["original_provider"] = current_provider
    new_payload["clone_timestamp"] = datetime.now(timezone.utc).isoformat()
    session_meta["payload"] = new_payload

    old_filename = session_file.name
    if current_id in old_filename:
        new_filename = old_filename.replace(current_id, new_id, 1)
    else:
        timestamp = extract_timestamp_from_rollout_name(old_filename)
        new_filename = f"rollout-{timestamp}-{new_id}.jsonl" if timestamp else f"rollout-CLONE-{new_id}.jsonl"

    new_file_path = session_file.with_name(new_filename)
    if new_file_path.exists():
        return CloneFileResult("skipped_exists", "Target file collision")

    output_lines = []
    for idx, (raw, _) in enumerate(records):
        if idx == meta_index:
            output_lines.append(json.dumps(session_meta, ensure_ascii=False, separators=(",", ":")) + "\n")
        else:
            output_lines.append(raw)

    if not dry_run:
        with new_file_path.open("w", encoding="utf-8") as fh:
            fh.writelines(output_lines)

    already_cloned_ids.add(current_id)
    action_prefix = "[DRY-RUN] Would create" if dry_run else "Created"
    message = f"{action_prefix} {new_filename} (from {current_provider or 'unknown'})"
    return CloneFileResult("cloned", message, new_file_path)


def clone_to_provider(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> CloneRunResult:
    provider = detect_provider(paths, explicit=target_provider)
    already_cloned = build_clone_index(paths, target_provider=provider, active_only=active_only)
    stats = {
        "cloned": 0,
        "skipped_exists": 0,
        "skipped_target": 0,
        "error": 0,
    }
    messages = []
    errors = []

    for session_file in iter_session_files(paths, active_only=active_only):
        result = clone_session_file(
            paths,
            session_file,
            target_provider=provider,
            already_cloned_ids=already_cloned,
            dry_run=dry_run,
        )
        stats[result.action] = stats.get(result.action, 0) + 1
        if result.action == "cloned":
            messages.append(result.message)
        elif result.action == "error":
            errors.append(f"{session_file.name}: {result.message}")

    return CloneRunResult(
        provider=provider,
        dry_run=dry_run,
        stats=stats,
        messages=messages,
        errors=errors,
    )


def cleanup_clones(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    dry_run: bool = False,
    active_only: bool = True,
) -> CleanupResult:
    provider = detect_provider(paths, explicit=target_provider)

    originals_by_ts: set[str] = set()
    targets_without_tag_by_ts: dict[str, list[Path]] = {}
    files_checked = 0

    for session_file in iter_session_files(paths, active_only=active_only):
        files_checked += 1
        timestamp = extract_timestamp_from_rollout_name(session_file.name)
        if not timestamp:
            continue

        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        current_provider = payload.get("model_provider", "")
        cloned_from = payload.get("cloned_from")
        if current_provider == provider:
            if not isinstance(cloned_from, str) or not cloned_from:
                targets_without_tag_by_ts.setdefault(timestamp, []).append(session_file)
        else:
            originals_by_ts.add(timestamp)

    files_to_delete: list[Path] = []
    for timestamp, paths_for_ts in targets_without_tag_by_ts.items():
        if timestamp in originals_by_ts:
            files_to_delete.extend(paths_for_ts)

    deleted = []
    errors = []
    if not dry_run:
        for target_path in files_to_delete:
            try:
                target_path.unlink()
                deleted.append(target_path)
            except Exception as exc:
                errors.append((target_path, str(exc)))

    return CleanupResult(
        provider=provider,
        dry_run=dry_run,
        files_checked=files_checked,
        files_to_delete=files_to_delete,
        deleted=deleted,
        errors=errors,
    )


def list_migrated_original_sessions(
    paths: CodexPaths,
    *,
    target_provider: str = "",
    active_only: bool = True,
) -> list[MigratedOriginalSessionSummary]:
    provider = detect_provider(paths, explicit=target_provider)
    sessions_by_id: dict[str, tuple[Path, dict]] = {}
    cloned_by_original_id: dict[str, tuple[Path, dict]] = {}

    for session_file in iter_session_files(paths, active_only=active_only):
        try:
            payload = read_session_payload(session_file)
        except Exception:
            continue

        session_id = payload.get("id") or session_id_from_filename(session_file)
        if not isinstance(session_id, str) or not session_id:
            continue
        sessions_by_id[session_id] = (session_file, payload)

        cloned_from = payload.get("cloned_from")
        if payload.get("model_provider") == provider and isinstance(cloned_from, str) and cloned_from:
            cloned_by_original_id.setdefault(cloned_from, (session_file, payload))

    candidates: list[MigratedOriginalSessionSummary] = []
    for original_id, (clone_path, clone_payload) in cloned_by_original_id.items():
        original = sessions_by_id.get(original_id)
        if original is None:
            continue
        original_path, original_payload = original
        if original_payload.get("model_provider") == provider:
            continue

        candidates.append(
            MigratedOriginalSessionSummary(
                session_id=original_id,
                path=original_path,
                model_provider=str(original_payload.get("model_provider") or ""),
                cloned_session_id=str(clone_payload.get("id") or session_id_from_filename(clone_path) or ""),
                cloned_path=clone_path,
                cloned_provider=str(clone_payload.get("model_provider") or ""),
                kind=_session_kind(original_payload),
                cwd=str(original_payload.get("cwd") or ""),
                preview=build_session_preview(
                    "",
                    original_path,
                    str(original_payload.get("cwd") or ""),
                    first_user_prompt=first_user_prompt_from_session(original_path),
                ),
            )
        )

    return sorted(candidates, key=lambda summary: str(summary.path), reverse=True)


def delete_migrated_original_sessions(
    paths: CodexPaths,
    *,
    session_ids: set[str] | None = None,
    target_provider: str = "",
    dry_run: bool = False,
) -> MigratedOriginalSessionDeleteResult:
    provider = detect_provider(paths, explicit=target_provider)
    requested_session_ids = set(session_ids or set())
    candidates = [
        candidate
        for candidate in list_migrated_original_sessions(paths, target_provider=provider)
        if not requested_session_ids or candidate.session_id in requested_session_ids
    ]
    candidate_ids = {candidate.session_id for candidate in candidates}
    state_db = paths.latest_state_db() or Path()

    index_entries_removed = remove_session_index_entries(paths.index_file, candidate_ids, dry_run=True)
    thread_rows_removed = delete_thread_rows_by_session_ids(
        state_db,
        candidate_ids,
        managed_roots=(paths.sessions_dir,),
        dry_run=True,
    )

    if dry_run:
        return MigratedOriginalSessionDeleteResult(
            provider=provider,
            dry_run=True,
            candidates=candidates,
            session_ids=sorted(candidate_ids),
            index_entries_removed=index_entries_removed,
            thread_rows_removed=thread_rows_removed,
        )

    deleted_files: list[Path] = []
    errors: list[tuple[Path, str]] = []
    deleted_session_ids: set[str] = set()
    for candidate in candidates:
        try:
            candidate.path.unlink()
            deleted_files.append(candidate.path)
            deleted_session_ids.add(candidate.session_id)
        except OSError as exc:
            errors.append((candidate.path, str(exc)))

    if deleted_session_ids:
        index_entries_removed = remove_session_index_entries(paths.index_file, deleted_session_ids)
        thread_rows_removed = delete_thread_rows_by_session_ids(
            state_db,
            deleted_session_ids,
            managed_roots=(paths.sessions_dir,),
        )
    else:
        index_entries_removed = 0
        thread_rows_removed = 0

    return MigratedOriginalSessionDeleteResult(
        provider=provider,
        dry_run=False,
        candidates=candidates,
        deleted_files=deleted_files,
        session_ids=sorted(deleted_session_ids),
        index_entries_removed=index_entries_removed,
        thread_rows_removed=thread_rows_removed,
        errors=errors,
    )


def _session_kind(payload: dict) -> str:
    source = str(payload.get("source") or "").lower()
    originator = str(payload.get("originator") or "").lower()
    if "cli" in source or "cli" in originator:
        return "cli"
    return "desktop"
