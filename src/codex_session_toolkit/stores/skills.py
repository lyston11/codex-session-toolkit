"""Skill discovery, bundling, and restoration for session bundles."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..errors import ToolkitError
from ..models import LocalSkillSummary, OperationWarning
from .skills_manifest import (
    SKILLS_DIR_NAME,
    SKILLS_MANIFEST_FILENAME,
    SKILLS_SCHEMA_VERSION,
    SkillDescriptor,
    SkillRestoreResult,
    SkillsBundleResult,
    SkillsManifest,
    SkillsRestoreOutcome,
    deduplicate_skill_manifests,
    is_safe_relative_posix_path as _is_safe_relative_posix_path,
    read_skills_manifest,
    write_batch_skills_restore_report,
    write_skills_manifest,
)

SKILL_MD_NAME = "SKILL.md"

_SKILL_LINE_RE = re.compile(r"^- (\S+?):\s+(.+?)\s+\(file:\s+(.+?)\)\s*$")
_AGENTS_MARKER = "/.agents/skills/"
_CODEX_MARKER = "/.codex/skills/"
_SYSTEM_PREFIX = ".system/"
_RUNTIME_PREFIX = "codex-primary-runtime/"
_RESTORABLE_SKILL_ROOTS = {"agents", "codex"}

__all__ = [
    "SKILLS_DIR_NAME",
    "SKILLS_MANIFEST_FILENAME",
    "SKILLS_SCHEMA_VERSION",
    "SKILL_MD_NAME",
    "SkillDescriptor",
    "SkillRestoreResult",
    "SkillsBundleResult",
    "SkillsManifest",
    "SkillsRestoreOutcome",
    "build_skills_manifest_from_local_summaries",
    "bundle_skills",
    "classify_skill_location",
    "collect_local_skill_summaries",
    "compute_skill_directory_hash",
    "deduplicate_skill_manifests",
    "infer_skill_source_root",
    "parse_skills_from_session",
    "read_skills_manifest",
    "restore_skills",
    "write_batch_skills_restore_report",
    "write_skills_manifest",
]


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
        dependency_level = "required" if is_used else "available"
        evidence = ("explicit_skill_usage",) if is_used else ("available_in_context",)
        descriptors.append(SkillDescriptor(
            name=name,
            skill_file=skill_file,
            source_root=source_root,
            relative_dir=relative_dir,
            location_kind=location_kind,
            used=is_used,
            usage_count=count,
            dependency_level=dependency_level,
            evidence=evidence,
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


def collect_local_skill_summaries(
    target_home: Path,
    *,
    include_system: bool = False,
) -> list[LocalSkillSummary]:
    summaries: list[LocalSkillSummary] = []
    seen_relative_dirs: set[str] = set()
    roots = (
        ("agents", target_home / ".agents" / "skills"),
        ("codex", target_home / ".codex" / "skills"),
    )
    for source_root, root_dir in roots:
        if not root_dir.is_dir():
            continue
        for skill_file in sorted(root_dir.rglob(SKILL_MD_NAME)):
            skill_dir = skill_file.parent
            try:
                relative_dir = skill_dir.relative_to(root_dir).as_posix()
            except ValueError:
                continue
            location_kind = classify_skill_location(relative_dir)
            if location_kind != "custom" and not include_system:
                continue
            if location_kind == "custom" and relative_dir in seen_relative_dirs:
                continue
            if location_kind == "custom":
                seen_relative_dirs.add(relative_dir)
            try:
                content_hash = compute_skill_directory_hash(skill_dir)
            except OSError:
                content_hash = ""
            summaries.append(LocalSkillSummary(
                name=skill_dir.name,
                source_root=source_root,
                relative_dir=relative_dir,
                skill_dir=skill_dir,
                location_kind=location_kind,
                content_hash=content_hash,
            ))
    return summaries


def build_skills_manifest_from_local_summaries(summaries: list[LocalSkillSummary]) -> SkillsManifest:
    skills: list[SkillDescriptor] = []
    for summary in summaries:
        skills.append(SkillDescriptor(
            name=summary.name,
            skill_file=str(summary.skill_dir / SKILL_MD_NAME),
            source_root=summary.source_root,
            relative_dir=summary.relative_dir,
            location_kind=summary.location_kind,
            used=True,
            usage_count=1,
            dependency_level="required",
            evidence=("skills_transfer",),
        ))
    return SkillsManifest(
        available_skill_count=len(skills),
        used_skill_count=len(skills),
        bundled_skill_count=0,
        skills=tuple(skills),
    )


def bundle_skills(manifest: SkillsManifest, bundle_dir: Path) -> SkillsBundleResult:
    updated: list[SkillDescriptor] = []
    bundled_count = 0
    warnings: list[OperationWarning] = []
    for skill in manifest.skills:
        required = _is_required_skill(skill)
        if skill.location_kind != "custom" or skill.bundled or not required:
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
            dependency_level=skill.dependency_level,
            evidence=skill.evidence,
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
            if skill.location_kind == "custom" and _is_required_skill(skill):
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
    skill_patterns = {
        name: re.compile(
            r"(?:"
            r"/\s*" + re.escape(name) + r"\b"
            r"|Skill\s*\(\s*skill\s*=\s*['\"]" + re.escape(name) + r"['\"]"
            r"|\[\$?" + re.escape(name) + r"\]\("
            r"|['\"]skill['\"]\s*:\s*['\"]" + re.escape(name) + r"['\"]"
            r")",
            re.IGNORECASE,
        )
        for name in skill_names
    }
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

            if obj.get("type") == "response_item" and payload.get("role") in {"assistant", "user"}:
                text_to_check = _extract_text_from_content(payload.get("content"))
            elif obj.get("type") == "message" and payload.get("role") in {"assistant", "user"}:
                text_to_check = str(payload.get("text") or "")
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


def _is_required_skill(skill: SkillDescriptor) -> bool:
    return skill.used or skill.dependency_level == "required"


def _is_restorable_custom_skill(skill: SkillDescriptor) -> bool:
    return (
        skill.source_root in _RESTORABLE_SKILL_ROOTS
        and _is_safe_relative_posix_path(skill.relative_dir)
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
