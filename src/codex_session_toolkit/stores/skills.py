"""Skill discovery, bundling, and restoration for session bundles."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from ..errors import ToolkitError
from ..models import OperationWarning

SKILLS_MANIFEST_FILENAME = "skills_manifest.json"
SKILLS_DIR_NAME = "skills"
SKILL_MD_NAME = "SKILL.md"
SKILLS_SCHEMA_VERSION = 1

_SKILL_LINE_RE = re.compile(r"^- (\S+?):\s+(.+?)\s+\(file:\s+(.+?)\)\s*$")
_AGENTS_MARKER = "/.agents/skills/"
_CODEX_MARKER = "/.codex/skills/"
_SYSTEM_PREFIX = ".system/"
_RUNTIME_PREFIX = "codex-primary-runtime/"
_RESTORABLE_SKILL_ROOTS = {"agents", "codex"}
_VALID_LOCATION_KINDS = {"custom", "system", "runtime"}


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


def infer_skill_source_root(skill_file_path: str) -> Tuple[str, str]:
    if _AGENTS_MARKER in skill_file_path:
        idx = skill_file_path.index(_AGENTS_MARKER) + len(_AGENTS_MARKER)
        relative = skill_file_path[idx:]
        relative = relative.rsplit("/", 1)[0] if "/" in relative else relative
        return "agents", relative
    if _CODEX_MARKER in skill_file_path:
        idx = skill_file_path.index(_CODEX_MARKER) + len(_CODEX_MARKER)
        relative = skill_file_path[idx:]
        relative = relative.rsplit("/", 1)[0] if "/" in relative else relative
        return "codex", relative
    return "unknown", ""


def classify_skill_location(relative_dir: str) -> str:
    if relative_dir.startswith(_SYSTEM_PREFIX):
        return "system"
    if relative_dir.startswith(_RUNTIME_PREFIX):
        return "runtime"
    return "custom"


def parse_skills_from_session(session_file: Path) -> SkillsManifest:
    skills_block = _extract_skills_block(session_file)
    if not skills_block:
        return SkillsManifest()

    parsed = _parse_available_skills(skills_block)
    if not parsed:
        return SkillsManifest()

    usage_map = _detect_skill_usage(session_file, [s[0] for s in parsed])

    descriptors: list[SkillDescriptor] = []
    used_count = 0
    for name, description, skill_file in parsed:
        source_root, relative_dir = infer_skill_source_root(skill_file)
        location_kind = classify_skill_location(relative_dir)
        count = usage_map.get(name, 0)
        is_used = count > 0
        if is_used:
            used_count += 1
        descriptors.append(SkillDescriptor(
            name=name,
            skill_file=skill_file,
            source_root=source_root,
            relative_dir=relative_dir,
            location_kind=location_kind,
            used=is_used,
            usage_count=count,
        ))

    return SkillsManifest(
        available_skill_count=len(descriptors),
        used_skill_count=used_count,
        bundled_skill_count=0,
        skills=tuple(descriptors),
    )


def compute_skill_directory_hash(skill_dir: Path) -> str:
    if not skill_dir.is_dir():
        return ""
    parts: list[str] = []
    for fpath in sorted(skill_dir.rglob("*")):
        if not fpath.is_file():
            continue
        if any(p.startswith(".") for p in fpath.relative_to(skill_dir).parts):
            continue
        rel = fpath.relative_to(skill_dir).as_posix()
        file_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
        parts.append(f"{rel}\0{file_hash}")
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def bundle_skills(manifest: SkillsManifest, bundle_dir: Path) -> SkillsBundleResult:
    updated: list[SkillDescriptor] = []
    bundled_count = 0
    warnings: list[OperationWarning] = []
    for skill in manifest.skills:
        if skill.location_kind != "custom" or skill.bundled:
            updated.append(skill)
            continue
        if not _is_restorable_custom_skill(skill):
            updated.append(skill)
            warnings.append(
                OperationWarning(
                    code="skill_not_bundled",
                    name=skill.name,
                    source_root=skill.source_root,
                    relative_dir=skill.relative_dir,
                    path=str(Path(skill.skill_file).parent),
                    detail="unsupported skill location",
                )
            )
            continue
        source_dir = _resolve_skill_source_dir(skill)
        if not source_dir or not source_dir.is_dir():
            updated.append(skill)
            warnings.append(
                OperationWarning(
                    code="skill_not_bundled",
                    name=skill.name,
                    source_root=skill.source_root,
                    relative_dir=skill.relative_dir,
                    path=str(source_dir or Path(skill.skill_file).parent),
                    detail="source directory not found",
                )
            )
            continue
        dest_dir = bundle_dir / SKILLS_DIR_NAME / skill.source_root / skill.relative_dir
        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(source_dir, dest_dir)
            content_hash = compute_skill_directory_hash(source_dir)
        except OSError as exc:
            shutil.rmtree(dest_dir, ignore_errors=True)
            updated.append(skill)
            warnings.append(
                OperationWarning(
                    code="bundle_skill_failed",
                    name=skill.name,
                    source_root=skill.source_root,
                    relative_dir=skill.relative_dir,
                    path=str(source_dir),
                    related_path=str(dest_dir),
                    detail=str(exc),
                )
            )
            continue
        bundle_path = f"{SKILLS_DIR_NAME}/{skill.source_root}/{skill.relative_dir}"
        updated.append(SkillDescriptor(
            name=skill.name,
            skill_file=skill.skill_file,
            source_root=skill.source_root,
            relative_dir=skill.relative_dir,
            location_kind=skill.location_kind,
            used=skill.used,
            usage_count=skill.usage_count,
            bundled=True,
            bundle_path=bundle_path,
            content_hash=content_hash,
        ))
        bundled_count += 1

    return SkillsBundleResult(
        manifest=SkillsManifest(
            schema_version=manifest.schema_version,
            available_skill_count=manifest.available_skill_count,
            used_skill_count=manifest.used_skill_count,
            bundled_skill_count=bundled_count,
            skills=tuple(updated),
        ),
        warnings=tuple(warnings),
    )


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


def restore_skills(
    manifest: SkillsManifest,
    bundle_dir: Path,
    target_home: Path,
    *,
    skills_mode: str = "best-effort",
) -> SkillsRestoreOutcome:
    results: list[SkillRestoreResult] = []
    warnings: list[OperationWarning] = []
    for skill in manifest.skills:
        if not skill.bundled:
            if skill.location_kind == "custom":
                existing_dir = _first_existing_local_skill_dir(target_home, skill)
                if existing_dir is not None:
                    try:
                        existing_hash = compute_skill_directory_hash(existing_dir)
                    except OSError as exc:
                        results.append(_failed_restore_result(skill, existing_dir))
                        warnings.append(_restore_skill_failed_warning(skill, existing_dir, existing_dir, exc))
                        if skills_mode == "strict":
                            raise ToolkitError(f"Failed to inspect local skill {skill.name}: {exc}") from exc
                        continue
                    results.append(SkillRestoreResult(
                        name=skill.name,
                        source_root=skill.source_root,
                        relative_dir=skill.relative_dir,
                        status="already_present",
                        target_path=str(existing_dir),
                        content_hash=existing_hash,
                    ))
                    continue
                results.append(SkillRestoreResult(
                    name=skill.name,
                    source_root=skill.source_root,
                    relative_dir=skill.relative_dir,
                    status="missing",
                    target_path=_target_skill_dir(target_home, skill),
                ))
                if skills_mode == "strict":
                    raise ToolkitError(f"Missing custom skill: {skill.name}")
            continue

        target_dir = Path(_target_skill_dir(target_home, skill))
        source_dir = bundle_dir / skill.bundle_path

        invalid_source_warning = _validate_bundled_skill_source(skill, source_dir)
        if invalid_source_warning is not None:
            results.append(_failed_restore_result(skill, target_dir))
            warnings.append(invalid_source_warning)
            if skills_mode == "strict":
                raise ToolkitError(f"Invalid bundled skill: {skill.name}: {invalid_source_warning.detail}")
            continue

        try:
            bundle_hash = skill.content_hash or compute_skill_directory_hash(source_dir)
        except OSError as exc:
            results.append(_failed_restore_result(skill, target_dir))
            warnings.append(_restore_skill_failed_warning(skill, target_dir, source_dir, exc))
            if skills_mode == "strict":
                raise ToolkitError(f"Failed to restore skill {skill.name}: {exc}") from exc
            continue

        existing_dirs = _existing_local_skill_dirs(target_home, skill)
        existing_hashes: list[tuple[Path, str]] = []
        existing_hash_failed = False
        for existing_dir in existing_dirs:
            try:
                existing_hash = compute_skill_directory_hash(existing_dir)
            except OSError as exc:
                results.append(_failed_restore_result(skill, existing_dir))
                warnings.append(_restore_skill_failed_warning(skill, existing_dir, source_dir, exc))
                if skills_mode == "strict":
                    raise ToolkitError(f"Failed to restore skill {skill.name}: {exc}") from exc
                existing_hash_failed = True
                break
            existing_hashes.append((existing_dir, existing_hash))
        if existing_hash_failed:
            continue

        if existing_hashes:
            matched_existing = False
            for existing_dir, existing_hash in existing_hashes:
                if existing_hash == bundle_hash:
                    results.append(SkillRestoreResult(
                        name=skill.name,
                        source_root=skill.source_root,
                        relative_dir=skill.relative_dir,
                        status="already_present",
                        target_path=str(existing_dir),
                        content_hash=existing_hash,
                    ))
                    matched_existing = True
                    break
            if matched_existing:
                continue

            existing_dir, existing_hash = existing_hashes[0]
            if skills_mode == "overwrite":
                try:
                    _replace_skill_directory(source_dir, existing_dir)
                except OSError as exc:
                    results.append(_failed_restore_result(skill, existing_dir))
                    warnings.append(_restore_skill_failed_warning(skill, existing_dir, source_dir, exc))
                    if skills_mode == "strict":
                        raise ToolkitError(f"Failed to restore skill {skill.name}: {exc}") from exc
                    continue
                results.append(SkillRestoreResult(
                    name=skill.name,
                    source_root=skill.source_root,
                    relative_dir=skill.relative_dir,
                    status="restored",
                    target_path=str(existing_dir),
                    content_hash=bundle_hash,
                ))
                continue
            if skills_mode == "strict":
                raise ToolkitError(f"Skill conflict (not overwriting): {skill.name} at {existing_dir}")
            results.append(SkillRestoreResult(
                name=skill.name,
                source_root=skill.source_root,
                relative_dir=skill.relative_dir,
                status="conflict_skipped",
                target_path=str(existing_dir),
                content_hash=existing_hash,
            ))
            continue

        try:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, target_dir)
        except OSError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            results.append(_failed_restore_result(skill, target_dir))
            warnings.append(_restore_skill_failed_warning(skill, target_dir, source_dir, exc))
            if skills_mode == "strict":
                raise ToolkitError(f"Failed to restore skill {skill.name}: {exc}") from exc
            continue
        results.append(SkillRestoreResult(
            name=skill.name,
            source_root=skill.source_root,
            relative_dir=skill.relative_dir,
            status="restored",
            target_path=str(target_dir),
            content_hash=bundle_hash,
        ))

    return SkillsRestoreOutcome(results=tuple(results), warnings=tuple(warnings))


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


def _extract_skills_block(session_file: Path) -> str:
    import json as _json

    with session_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = _json.loads(stripped)
            except _json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict) or payload.get("role") != "developer":
                continue
            content = payload.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                text = item.get("text", "") if isinstance(item, dict) else ""
                if "<skills_instructions>" in text:
                    start = text.index("<skills_instructions>")
                    end = text.find("</skills_instructions>")
                    if end != -1:
                        return text[start:end + len("</skills_instructions>")]
                    return text[start:]
    return ""


def _parse_available_skills(block: str) -> List[Tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    for line in block.splitlines():
        m = _SKILL_LINE_RE.match(line.strip())
        if m:
            results.append((m.group(1), m.group(2), m.group(3)))
    return results


def _detect_skill_usage(session_file: Path, skill_names: List[str]) -> Dict[str, int]:
    import json as _json

    if not skill_names:
        return {}
    counts: Dict[str, int] = {name: 0 for name in skill_names}
    skill_patterns = {name: re.compile(r"(?:/\s*" + re.escape(name) + r"|Skill\s*\(\s*skill\s*=\s*['\"]" + re.escape(name) + r"['\"]|skill.*" + re.escape(name) + r")", re.IGNORECASE) for name in skill_names}
    skill_file_patterns = {name: re.compile(re.escape(name) + r"/SKILL\.md", re.IGNORECASE) for name in skill_names}

    with session_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = _json.loads(stripped)
            except _json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue

            text_to_check = ""

            if obj.get("type") == "response_item" and payload.get("role") == "assistant":
                text_to_check = _extract_text_from_content(payload.get("content"))
            elif obj.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                text_to_check = json.dumps(payload)

            if not text_to_check:
                continue

            for name in skill_names:
                if skill_patterns[name].search(text_to_check):
                    counts[name] += 1
                elif skill_file_patterns[name].search(text_to_check):
                    counts[name] += 1

    return counts


def _extract_text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text", "")
                if text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return ""


def _resolve_skill_source_dir(skill: SkillDescriptor) -> Optional[Path]:
    path = Path(skill.skill_file)
    if path.is_file():
        return path.parent
    parent = path.parent
    if parent.is_dir():
        return parent
    return None


def _first_existing_local_skill_dir(target_home: Path, skill: SkillDescriptor) -> Optional[Path]:
    for skill_dir in _candidate_local_skill_dirs(target_home, skill):
        if skill_dir.is_dir():
            return skill_dir
    return None


def _existing_local_skill_dirs(target_home: Path, skill: SkillDescriptor) -> list[Path]:
    return [skill_dir for skill_dir in _candidate_local_skill_dirs(target_home, skill) if skill_dir.is_dir()]


def _candidate_local_skill_dirs(target_home: Path, skill: SkillDescriptor) -> list[Path]:
    primary = Path(_target_skill_dir(target_home, skill))
    candidates = [primary]
    if skill.source_root in _RESTORABLE_SKILL_ROOTS and _is_safe_relative_posix_path(skill.relative_dir):
        for source_root in ("agents", "codex"):
            candidate = Path(_target_skill_dir_for_root(target_home, source_root, skill.relative_dir))
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _target_skill_dir(target_home: Path, skill: SkillDescriptor) -> str:
    return _target_skill_dir_for_root(target_home, skill.source_root, skill.relative_dir)


def _target_skill_dir_for_root(target_home: Path, source_root: str, relative_dir: str) -> str:
    if source_root == "agents":
        return str(target_home / ".agents" / "skills" / relative_dir)
    return str(target_home / ".codex" / "skills" / relative_dir)


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

    if location_kind not in _VALID_LOCATION_KINDS:
        return None
    if not _is_safe_relative_posix_path(relative_dir):
        return None
    if bundled and not _is_valid_bundled_skill_path(bundle_path, source_root=source_root, relative_dir=relative_dir):
        return None
    if not bundled and bundle_path and not _is_safe_relative_posix_path(bundle_path):
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


def _non_negative_int_or_default(value: object, default: int) -> int:
    parsed = _required_non_negative_int(value)
    return default if parsed is None else parsed


def _is_restorable_custom_skill(skill: SkillDescriptor) -> bool:
    return (
        skill.source_root in _RESTORABLE_SKILL_ROOTS
        and _is_safe_relative_posix_path(skill.relative_dir)
    )


def _is_safe_relative_posix_path(raw_path: str) -> bool:
    if not raw_path:
        return False
    path = PurePosixPath(raw_path)
    if path.is_absolute():
        return False
    parts = path.parts
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _is_valid_bundled_skill_path(bundle_path: str, *, source_root: str, relative_dir: str) -> bool:
    if not _is_safe_relative_posix_path(bundle_path):
        return False
    bundle_parts = PurePosixPath(bundle_path).parts
    relative_parts = PurePosixPath(relative_dir).parts
    return (
        len(bundle_parts) >= 3
        and bundle_parts[0] == SKILLS_DIR_NAME
        and bundle_parts[1] == source_root
        and bundle_parts[2:] == relative_parts
    )


def _replace_skill_directory(source_dir: Path, target_dir: Path) -> None:
    stage_dir = target_dir.with_name(target_dir.name + ".stage")
    backup_dir = target_dir.with_name(target_dir.name + ".bak")
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    try:
        shutil.copytree(source_dir, stage_dir)
        target_dir.rename(backup_dir)
        stage_dir.rename(target_dir)
    except OSError:
        if not target_dir.exists() and backup_dir.exists():
            backup_dir.rename(target_dir)
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _failed_restore_result(skill: SkillDescriptor, target_dir: Path) -> SkillRestoreResult:
    return SkillRestoreResult(
        name=skill.name,
        source_root=skill.source_root,
        relative_dir=skill.relative_dir,
        status="failed",
        target_path=str(target_dir),
    )


def _validate_bundled_skill_source(
    skill: SkillDescriptor,
    source_dir: Path,
) -> Optional[OperationWarning]:
    if not source_dir.exists():
        detail = "bundled skill directory missing"
    elif not source_dir.is_dir():
        detail = "bundled skill path is not a directory"
    elif not (source_dir / SKILL_MD_NAME).is_file():
        detail = f"missing {SKILL_MD_NAME}"
    else:
        return None
    return OperationWarning(
        code="invalid_bundled_skill",
        name=skill.name,
        source_root=skill.source_root,
        relative_dir=skill.relative_dir,
        path=str(source_dir),
        detail=detail,
    )


def _restore_skill_failed_warning(
    skill: SkillDescriptor,
    target_dir: Path,
    source_dir: Path,
    exc: OSError,
) -> OperationWarning:
    return OperationWarning(
        code="restore_skill_failed",
        name=skill.name,
        source_root=skill.source_root,
        relative_dir=skill.relative_dir,
        path=str(target_dir),
        related_path=str(source_dir),
        detail=str(exc),
    )
