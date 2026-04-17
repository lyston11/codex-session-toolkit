"""Bundle validation helpers."""

from __future__ import annotations

from pathlib import Path

from ..errors import ToolkitError
from ..models import BundleValidationResult
from ..support import ensure_path_within_dir
from ..validation import (
    load_manifest,
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
        relative_path = validate_relative_path(manifest.get("RELATIVE_PATH", ""), session_id)

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
