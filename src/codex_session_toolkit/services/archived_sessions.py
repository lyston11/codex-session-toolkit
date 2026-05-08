"""Archived session cleanup service."""

from __future__ import annotations

from pathlib import Path

from ..models import ArchivedSessionDeleteResult
from ..paths import CodexPaths
from ..stores.desktop_state import delete_thread_rows_by_session_ids, redirect_thread_rows_by_session_paths
from ..stores.index import remove_session_index_entries
from ..stores.session_files import session_id_from_filename


def delete_archived_sessions(
    paths: CodexPaths,
    *,
    session_ids: set[str] | None = None,
    dry_run: bool = False,
) -> ArchivedSessionDeleteResult:
    archive_root = paths.archived_sessions_dir
    requested_session_ids = set(session_ids or set())
    files_to_delete = [
        path
        for path in _archived_session_files(paths)
        if not requested_session_ids or session_id_from_filename(path) in requested_session_ids
    ]
    session_ids = sorted(
        {
            session_id
            for session_id in (session_id_from_filename(path) for path in files_to_delete)
            if session_id
        }
    )
    active_session_paths = _active_session_paths(paths, set(session_ids))
    active_session_ids = set(active_session_paths)
    removable_index_session_ids = set(session_ids) - active_session_ids
    bytes_to_delete = sum(_file_size(path) for path in files_to_delete)
    state_db = paths.latest_state_db() or Path()

    if dry_run:
        thread_rows_restored = redirect_thread_rows_by_session_paths(
            state_db,
            active_session_paths,
            managed_roots=(paths.archived_sessions_dir,),
            dry_run=True,
        )
        thread_rows_removed = delete_thread_rows_by_session_ids(
            state_db,
            set(session_ids) - set(active_session_paths),
            managed_roots=(paths.archived_sessions_dir,),
            dry_run=True,
        )
        return ArchivedSessionDeleteResult(
            archive_root=archive_root,
            dry_run=True,
            files_to_delete=files_to_delete,
            session_ids=session_ids,
            bytes_to_delete=bytes_to_delete,
            index_entries_removed=remove_session_index_entries(
                paths.index_file,
                removable_index_session_ids,
                dry_run=True,
            ),
            thread_rows_removed=thread_rows_removed,
            thread_rows_restored=thread_rows_restored,
        )

    deleted_files: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for session_file in files_to_delete:
        try:
            session_file.unlink()
            deleted_files.append(session_file)
        except OSError as exc:
            errors.append((session_file, str(exc)))

    deleted_session_ids = sorted(
        {
            session_id
            for session_id in (session_id_from_filename(path) for path in deleted_files)
            if session_id
        }
    )
    deleted_session_id_set = set(deleted_session_ids)
    active_session_paths = _active_session_paths(paths, deleted_session_id_set)
    active_session_ids = set(active_session_paths)
    removable_session_ids = deleted_session_id_set - active_session_ids
    thread_rows_restored = redirect_thread_rows_by_session_paths(
        state_db,
        active_session_paths,
        managed_roots=(paths.archived_sessions_dir,),
    )
    thread_rows_removed = delete_thread_rows_by_session_ids(
        state_db,
        removable_session_ids,
        managed_roots=(paths.archived_sessions_dir,),
    )
    index_entries_removed = remove_session_index_entries(paths.index_file, removable_session_ids)
    empty_dirs_removed = _remove_empty_archive_dirs(archive_root)

    return ArchivedSessionDeleteResult(
        archive_root=archive_root,
        dry_run=False,
        files_to_delete=files_to_delete,
        deleted_files=deleted_files,
        session_ids=deleted_session_ids,
        bytes_to_delete=bytes_to_delete,
        index_entries_removed=index_entries_removed,
        thread_rows_removed=thread_rows_removed,
        thread_rows_restored=thread_rows_restored,
        empty_dirs_removed=empty_dirs_removed,
        errors=errors,
    )


def _archived_session_files(paths: CodexPaths) -> list[Path]:
    if not paths.archived_sessions_dir.exists():
        return []
    return sorted(paths.archived_sessions_dir.rglob("rollout-*.jsonl"))


def _active_session_paths(paths: CodexPaths, session_ids: set[str]) -> dict[str, Path]:
    if not session_ids or not paths.sessions_dir.exists():
        return {}
    active_paths: dict[str, Path] = {}
    for session_file in sorted(paths.sessions_dir.rglob("rollout-*.jsonl"), reverse=True):
        session_id = session_id_from_filename(session_file)
        if session_id in session_ids and session_id not in active_paths:
            active_paths[session_id] = session_file
    return active_paths


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove_empty_archive_dirs(archive_root: Path) -> int:
    if not archive_root.exists():
        return 0
    removed = 0
    for directory in sorted(
        [path for path in archive_root.rglob("*") if path.is_dir()],
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
            removed += 1
        except OSError:
            continue
    return removed
