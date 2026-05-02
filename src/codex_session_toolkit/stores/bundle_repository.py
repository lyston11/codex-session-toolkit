"""Bundle directory resolution helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Mapping

from ..errors import ToolkitError
from ..paths import CodexPaths
from ..validation import load_manifest, validate_session_id
from .bundle_scanner import (
    bundle_directory_sort_key,
    collect_known_bundle_summaries,
    iter_bundle_directories_under_root,
)


def resolve_bundle_dir(bundle_root: Path, session_id: str) -> Path:
    session_id = validate_session_id(session_id)
    bundle_root = Path(bundle_root).expanduser()

    direct_candidate = bundle_root / session_id
    candidates: List[Path] = []
    if (direct_candidate / "manifest.env").is_file():
        candidates.append(direct_candidate)

    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        if bundle_dir in candidates:
            continue
        manifest_file = bundle_dir / "manifest.env"
        candidate_session_id = ""
        try:
            candidate_session_id = load_manifest(manifest_file).get("SESSION_ID", "")
        except Exception:
            pass
        if bundle_dir.name == session_id or candidate_session_id == session_id:
            candidates.append(bundle_dir)

    if not candidates:
        raise ToolkitError(f"Bundle not found for session id: {session_id}")

    candidates.sort(key=bundle_directory_sort_key, reverse=True)
    return candidates[0]


def resolve_known_bundle_dir(
    paths: CodexPaths,
    session_id: str,
    *,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
) -> Path:
    session_id = validate_session_id(session_id)
    candidates = [
        summary.bundle_dir
        for summary in collect_known_bundle_summaries(
            paths,
            source_group=source_group,
            machine_filter=machine_filter,
            export_group_filter=export_group_filter,
            limit=None,
        )
        if summary.session_id == session_id
    ]
    if not candidates:
        raise ToolkitError(f"Bundle not found for session id: {session_id}")

    candidates.sort(key=bundle_directory_sort_key, reverse=True)
    return candidates[0]


def write_batch_export_manifest(
    manifest_file: Path,
    metadata: Mapping[str, object],
    session_ids: Iterable[str],
) -> Path:
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    with manifest_file.open("w", encoding="utf-8") as fh:
        for key, value in metadata.items():
            fh.write(f"# {key}={value}\n")
        for session_id in session_ids:
            fh.write(str(session_id) + "\n")
    return manifest_file
