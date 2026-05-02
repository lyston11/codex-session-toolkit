"""Skill sidecar models and manifest serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from ..models import OperationWarning


SKILLS_MANIFEST_FILENAME = "skills_manifest.json"
SKILLS_DIR_NAME = "skills"
SKILLS_SCHEMA_VERSION = 1

_VALID_LOCATION_KINDS = {"custom", "system", "runtime"}
_VALID_DEPENDENCY_LEVELS = {"available", "required", "uncertain"}


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    skill_file: str
    source_root: str
    relative_dir: str
    location_kind: str
    used: bool = False
    usage_count: int = 0
    bundled: bool = False
    bundle_path: str = ""
    content_hash: str = ""
    dependency_level: str = "available"
    evidence: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillsManifest:
    schema_version: int = SKILLS_SCHEMA_VERSION
    available_skill_count: int = 0
    used_skill_count: int = 0
    bundled_skill_count: int = 0
    skills: Tuple[SkillDescriptor, ...] = ()


@dataclass(frozen=True)
class SkillRestoreResult:
    name: str
    source_root: str
    relative_dir: str
    status: str
    target_path: str
    content_hash: str = ""


@dataclass(frozen=True)
class SkillsBundleResult:
    manifest: SkillsManifest
    warnings: Tuple[OperationWarning, ...] = ()


@dataclass(frozen=True)
class SkillsRestoreOutcome:
    results: Tuple[SkillRestoreResult, ...] = ()
    warnings: Tuple[OperationWarning, ...] = ()


def write_skills_manifest(manifest: SkillsManifest, bundle_dir: Path) -> Path:
    data = {
        "schema_version": manifest.schema_version,
        "available_skill_count": manifest.available_skill_count,
        "used_skill_count": manifest.used_skill_count,
        "bundled_skill_count": manifest.bundled_skill_count,
        "skills": [
            {
                "name": s.name,
                "skill_file": s.skill_file,
                "source_root": s.source_root,
                "relative_dir": s.relative_dir,
                "location_kind": s.location_kind,
                "used": s.used,
                "usage_count": s.usage_count,
                "bundled": s.bundled,
                "bundle_path": s.bundle_path,
                "content_hash": s.content_hash,
                "dependency_level": s.dependency_level,
                "evidence": list(s.evidence),
            }
            for s in manifest.skills
        ],
    }
    path = bundle_dir / SKILLS_MANIFEST_FILENAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_skills_manifest(bundle_dir: Path) -> Optional[SkillsManifest]:
    path = bundle_dir / SKILLS_MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if data.get("schema_version") != SKILLS_SCHEMA_VERSION:
        return None
    skills_payload = data.get("skills", [])
    if not isinstance(skills_payload, list):
        return None

    descriptors: list[SkillDescriptor] = []
    for raw_skill in skills_payload:
        descriptor = _deserialize_skill_descriptor(raw_skill)
        if descriptor is None:
            return None
        descriptors.append(descriptor)
    return SkillsManifest(
        schema_version=data.get("schema_version", SKILLS_SCHEMA_VERSION),
        available_skill_count=_non_negative_int_or_default(data.get("available_skill_count"), len(descriptors)),
        used_skill_count=_non_negative_int_or_default(data.get("used_skill_count"), 0),
        bundled_skill_count=_non_negative_int_or_default(data.get("bundled_skill_count"), 0),
        skills=tuple(descriptors),
    )


def write_batch_skills_restore_report(
    report_path: Path,
    session_id: str,
    results: List[SkillRestoreResult],
) -> Path:
    existing: dict = {}
    if report_path.is_file():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    session_results = {
        "session_id": session_id,
        "total": len(results),
        "restored": sum(1 for r in results if r.status == "restored"),
        "already_present": sum(1 for r in results if r.status == "already_present"),
        "conflict_skipped": sum(1 for r in results if r.status == "conflict_skipped"),
        "missing": sum(1 for r in results if r.status == "missing"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "skills": [
            {
                "name": r.name,
                "source_root": r.source_root,
                "relative_dir": r.relative_dir,
                "status": r.status,
                "target_path": r.target_path,
                "content_hash": r.content_hash,
            }
            for r in results
        ],
    }
    sessions = existing.get("sessions", [])
    sessions.append(session_results)
    report = {
        "schema_version": 1,
        "total_sessions": len(sessions),
        "sessions": sessions,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report_path


def deduplicate_skill_manifests(manifests: List[SkillsManifest]) -> SkillsManifest:
    seen: Dict[Tuple[str, str], SkillDescriptor] = {}
    for manifest in manifests:
        for skill in manifest.skills:
            key = (skill.source_root, skill.relative_dir)
            existing = seen.get(key)
            if existing is None or skill.usage_count > existing.usage_count:
                seen[key] = skill
    skills = tuple(seen.values())
    used_count = sum(1 for s in skills if s.used)
    bundled_count = sum(1 for s in skills if s.bundled)
    return SkillsManifest(
        available_skill_count=len(skills),
        used_skill_count=used_count,
        bundled_skill_count=bundled_count,
        skills=skills,
    )


def is_safe_relative_posix_path(raw_path: str) -> bool:
    if not raw_path:
        return False
    path = PurePosixPath(raw_path)
    if path.is_absolute():
        return False
    parts = path.parts
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def is_valid_bundled_skill_path(bundle_path: str, *, source_root: str, relative_dir: str) -> bool:
    if not is_safe_relative_posix_path(bundle_path):
        return False
    bundle_parts = PurePosixPath(bundle_path).parts
    relative_parts = PurePosixPath(relative_dir).parts
    return (
        len(bundle_parts) >= 3
        and bundle_parts[0] == SKILLS_DIR_NAME
        and bundle_parts[1] == source_root
        and bundle_parts[2:] == relative_parts
    )


def _deserialize_skill_descriptor(raw_skill: object) -> Optional[SkillDescriptor]:
    if not isinstance(raw_skill, dict):
        return None

    name = _required_non_empty_string(raw_skill.get("name"))
    skill_file = _required_non_empty_string(raw_skill.get("skill_file"))
    source_root = _required_non_empty_string(raw_skill.get("source_root"))
    relative_dir = _required_non_empty_string(raw_skill.get("relative_dir"))
    location_kind = _required_non_empty_string(raw_skill.get("location_kind"))
    used = _required_bool(raw_skill.get("used"))
    usage_count = _required_non_negative_int(raw_skill.get("usage_count"))
    bundled = _required_bool(raw_skill.get("bundled"))
    bundle_path = _required_string(raw_skill.get("bundle_path"))
    content_hash = _required_string(raw_skill.get("content_hash"))
    dependency_level = _optional_dependency_level(raw_skill.get("dependency_level"), used)
    evidence = _optional_string_tuple(raw_skill.get("evidence"))

    if None in {
        name,
        skill_file,
        source_root,
        relative_dir,
        location_kind,
        used,
        usage_count,
        bundled,
        bundle_path,
        content_hash,
        dependency_level,
        evidence,
    }:
        return None
    assert name is not None
    assert skill_file is not None
    assert source_root is not None
    assert relative_dir is not None
    assert location_kind is not None
    assert used is not None
    assert usage_count is not None
    assert bundled is not None
    assert bundle_path is not None
    assert content_hash is not None
    assert dependency_level is not None
    assert evidence is not None

    if location_kind not in _VALID_LOCATION_KINDS:
        return None
    if dependency_level not in _VALID_DEPENDENCY_LEVELS:
        return None
    if not is_safe_relative_posix_path(relative_dir):
        return None
    if bundled and not is_valid_bundled_skill_path(bundle_path, source_root=source_root, relative_dir=relative_dir):
        return None
    if not bundled and bundle_path and not is_safe_relative_posix_path(bundle_path):
        return None

    return SkillDescriptor(
        name=name,
        skill_file=skill_file,
        source_root=source_root,
        relative_dir=relative_dir,
        location_kind=location_kind,
        used=used,
        usage_count=usage_count,
        bundled=bundled,
        bundle_path=bundle_path,
        content_hash=content_hash,
        dependency_level=dependency_level,
        evidence=evidence,
    )


def _required_string(value: object) -> Optional[str]:
    return value if isinstance(value, str) else None


def _required_non_empty_string(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    return value


def _required_bool(value: object) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _required_non_negative_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _optional_dependency_level(value: object, used: Optional[bool]) -> Optional[str]:
    if value is None:
        return "required" if used else "available"
    if not isinstance(value, str):
        return None
    return value


def _optional_string_tuple(value: object) -> Optional[Tuple[str, ...]]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        items.append(item)
    return tuple(items)


def _non_negative_int_or_default(value: object, default: int) -> int:
    parsed = _required_non_negative_int(value)
    return default if parsed is None else parsed
