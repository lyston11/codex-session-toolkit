"""Helpers for discovering paginated rollout ancestry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

from ..errors import ToolkitError
from ..paths import CodexPaths
from .session_files import iter_session_files, session_id_from_filename
from .session_parser import parse_session_summary_file


LINEAGE_FILENAME = "history_lineage.json"
LINEAGE_PROJECTION_DIR = "thread_history"


def collect_session_lineage(paths: CodexPaths, session_file: Path) -> List[Path]:
    """Return the current rollout followed by its history_base ancestors."""
    by_rollout_id = {
        rollout_id: path
        for path in iter_session_files(paths)
        if (rollout_id := session_id_from_filename(path))
    }
    lineage: List[Path] = []
    seen: set[str] = set()
    current = session_file
    while current is not None:
        rollout_id = session_id_from_filename(current)
        if not rollout_id or rollout_id in seen:
            break
        seen.add(rollout_id)
        lineage.append(current)
        current = _parent_rollout_path(current, by_rollout_id)
    return lineage


def build_lineage_manifest(paths: Iterable[Path], root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in paths:
        rollout_id = session_id_from_filename(path)
        if not rollout_id:
            continue
        try:
            relative_path = path.relative_to(root)
        except ValueError:
            continue
        entries.append({"rollout_id": rollout_id, "relative_path": relative_path.as_posix()})
    return entries


def write_lineage_manifest(path: Path, entries: list[dict[str, str]]) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def read_lineage_manifest(path: Path) -> list[dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolkitError(f"Invalid history lineage manifest: {path}") from exc
    if not isinstance(value, list):
        raise ToolkitError(f"Invalid history lineage manifest: {path}")
    entries: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rollout_id = item.get("rollout_id")
        relative_path = item.get("relative_path")
        if isinstance(rollout_id, str) and isinstance(relative_path, str):
            entries.append({"rollout_id": rollout_id, "relative_path": relative_path})
    return entries


def _parent_rollout_path(path: Path, by_rollout_id: dict[str, Path]) -> Optional[Path]:
    try:
        metadata = parse_session_summary_file(path).session_meta
    except ToolkitError:
        return None
    history_base = metadata.get("history_base")
    if not isinstance(history_base, dict):
        return None
    parent_rollout_id = history_base.get("thread_id")
    if not isinstance(parent_rollout_id, str):
        return None
    return by_rollout_id.get(parent_rollout_id)
