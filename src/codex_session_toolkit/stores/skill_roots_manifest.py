"""Serialization helpers for Skill root snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .skill_roots import SkillRoot

ROOTS_MANIFEST_FILENAME = "roots_manifest.json"


def write_roots_manifest(bundle_dir: Path, roots: Iterable[SkillRoot]) -> Path:
    payload = {
        "schema_version": 1,
        "roots": [
            {
                "id": root.root_id,
                "label": root.label,
                "kind": root.kind,
                "relative_path": root.relative_path,
                "enabled": root.enabled,
            }
            for root in roots
        ],
    }
    path = bundle_dir / ROOTS_MANIFEST_FILENAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
