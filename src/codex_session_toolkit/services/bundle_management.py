"""Bundle management use cases."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

from ..errors import ToolkitError
from ..models import BundleDeleteResult, BundleSummary
from ..paths import CodexPaths
from ..stores.bundle_scanner import iter_known_bundle_directories
from ..support import ensure_path_within_dir


def delete_bundle_summaries(
    paths: CodexPaths,
    bundles: Sequence[BundleSummary],
    *,
    dry_run: bool = False,
) -> list[BundleDeleteResult]:
    results: list[BundleDeleteResult] = []
    seen_dirs: set[str] = set()
    for bundle in bundles:
        dir_key = _path_key(bundle.bundle_dir)
        if dir_key in seen_dirs:
            continue
        seen_dirs.add(dir_key)
        try:
            results.append(_delete_bundle_summary(paths, bundle, dry_run=dry_run))
        except ToolkitError as exc:
            results.append(
                BundleDeleteResult(
                    bundle_dir=bundle.bundle_dir.expanduser(),
                    session_id=bundle.session_id,
                    dry_run=dry_run,
                    error=str(exc),
                )
            )
    return results


def _delete_bundle_summary(paths: CodexPaths, bundle: BundleSummary, *, dry_run: bool) -> BundleDeleteResult:
    bundle_dir = bundle.bundle_dir.expanduser()
    _assert_known_bundle_dir(paths, bundle_dir)

    if not dry_run:
        shutil.rmtree(bundle_dir)

    return BundleDeleteResult(
        bundle_dir=bundle_dir,
        session_id=bundle.session_id,
        dry_run=dry_run,
        deleted=not dry_run,
    )


def _assert_known_bundle_dir(paths: CodexPaths, bundle_dir: Path) -> None:
    known_roots = _known_bundle_roots(paths)
    for root in known_roots:
        try:
            ensure_path_within_dir(bundle_dir, root, "Bundle directory")
        except ToolkitError:
            continue
        if not bundle_dir.is_dir():
            raise ToolkitError(f"Bundle directory not found: {bundle_dir}")
        if not (bundle_dir / "manifest.env").is_file():
            raise ToolkitError(f"Refusing to delete invalid Bundle directory: {bundle_dir}")
        return
    raise ToolkitError(f"Refusing to delete Bundle outside known bundle workspaces: {bundle_dir}")


def _known_bundle_roots(paths: CodexPaths) -> list[Path]:
    roots = [
        paths.local_bundle_workspace,
        paths.legacy_session_bundle_workspace,
        paths.legacy_bundle_root,
        paths.legacy_desktop_bundle_root,
    ]
    for _, bundle_dir in iter_known_bundle_directories(paths, source_group="all"):
        roots.append(bundle_dir.parent)

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        root_key = _path_key(root)
        if root_key not in seen:
            seen.add(root_key)
            unique_roots.append(root.expanduser())
    return unique_roots


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.expanduser())
