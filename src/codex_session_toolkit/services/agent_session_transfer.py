"""File-level Bundle export/import for non-Codex agent sessions.

Codex bundles carry the full Codex pipeline (history/index/desktop repair,
lineage, thread-history sidecars).  Claude Code, Pi, and ZCode sessions have
no such state stores, so their bundles stay deliberately simple: the session
file copied at its home-relative path plus a manifest that records the agent,
and import copies it back with hash-based conflict handling.

Note: 为何非 Codex Bundle 是文件级布局、不经 Codex 导入管线 —
见 .agents/notes/implemented/architecture/2026-09-12-agent-session-file-bundles.md
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..errors import ToolkitError
from ..models import ExportResult, ImportResult, OperationWarning
from ..paths import CodexPaths
from ..stores.agent_sessions import (
    NON_CODEX_SESSION_AGENTS,
    collect_agent_session_summaries,
    iter_agent_session_files,
)
from ..support import (
    build_single_export_root,
    detect_machine_key,
    detect_machine_label,
    normalize_bundle_root,
    restrict_to_local_bundle_workspace,
)
from ..validation import normalize_relative_path, validate_session_id, write_manifest

_AGENT_SESSION_MODES = {"best-effort", "skip", "overwrite", "strict"}


def export_agent_sessions(
    paths: CodexPaths,
    session_ids: List[str],
    *,
    agent: str,
    bundle_root: Optional[Path] = None,
) -> List[ExportResult]:
    if agent not in NON_CODEX_SESSION_AGENTS:
        raise ToolkitError(f"Unsupported session agent: {agent}")
    if not session_ids:
        raise ToolkitError("Session id is required.")
    for session_id in session_ids:
        validate_session_id(session_id)

    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root)
    export_root = build_single_export_root(resolved_root, machine_key)
    export_root.mkdir(parents=True, exist_ok=True)

    home = paths.home
    available = collect_agent_session_summaries(home, agents=(agent,))
    by_id = {summary.session_id: summary for summary in available}

    results: List[ExportResult] = []
    seen: set[str] = set()
    for session_id in session_ids:
        if session_id in seen:
            continue
        seen.add(session_id)
        summary = by_id.get(session_id)
        if summary is None:
            raise ToolkitError(f"{agent} session not found: {session_id}")
        results.append(
            _export_one_agent_session(
                paths,
                summary,
                agent=agent,
                export_root=export_root,
                machine_key=machine_key,
                machine_label=machine_label,
            )
        )
    return results


def _export_one_agent_session(
    paths: CodexPaths,
    summary,
    *,
    agent: str,
    export_root: Path,
    machine_key: str,
    machine_label: str,
) -> ExportResult:
    session_file = summary.path
    try:
        relative_path = session_file.relative_to(paths.home)
    except ValueError as exc:
        raise ToolkitError(f"Unexpected {agent} session path: {session_file}") from exc
    relative_posix = normalize_relative_path(relative_path.as_posix())

    final_bundle_dir = export_root / validate_session_id(summary.session_id)
    stage_root = Path(tempfile.mkdtemp(prefix=".tmp.", dir=str(export_root)))
    stage_bundle_dir = stage_root / final_bundle_dir.name
    old_backup: Optional[Path] = None
    try:
        staged_file = stage_bundle_dir / Path(*relative_posix.split("/"))
        staged_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(session_file, staged_file)
        manifest_data = OrderedDict(
            SESSION_ID=validate_session_id(summary.session_id),
            AGENT=agent,
            RELATIVE_PATH=relative_posix,
            EXPORTED_AT=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            UPDATED_AT=_mtime_iso(session_file),
            THREAD_NAME=(summary.thread_name or summary.preview or "")[:80],
            FIRST_USER_MESSAGE=(summary.preview or "")[:200],
            SESSION_CWD=summary.cwd,
            SESSION_KIND=summary.kind,
            SESSION_SOURCE=agent,
            EXPORT_MACHINE=machine_label,
            EXPORT_MACHINE_KEY=machine_key,
        )
        write_manifest(stage_bundle_dir / "manifest.env", manifest_data)

        if final_bundle_dir.exists():
            old_backup = export_root / f".{final_bundle_dir.name}.bak.{int(datetime.now().timestamp())}"
            final_bundle_dir.rename(old_backup)
        stage_bundle_dir.rename(final_bundle_dir)
    except Exception:
        shutil.rmtree(stage_bundle_dir, ignore_errors=True)
        if old_backup and old_backup.exists() and not final_bundle_dir.exists():
            old_backup.rename(final_bundle_dir)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        if old_backup and old_backup.exists():
            shutil.rmtree(old_backup, ignore_errors=True)

    return ExportResult(
        session_id=summary.session_id,
        bundle_dir=final_bundle_dir,
        relative_path=relative_posix,
        session_kind=summary.kind,
        session_cwd=summary.cwd,
        source_machine=machine_label,
        source_machine_key=machine_key,
    )


def import_agent_session_bundle(
    paths: CodexPaths,
    bundle_dir: Path,
    manifest: dict,
    *,
    import_mode: str = "best-effort",
) -> ImportResult:
    """Restore a non-Codex session file from its bundle back to the home tree."""
    if import_mode not in _AGENT_SESSION_MODES:
        raise ToolkitError(f"Unsupported import mode: {import_mode}")
    agent = manifest.get("AGENT", "")
    if agent not in NON_CODEX_SESSION_AGENTS:
        raise ToolkitError(f"Manifest does not describe a non-Codex agent session: {bundle_dir}")
    session_id = validate_session_id(manifest["SESSION_ID"])
    relative_posix = normalize_relative_path(manifest.get("RELATIVE_PATH", ""))
    _assert_agent_relative_path(relative_posix, agent)

    bundle_dir = restrict_to_local_bundle_workspace(paths, bundle_dir, "Agent session bundle")
    source_file = bundle_dir / Path(*relative_posix.split("/"))
    _assert_within(source_file, bundle_dir)
    if not source_file.is_file():
        raise ToolkitError(f"Missing bundled {agent} session file: {source_file}")

    target_file = paths.home / Path(*relative_posix.split("/"))
    warnings: List[OperationWarning] = []
    status = "restored"
    if target_file.exists():
        if _sha256(target_file) == _sha256(source_file):
            status = "already_present"
        elif import_mode == "skip":
            status = "skipped"
        elif import_mode == "overwrite":
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
        elif import_mode == "strict":
            raise ToolkitError(f"Agent session conflict (not overwriting): {session_id} at {target_file}")
        else:
            status = "conflict_skipped"
            warnings.append(OperationWarning(
                code="agent_session_conflict_skipped",
                session_id=session_id,
                path=str(target_file),
                related_path=str(source_file),
                detail="target exists with different content",
            ))
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)

    return ImportResult(
        session_id=session_id,
        bundle_dir=bundle_dir,
        relative_path=relative_posix,
        import_mode=import_mode,
        rollout_action=status,
        session_kind="cli",
        session_cwd=manifest.get("SESSION_CWD", ""),
        desktop_registered=False,
        desktop_registration_target="",
        thread_row_upserted=False,
        target_desktop_model_provider="",
        warnings=warnings,
    )


def count_agent_session_files(paths: CodexPaths, agent: str) -> int:
    return len(iter_agent_session_files(paths.home, agent))


def _assert_agent_relative_path(relative_posix: str, agent: str) -> None:
    parts = relative_posix.split("/")
    if not relative_posix or relative_posix.startswith("/") or ".." in parts:
        raise ToolkitError(f"Unsafe relative path in manifest: {relative_posix}")
    if parts[0] != _agent_home_dir(agent):
        raise ToolkitError(f"Relative path does not match agent {agent}: {relative_posix}")


def _agent_home_dir(agent: str) -> str:
    if agent == "claude":
        return ".claude"
    if agent == "pi":
        return ".pi"
    if agent == "zcode":
        return ".zcode"
    raise ToolkitError(f"Unsupported session agent: {agent}")


def _assert_within(path: Path, base_dir: Path) -> None:
    base_real = Path(base_dir).resolve()
    try:
        path.resolve().relative_to(base_real)
    except ValueError as exc:
        raise ToolkitError(f"Path escapes bundle directory: {path}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime_iso(path: Path) -> str:
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
