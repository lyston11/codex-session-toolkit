"""Bundle repository scanning and summary helpers."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from ..errors import ToolkitError
from ..models import BundleSummary
from ..paths import CodexPaths
from ..support import iso_to_epoch
from ..validation import load_manifest
from .bundle_layout import (
    LEGACY_MACHINE_KEY,
    LEGACY_ROOT_DIRS,
    canonical_export_group_name,
    infer_bundle_export_group,
    infer_bundle_machine,
    infer_bundle_project_metadata,
    source_group_allows_export_group,
)


def iter_bundle_directories_under_root(bundle_root: Path) -> List[Path]:
    bundle_root = Path(bundle_root).expanduser()
    if not bundle_root.is_dir():
        return []

    bundle_dirs: List[Path] = []
    seen_dirs: set[Path] = set()
    for manifest_file in bundle_root.rglob("manifest.env"):
        bundle_dir = manifest_file.parent
        try:
            relative_parts = bundle_dir.relative_to(bundle_root).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative_parts):
            continue
        if bundle_dir not in seen_dirs:
            bundle_dirs.append(bundle_dir)
            seen_dirs.add(bundle_dir)
    bundle_dirs.sort()
    return bundle_dirs


def bundle_directory_sort_key(bundle_dir: Path) -> Tuple[int, int, str]:
    manifest_file = bundle_dir / "manifest.env"
    exported_epoch = 0
    try:
        manifest = load_manifest(manifest_file)
        exported_epoch = iso_to_epoch(manifest.get("EXPORTED_AT", "") or manifest.get("UPDATED_AT", ""))
    except Exception:
        pass
    try:
        modified_ns = bundle_dir.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return (exported_epoch, modified_ns, str(bundle_dir))


def collect_bundle_summaries(
    bundle_root: Path,
    *,
    source_group: str = "",
    pattern: str = "",
    machine_filter: str = "",
    export_group_filter: str = "",
    limit: Optional[int] = None,
) -> List[BundleSummary]:
    bundle_root = Path(bundle_root).expanduser()
    if not bundle_root.is_dir():
        return []
    export_group_filter = canonical_export_group_name(export_group_filter)

    summaries: List[BundleSummary] = []
    project_batch_cache: dict[Path, tuple[str, str]] = {}
    for bundle_dir in iter_bundle_directories_under_root(bundle_root):
        try:
            relative_parts = bundle_dir.relative_to(bundle_root).parts
        except ValueError:
            relative_parts = ()
        if bundle_root.name == "codex_sessions" and relative_parts and relative_parts[0] in LEGACY_ROOT_DIRS:
            continue
        manifest_file = bundle_dir / "manifest.env"
        try:
            manifest = load_manifest(manifest_file)
        except ToolkitError:
            continue
        machine_key, machine_label = infer_bundle_machine(bundle_root, bundle_dir, manifest)
        if machine_filter and machine_key != machine_filter:
            continue
        export_group, export_group_label = infer_bundle_export_group(bundle_root, bundle_dir)
        if not source_group_allows_export_group(source_group, export_group):
            continue
        if export_group_filter and export_group != export_group_filter:
            continue
        project_key, project_label, project_path = infer_bundle_project_metadata(
            bundle_root,
            bundle_dir,
            export_group,
            project_batch_cache,
        )

        summary = BundleSummary(
            source_group=source_group,
            session_id=manifest.get("SESSION_ID", ""),
            bundle_dir=bundle_dir,
            relative_path=manifest.get("RELATIVE_PATH", ""),
            updated_at=manifest.get("UPDATED_AT", ""),
            exported_at=manifest.get("EXPORTED_AT", ""),
            thread_name=manifest.get("THREAD_NAME", ""),
            session_cwd=manifest.get("SESSION_CWD", ""),
            session_kind=manifest.get("SESSION_KIND", ""),
            source_machine=machine_label,
            source_machine_key=machine_key,
            export_group=export_group,
            export_group_label=export_group_label,
            project_key=project_key,
            project_label=project_label,
            project_path=project_path,
        )
        if pattern:
            combined = " ".join(
                [
                    summary.session_id,
                    summary.relative_path,
                    summary.thread_name,
                    summary.session_cwd,
                    summary.session_kind,
                    summary.source_machine,
                    summary.source_machine_key,
                    summary.export_group,
                    summary.export_group_label,
                    summary.project_key,
                    summary.project_label,
                    summary.project_path,
                    str(summary.bundle_dir),
                ]
            )
            if pattern not in combined:
                continue

        summaries.append(summary)
        if limit is not None and len(summaries) >= max(1, limit):
            break

    return summaries


def collect_known_bundle_summaries(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
) -> List[BundleSummary]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")
    export_group_filter = canonical_export_group_name(export_group_filter)

    summaries: List[BundleSummary] = []
    roots: List[Tuple[str, Path, str]] = [("primary", paths.default_bundle_root, source_group)]
    if source_group in {"all", "bundle"}:
        roots.append(("legacy-bundle", paths.legacy_bundle_root, "bundle"))
    if source_group in {"all", "desktop"}:
        roots.append(("legacy-desktop", paths.legacy_desktop_bundle_root, "desktop"))

    seen_roots: set[Path] = set()
    for _, root_path, root_filter in roots:
        resolved_root = Path(root_path).expanduser()
        if resolved_root in seen_roots:
            continue
        seen_roots.add(resolved_root)
        summaries.extend(
            collect_bundle_summaries(
                resolved_root,
                source_group=root_filter,
                pattern=pattern,
                machine_filter=machine_filter,
                export_group_filter=export_group_filter,
            )
        )

    summaries.sort(
        key=lambda item: (iso_to_epoch(item.updated_at or item.exported_at), item.session_id),
        reverse=True,
    )
    if limit is not None:
        return summaries[: max(1, limit)]
    return summaries


def latest_distinct_bundle_summaries(summaries: List[BundleSummary]) -> List[BundleSummary]:
    latest: List[BundleSummary] = []
    seen_keys: set[tuple[str, str]] = set()

    for bundle in sorted(
        summaries,
        key=lambda item: (iso_to_epoch(item.updated_at or item.exported_at), str(item.bundle_dir)),
        reverse=True,
    ):
        dedupe_key = (
            bundle.source_machine_key or LEGACY_MACHINE_KEY,
            bundle.session_id,
        )
        if dedupe_key in seen_keys:
            continue
        latest.append(bundle)
        seen_keys.add(dedupe_key)

    return latest


def iter_known_bundle_directories(
    paths: CodexPaths,
    *,
    source_group: str = "all",
) -> List[Tuple[str, Path]]:
    if source_group not in {"all", "bundle", "desktop"}:
        raise ToolkitError(f"Unsupported source_group: {source_group}")

    roots: List[Tuple[str, Path, str]] = [("primary", paths.default_bundle_root, source_group)]
    if source_group in {"all", "bundle"}:
        roots.append(("legacy-bundle", paths.legacy_bundle_root, "bundle"))
    if source_group in {"all", "desktop"}:
        roots.append(("legacy-desktop", paths.legacy_desktop_bundle_root, "desktop"))

    bundle_dirs: List[Tuple[str, Path]] = []
    seen_roots: set[Path] = set()
    for group_name, root, root_filter in roots:
        root = Path(root).expanduser()
        if root in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root)
        for path in iter_bundle_directories_under_root(root):
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                relative_parts = ()
            if root.name == "codex_sessions" and relative_parts and relative_parts[0] in LEGACY_ROOT_DIRS:
                continue
            export_group, _ = infer_bundle_export_group(root, path)
            if not source_group_allows_export_group(root_filter, export_group):
                continue
            bundle_dirs.append((group_name, path))
    return bundle_dirs
