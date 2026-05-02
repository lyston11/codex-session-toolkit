"""Session rollout backup browsing and restore helpers."""

from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..errors import ToolkitError
from ..models import SessionBackupDeleteResult, SessionBackupRestoreResult, SessionBackupSummary
from ..paths import CodexPaths
from ..stores.session_files import build_session_preview, session_id_from_filename
from ..stores.session_parser import parse_session_summary_file
from ..support import ensure_path_within_dir
from ..validation import validate_jsonl_file, validate_session_id


BACKUP_NAME_RE = re.compile(r"^(rollout-.+\.jsonl)\.bak(?:\.(restore))?\.(\d+)$")


def list_session_backups(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
) -> list[SessionBackupSummary]:
    backups: list[SessionBackupSummary] = []
    for backup_path in _iter_session_backup_files(paths):
        summary = _summarize_backup(paths, backup_path)
        if summary is None:
            continue
        if pattern:
            haystack = " ".join(
                [
                    summary.session_id,
                    summary.scope,
                    summary.backup_kind,
                    summary.model_provider,
                    summary.cwd,
                    summary.preview,
                    str(summary.backup_path),
                    str(summary.target_path),
                ]
            )
            if pattern not in haystack:
                continue
        backups.append(summary)

    backups.sort(
        key=lambda item: (
            item.backup_epoch,
            item.backup_path.stat().st_mtime if item.backup_path.exists() else 0,
            str(item.backup_path),
        ),
        reverse=True,
    )
    if limit is not None:
        return backups[: max(1, limit)]
    return backups


def restore_session_backup(
    paths: CodexPaths,
    backup_path_or_session_id: str,
    *,
    dry_run: bool = False,
) -> SessionBackupRestoreResult:
    summary = resolve_session_backup(paths, backup_path_or_session_id)
    validate_jsonl_file(summary.backup_path, "Session backup", "session", expected_session_id=summary.session_id)

    if dry_run:
        return SessionBackupRestoreResult(
            session_id=summary.session_id,
            backup_path=summary.backup_path,
            target_path=summary.target_path,
            dry_run=True,
        )

    summary.target_path.parent.mkdir(parents=True, exist_ok=True)
    current_backup_path: Optional[Path] = None
    if summary.target_path.exists():
        current_backup_path = _next_restore_backup_path(summary.target_path)
        shutil.copy2(summary.target_path, current_backup_path)

    shutil.copy2(summary.backup_path, summary.target_path)
    return SessionBackupRestoreResult(
        session_id=summary.session_id,
        backup_path=summary.backup_path,
        target_path=summary.target_path,
        dry_run=False,
        restored=True,
        current_backup_path=current_backup_path,
    )


def delete_session_backup(
    paths: CodexPaths,
    backup_path_or_session_id: str,
    *,
    dry_run: bool = False,
) -> SessionBackupDeleteResult:
    summary = resolve_session_backup(paths, backup_path_or_session_id)

    if not dry_run:
        summary.backup_path.unlink()

    return SessionBackupDeleteResult(
        session_id=summary.session_id,
        backup_path=summary.backup_path,
        target_path=summary.target_path,
        dry_run=dry_run,
        deleted=not dry_run,
    )


def resolve_session_backup(paths: CodexPaths, backup_path_or_session_id: str) -> SessionBackupSummary:
    raw_value = (backup_path_or_session_id or "").strip()
    if not raw_value:
        raise ToolkitError("Missing backup path or session id.")

    candidate = Path(raw_value).expanduser()
    if candidate.exists():
        summary = _summarize_backup(paths, candidate)
        if summary is None:
            raise ToolkitError(f"Not a supported session backup: {candidate}")
        return summary

    backups = list_session_backups(paths)
    path_matches = [
        summary
        for summary in backups
        if summary.backup_path.name == raw_value or str(summary.backup_path) == raw_value
    ]
    if path_matches:
        return path_matches[0]

    validate_session_id(raw_value)
    session_matches = [summary for summary in backups if summary.session_id == raw_value]
    if not session_matches:
        raise ToolkitError(f"No session backup found for: {raw_value}")
    return session_matches[0]


def _iter_session_backup_files(paths: CodexPaths) -> list[Path]:
    backup_files: list[Path] = []
    for root in (paths.sessions_dir, paths.archived_sessions_dir):
        if root.exists():
            backup_files.extend(sorted(root.rglob("rollout-*.jsonl.bak.*")))
    return backup_files


def _summarize_backup(paths: CodexPaths, backup_path: Path) -> Optional[SessionBackupSummary]:
    backup_path = Path(backup_path).expanduser()
    backup_root = _backup_root_for_path(paths, backup_path)
    if backup_root is None:
        return None

    match = BACKUP_NAME_RE.match(backup_path.name)
    if not match:
        return None

    base_name, restore_marker, epoch_text = match.groups()
    session_id = session_id_from_filename(Path(base_name))
    if not session_id:
        return None

    target_path = backup_path.with_name(base_name)
    ensure_path_within_dir(backup_path, backup_root, "Session backup")
    ensure_path_within_dir(target_path, backup_root, "Session backup target")

    backup_epoch = int(epoch_text)
    backup_kind = "restore-safety" if restore_marker else "import-overwrite"
    backup_time_label = datetime.fromtimestamp(backup_epoch).strftime("%Y-%m-%d %H:%M:%S")
    scope = "archived" if backup_root == paths.archived_sessions_dir else "active"
    size_bytes = backup_path.stat().st_size if backup_path.exists() else 0

    kind = "unknown"
    cwd = ""
    model_provider = ""
    preview = ""
    try:
        parsed = parse_session_summary_file(backup_path, include_first_user_prompt=True)
        kind = parsed.session_kind
        cwd = parsed.cwd
        model_provider = parsed.model_provider
        preview = build_session_preview("", backup_path, cwd, first_user_prompt=parsed.first_user_prompt)
    except ToolkitError:
        preview = backup_path.name

    return SessionBackupSummary(
        session_id=session_id,
        scope=scope,
        backup_path=backup_path,
        target_path=target_path,
        backup_kind=backup_kind,
        backup_epoch=backup_epoch,
        backup_time_label=backup_time_label,
        size_bytes=size_bytes,
        target_exists=target_path.exists(),
        preview=preview,
        kind=kind,
        cwd=cwd,
        model_provider=model_provider,
    )


def _backup_root_for_path(paths: CodexPaths, backup_path: Path) -> Optional[Path]:
    for root in (paths.sessions_dir, paths.archived_sessions_dir):
        try:
            ensure_path_within_dir(backup_path, root, "Session backup")
        except ToolkitError:
            continue
        return root
    return None


def _next_restore_backup_path(target_path: Path) -> Path:
    epoch = int(time.time())
    while True:
        candidate = target_path.with_name(target_path.name + f".bak.restore.{epoch}")
        if not candidate.exists():
            return candidate
        epoch += 1
