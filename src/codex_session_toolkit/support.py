"""Shared utility helpers."""

from __future__ import annotations

import ntpath
import os
import platform
import posixpath
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from .errors import ToolkitError
from .paths import CodexPaths


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def extract_iso_timestamp(raw_value: str) -> str:
    if not raw_value:
        return ""
    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", raw_value)
    return match.group(0) if match else ""


def normalize_iso(raw_value: str) -> str:
    return extract_iso_timestamp(raw_value)


def iso_to_epoch(raw_value: str) -> int:
    normalized = normalize_iso(raw_value)
    if not normalized:
        return 0
    try:
        return int(datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def export_batch_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def machine_label_to_key(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or "").strip()).strip("-._")
    return normalized or "unknown-machine"


def project_label_to_key(label: str) -> str:
    normalized = machine_label_to_key(label)
    return normalized if normalized != "unknown-machine" else "root"


def detect_machine_label() -> str:
    raw = (
        os.environ.get("CST_MACHINE_LABEL")
        or os.environ.get("CSC_MACHINE_LABEL")
        or os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or platform.node()
        or "unknown-machine"
    )
    return raw.strip() or "unknown-machine"


def detect_machine_key() -> str:
    return machine_label_to_key(detect_machine_label())


def build_machine_bundle_root(bundle_root: Path, machine_key: Optional[str] = None) -> Path:
    resolved_key = machine_key or detect_machine_key()
    return Path(bundle_root).expanduser() / resolved_key


def build_single_export_root(bundle_root: Path, machine_key: Optional[str] = None) -> Path:
    return build_machine_bundle_root(bundle_root, machine_key) / "single" / export_batch_slug()


def build_batch_export_root(bundle_root: Path, archive_group: str, machine_key: Optional[str] = None) -> Path:
    return build_machine_bundle_root(bundle_root, machine_key) / archive_group / export_batch_slug()


def build_project_export_root(bundle_root: Path, project_key: str, machine_key: Optional[str] = None) -> Path:
    return build_machine_bundle_root(bundle_root, machine_key) / "project" / project_key / export_batch_slug()


def classify_session_kind(source_name: str, originator_name: str) -> str:
    if source_name == "vscode":
        return "desktop"
    if source_name == "cli":
        return "cli"
    if "Desktop" in originator_name:
        return "desktop"
    if originator_name in {"codex_cli_rs", "codex-tui"} or originator_name.startswith("codex_cli"):
        return "cli"
    return "unknown"


def ensure_path_within_dir(target_path: Path, base_dir: Path, label: str) -> None:
    try:
        target_real = os.path.realpath(target_path)
        base_real = os.path.realpath(base_dir)
        common = os.path.commonpath([target_real, base_real])
    except ValueError:
        common = ""

    if common == base_real:
        return

    raise ToolkitError(f"{label} escapes base directory: {target_path}")


def restrict_to_local_bundle_workspace(paths: CodexPaths, target_path: Path, label: str) -> Path:
    workspace = paths.local_bundle_workspace.expanduser()
    target_path = Path(target_path).expanduser()
    ensure_path_within_dir(target_path, workspace, label)
    return target_path


def normalize_bundle_root(
    paths: CodexPaths,
    bundle_root: Optional[Path],
    default_root: Path,
    *,
    label: str = "Bundle root",
) -> Path:
    target_root = Path(bundle_root or default_root).expanduser()
    return restrict_to_local_bundle_workspace(paths, target_root, label)


def _strip_wrapping_quotes(raw_value: str) -> str:
    text = (raw_value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return text.strip()


def _looks_like_windows_path(raw_value: str) -> bool:
    return bool(WINDOWS_DRIVE_RE.match(raw_value or "")) or "\\" in (raw_value or "")


def normalize_project_path(project_path: str) -> str:
    raw_value = _strip_wrapping_quotes(project_path)
    if not raw_value:
        return ""

    expanded = os.path.expanduser(raw_value)
    if _looks_like_windows_path(expanded):
        return ntpath.normpath(expanded)

    candidate = Path(expanded)
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve(strict=False)
    return posixpath.normpath(str(candidate))


def _normalized_path_parts(raw_value: str) -> tuple[str, ...]:
    normalized = normalize_project_path(raw_value)
    if not normalized:
        return ()

    if _looks_like_windows_path(normalized):
        return tuple(part.lower() for part in PureWindowsPath(normalized).parts)
    return PurePosixPath(normalized).parts


def project_label_from_path(project_path: str) -> str:
    normalized = normalize_project_path(project_path)
    if not normalized:
        return ""

    if _looks_like_windows_path(normalized):
        pure_path = PureWindowsPath(normalized)
        if pure_path.name:
            return pure_path.name
        if pure_path.drive:
            return pure_path.drive.rstrip(":\\/") + "-drive"
        return "root"

    pure_path = PurePosixPath(normalized)
    if pure_path.name:
        return pure_path.name
    return "root"


def project_filter_to_key(project_filter: str) -> str:
    normalized = _strip_wrapping_quotes(project_filter)
    if not normalized:
        return ""
    if "/" in normalized or "\\" in normalized:
        return project_label_to_key(project_label_from_path(normalized))
    return project_label_to_key(normalized)


def project_path_matches(session_cwd: str, project_path: str) -> bool:
    session_parts = _normalized_path_parts(session_cwd)
    project_parts = _normalized_path_parts(project_path)
    if not session_parts or not project_parts:
        return False

    session_is_windows = _looks_like_windows_path(normalize_project_path(session_cwd))
    project_is_windows = _looks_like_windows_path(normalize_project_path(project_path))
    if session_is_windows != project_is_windows:
        return False

    if len(session_parts) < len(project_parts):
        return False
    return session_parts[: len(project_parts)] == project_parts


def remap_session_cwd_to_project(session_cwd: str, source_project_path: str, target_project_path: str) -> str:
    normalized_target = normalize_project_path(target_project_path)
    if not normalized_target:
        return ""

    normalized_session = normalize_project_path(session_cwd)
    normalized_source = normalize_project_path(source_project_path)
    if not normalized_session:
        return normalized_target
    if not normalized_source or not project_path_matches(normalized_session, normalized_source):
        return normalized_target

    source_is_windows = _looks_like_windows_path(normalized_source)
    if source_is_windows:
        source_parts = PureWindowsPath(normalized_source).parts
        session_parts = PureWindowsPath(normalized_session).parts
    else:
        source_parts = PurePosixPath(normalized_source).parts
        session_parts = PurePosixPath(normalized_session).parts
    relative_parts = session_parts[len(source_parts) :]

    if _looks_like_windows_path(normalized_target):
        target_path = PureWindowsPath(normalized_target).joinpath(*relative_parts)
        return ntpath.normpath(str(target_path))

    target_path = PurePosixPath(normalized_target).joinpath(*relative_parts)
    return posixpath.normpath(str(target_path))


def default_local_project_target(project_label: str, source_project_path: str) -> tuple[str, str]:
    normalized_source = normalize_project_path(source_project_path)
    if normalized_source and Path(normalized_source).is_dir():
        return normalized_source, "same_path"

    resolved_label = (project_label or project_label_from_path(source_project_path) or "project").strip() or "project"
    candidate_roots = [Path.cwd()]
    if Path.cwd().parent != Path.cwd():
        candidate_roots.append(Path.cwd().parent)

    seen_candidates: set[str] = set()
    for root in candidate_roots:
        candidate = normalize_project_path(str(root / resolved_label))
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        if Path(candidate).is_dir():
            return candidate, "same_name"

    fallback_root = candidate_roots[-1] if candidate_roots else Path.cwd()
    fallback_path = normalize_project_path(str(fallback_root / resolved_label))
    return fallback_path, "missing"


def nearest_existing_parent(path_str: str) -> str:
    if not path_str:
        return ""
    path = Path(path_str).expanduser()
    while True:
        if path.exists():
            return str(path)
        if path.parent == path:
            return ""
        path = path.parent


def backup_file(code_dir: Path, backup_root: Path, backed_up: set[str], path: Path, *, enabled: bool) -> None:
    if not enabled or not path.exists():
        return
    resolved = str(path.resolve())
    if resolved in backed_up:
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.relative_to(code_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    backed_up.add(resolved)
