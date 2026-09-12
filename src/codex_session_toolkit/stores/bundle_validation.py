"""Bundle validation helpers."""

from __future__ import annotations

from pathlib import Path

from ..errors import ToolkitError
from ..models import BundleValidationResult
from ..support import ensure_path_within_dir
from ..validation import (
    load_manifest,
    normalize_relative_path,
    validate_jsonl_file,
    validate_relative_path,
    validate_session_id,
)


def validate_bundle_directory(
    bundle_dir: Path,
    *,
    source_group: str = "",
) -> BundleValidationResult:
    bundle_dir = Path(bundle_dir).expanduser()
    manifest_file = bundle_dir / "manifest.env"
    bundle_history = bundle_dir / "history.jsonl"

    try:
        if not manifest_file.is_file():
            raise ToolkitError(f"Missing manifest: {manifest_file}")

        manifest = load_manifest(manifest_file)
        session_id = validate_session_id(manifest.get("SESSION_ID", ""))
        agent = manifest.get("AGENT", "")
        if agent and agent != "codex":
            return _validate_agent_bundle_directory(
                bundle_dir,
                manifest,
                session_id=session_id,
                agent=agent,
                source_group=source_group,
            )
        rollout_id = validate_session_id(manifest.get("ROLLOUT_ID", "") or session_id)
        relative_path = validate_relative_path(manifest.get("RELATIVE_PATH", ""), rollout_id)

        source_session = bundle_dir / "codex" / Path(*relative_path.split("/"))
        ensure_path_within_dir(source_session, bundle_dir / "codex", "Bundled session file")
        validate_jsonl_file(source_session, "Bundled session file", "session", session_id)
        if bundle_history.exists():
            validate_jsonl_file(bundle_history, "Bundled history file", "history", session_id)

        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=session_id,
            is_valid=True,
            message="OK",
        )
    except Exception as exc:
        fallback_session_id = bundle_dir.name
        try:
            if manifest_file.is_file():
                fallback_session_id = load_manifest(manifest_file).get("SESSION_ID", bundle_dir.name) or bundle_dir.name
        except Exception:
            pass
        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=fallback_session_id,
            is_valid=False,
            message=str(exc),
        )


def _validate_agent_bundle_directory(
    bundle_dir: Path,
    manifest: dict,
    *,
    session_id: str,
    agent: str,
    source_group: str,
) -> BundleValidationResult:
    """Agent session bundles hold the file at its home-relative path."""
    try:
        relative_posix = normalize_relative_path(manifest.get("RELATIVE_PATH", ""))
        parts = relative_posix.split("/")
        if not relative_posix or relative_posix.startswith("/") or ".." in parts:
            raise ToolkitError(f"Unsafe relative path in manifest: {relative_posix}")
        if parts[0] != {"claude": ".claude", "pi": ".pi", "zcode": ".zcode"}.get(agent, ""):
            raise ToolkitError(f"Relative path does not match agent {agent}: {relative_posix}")
        source_session = bundle_dir / Path(*parts)
        ensure_path_within_dir(source_session, bundle_dir, "Bundled session file")
        if not source_session.is_file():
            raise ToolkitError(f"Missing bundled session file: {source_session}")
        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=session_id,
            is_valid=True,
            message=f"OK ({agent})",
        )
    except Exception as exc:
        return BundleValidationResult(
            source_group=source_group,
            bundle_dir=bundle_dir,
            session_id=session_id or bundle_dir.name,
            is_valid=False,
            message=str(exc),
        )
