"""Standalone Skills bundle export/import services."""

from __future__ import annotations

import shlex
import shutil
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from ..errors import ToolkitError
from ..models import (
    LocalSkillSummary,
    OperationWarning,
    SkillBundleSummary,
    SkillDeleteResult,
    SkillExportResult,
    SkillImportResult,
)
from ..paths import CodexPaths
from ..stores.skills_manifest import SKILLS_MANIFEST_FILENAME, read_skills_manifest, write_skills_manifest
from ..stores.skills import (
    build_skills_manifest_from_local_summaries,
    bundle_skills,
    classify_skill_location,
    collect_local_skill_summaries,
    compute_skill_directory_hash,
    restore_skills,
)
from ..support import build_skills_export_root, detect_machine_key, detect_machine_label, ensure_path_within_dir, normalize_bundle_root
from ..support import restrict_to_local_bundle_workspace
from ..validation import write_manifest


def list_local_skills(
    paths: CodexPaths,
    *,
    pattern: str = "",
    include_system: bool = False,
) -> list[LocalSkillSummary]:
    summaries = collect_local_skill_summaries(paths.home, include_system=include_system)
    if pattern:
        summaries = [
            summary for summary in summaries
            if pattern in " ".join([
                summary.name,
                summary.source_root,
                summary.relative_dir,
                summary.location_kind,
                str(summary.skill_dir),
            ])
        ]
    return summaries


def export_skills(
    paths: CodexPaths,
    *,
    pattern: str = "",
    input_values: Sequence[str] = (),
    bundle_root: Optional[Path] = None,
    include_system: bool = False,
    skills_mode: str = "best-effort",
) -> SkillExportResult:
    selected_inputs = [value for value in input_values if value]
    machine_key = detect_machine_key()
    machine_label = detect_machine_label()
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.skills_bundle_root)
    export_group = "all" if not pattern and not selected_inputs else "single" if pattern or len(selected_inputs) == 1 else "selected"
    export_root = build_skills_export_root(resolved_root, export_group, machine_key)
    export_root.parent.mkdir(parents=True, exist_ok=True)

    local_skills = _selected_local_skills(
        paths,
        pattern=pattern,
        input_values=selected_inputs,
        include_system=include_system,
    )
    exportable = [summary for summary in local_skills if summary.location_kind == "custom"]
    skipped_count = len(local_skills) - len(exportable)
    if (pattern or selected_inputs) and not exportable:
        raise ToolkitError(f"No matching custom Skills found: {_skill_selection_label(pattern, selected_inputs)}")

    stage_root = Path(tempfile.mkdtemp(prefix=".skills.tmp.", dir=str(export_root.parent)))
    stage_dir = stage_root / export_root.name
    old_backup: Optional[Path] = None
    warnings: list[OperationWarning] = []
    try:
        manifest = build_skills_manifest_from_local_summaries(exportable)
        bundle_result = bundle_skills(manifest, stage_dir)
        warnings.extend(bundle_result.warnings)
        if warnings and skills_mode == "strict":
            raise ToolkitError(_format_skill_warning(warnings[0]))
        stage_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = write_skills_manifest(bundle_result.manifest, stage_dir)
        _write_skill_bundle_manifest(
            stage_dir / "manifest.env",
            exported_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            machine_label=machine_label,
            machine_key=machine_key,
            export_group=export_group,
            skill_count=len(exportable),
            bundled_count=bundle_result.manifest.bundled_skill_count,
        )

        if export_root.exists():
            old_backup = export_root.with_name(export_root.name + ".bak")
            if old_backup.exists():
                shutil.rmtree(old_backup, ignore_errors=True)
            export_root.rename(old_backup)
        stage_dir.rename(export_root)
        shutil.rmtree(stage_root, ignore_errors=True)
        if old_backup and old_backup.exists():
            shutil.rmtree(old_backup, ignore_errors=True)
        return SkillExportResult(
            bundle_dir=export_root,
            source_machine=machine_label,
            source_machine_key=machine_key,
            exported_count=bundle_result.manifest.bundled_skill_count,
            skipped_count=skipped_count,
            manifest_file=export_root / manifest_path.name,
            warnings=warnings,
        )
    except Exception:
        if old_backup and old_backup.exists() and not export_root.exists():
            old_backup.rename(export_root)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def list_skill_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
) -> list[SkillBundleSummary]:
    roots = [paths.skills_bundle_root, paths.legacy_session_bundle_root]
    summaries: list[SkillBundleSummary] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser()
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        for manifest_file in root.rglob("manifest.env"):
            bundle_dir = manifest_file.parent
            try:
                manifest = _read_skill_bundle_manifest(manifest_file)
            except ToolkitError:
                continue
            if manifest.get("BUNDLE_TYPE") != "skills":
                continue
            skills_manifest = read_skills_manifest(bundle_dir)
            skill_names = tuple(skill.name for skill in skills_manifest.skills) if skills_manifest else ()
            summary = SkillBundleSummary(
                bundle_dir=bundle_dir,
                exported_at=manifest.get("EXPORTED_AT", ""),
                source_machine=manifest.get("EXPORT_MACHINE", ""),
                source_machine_key=manifest.get("EXPORT_MACHINE_KEY", ""),
                export_group=manifest.get("EXPORT_GROUP", ""),
                skill_count=int(manifest.get("SKILL_COUNT", "0") or "0"),
                bundled_skill_count=int(manifest.get("BUNDLED_SKILL_COUNT", "0") or "0"),
                skills=skill_names,
            )
            if pattern and pattern not in " ".join([
                str(summary.bundle_dir),
                summary.source_machine,
                summary.source_machine_key,
                summary.export_group,
                " ".join(summary.skills),
            ]):
                continue
            summaries.append(summary)
    summaries.sort(key=lambda item: (item.exported_at, str(item.bundle_dir)), reverse=True)
    return summaries


def import_skill_bundle(
    paths: CodexPaths,
    input_value: str,
    *input_values: str,
    skills_mode: str = "best-effort",
) -> SkillImportResult:
    return import_selected_skill_bundles(
        paths,
        [input_value, *input_values],
        skills_mode=skills_mode,
    )


def import_selected_skill_bundles(
    paths: CodexPaths,
    input_values: Sequence[str],
    *,
    skills_mode: str = "best-effort",
) -> SkillImportResult:
    if not input_values:
        raise ToolkitError("Skill bundle input is required.")
    bundle_dirs = _resolve_skill_bundle_dirs(paths, input_values)
    return _import_skill_bundle_dirs(paths, bundle_dirs, result_bundle_dir=bundle_dirs[0], skills_mode=skills_mode)


def _import_one_skill_bundle_dir(
    paths: CodexPaths,
    bundle_dir: Path,
    *,
    skills_mode: str,
) -> SkillImportResult:
    _assert_skill_bundle(bundle_dir)
    manifest = read_skills_manifest(bundle_dir)
    if manifest is None:
        raise ToolkitError(f"Invalid skills manifest: {bundle_dir / SKILLS_MANIFEST_FILENAME}")
    if skills_mode == "skip":
        return SkillImportResult(bundle_dir=bundle_dir)
    outcome = restore_skills(manifest, bundle_dir, paths.home, skills_mode=skills_mode)
    restored = sum(1 for result in outcome.results if result.status == "restored")
    already_present = sum(1 for result in outcome.results if result.status == "already_present")
    conflict_skipped = sum(1 for result in outcome.results if result.status == "conflict_skipped")
    missing = sum(1 for result in outcome.results if result.status == "missing")
    failed = sum(1 for result in outcome.results if result.status == "failed")
    return SkillImportResult(
        bundle_dir=bundle_dir,
        restored_count=restored,
        already_present_count=already_present,
        conflict_skipped_count=conflict_skipped,
        missing_count=missing,
        failed_count=failed,
        warnings=list(outcome.warnings),
    )


def import_all_skill_bundles(
    paths: CodexPaths,
    *,
    machine_filter: str = "",
    skills_mode: str = "best-effort",
) -> SkillImportResult:
    bundles = [
        bundle for bundle in list_skill_bundles(paths)
        if not machine_filter or bundle.source_machine_key == machine_filter or bundle.source_machine == machine_filter
    ]
    return _import_skill_bundle_dirs(
        paths,
        [bundle.bundle_dir for bundle in bundles],
        result_bundle_dir=paths.skills_bundle_root,
        skills_mode=skills_mode,
    )


def _selected_local_skills(
    paths: CodexPaths,
    *,
    pattern: str,
    input_values: Sequence[str],
    include_system: bool,
) -> list[LocalSkillSummary]:
    if not input_values:
        return list_local_skills(paths, pattern=pattern, include_system=include_system)
    if pattern:
        raise ToolkitError("Pass either a pattern or selected Skills, not both.")

    available = collect_local_skill_summaries(paths.home, include_system=include_system)
    targets: list[LocalSkillSummary] = []
    seen: set[tuple[str, str]] = set()
    for input_value in input_values:
        allow_many = False
        matches = _resolve_local_skill_matches(available, input_value)
        if not matches:
            matches = [
                summary for summary in list_local_skills(paths, pattern=input_value, include_system=include_system)
                if summary.location_kind == "custom"
            ]
            allow_many = True
        if not matches:
            raise ToolkitError(f"Custom Skill not found: {input_value}")
        if len(matches) > 1 and not allow_many:
            roots = ", ".join(sorted({match.source_root for match in matches}))
            raise ToolkitError(f"Multiple matching Skills found in {roots}; pass an exact Skill directory")
        for target in matches:
            key = (target.source_root, target.relative_dir)
            if key not in seen:
                targets.append(target)
                seen.add(key)
    return targets


def _skill_selection_label(pattern: str, input_values: Sequence[str]) -> str:
    if pattern:
        return pattern
    return ", ".join(input_values) if input_values else "all"


def _resolve_skill_bundle_dirs(paths: CodexPaths, input_values: Sequence[str]) -> list[Path]:
    bundle_dirs: list[Path] = []
    seen: set[str] = set()
    for input_value in input_values:
        bundle_dir = _resolve_skill_bundle(paths, input_value)
        dir_key = _path_key(bundle_dir)
        if dir_key not in seen:
            bundle_dirs.append(bundle_dir)
            seen.add(dir_key)
    return bundle_dirs


def _import_skill_bundle_dirs(
    paths: CodexPaths,
    bundle_dirs: Sequence[Path],
    *,
    result_bundle_dir: Path,
    skills_mode: str,
) -> SkillImportResult:
    warnings: list[OperationWarning] = []
    restored = already = conflicts = missing = failed = 0
    for bundle_dir in bundle_dirs:
        try:
            result = _import_one_skill_bundle_dir(paths, bundle_dir, skills_mode=skills_mode)
        except ToolkitError as exc:
            failed += 1
            warnings.append(OperationWarning(code="restore_skills_failed", path=str(bundle_dir), detail=str(exc)))
            continue
        restored += result.restored_count
        already += result.already_present_count
        conflicts += result.conflict_skipped_count
        missing += result.missing_count
        failed += result.failed_count
        warnings.extend(result.warnings)
    return SkillImportResult(
        bundle_dir=result_bundle_dir,
        restored_count=restored,
        already_present_count=already,
        conflict_skipped_count=conflicts,
        missing_count=missing,
        failed_count=failed,
        warnings=warnings,
    )


def delete_local_skill(
    paths: CodexPaths,
    input_value: str,
    *,
    source_root: str = "",
    dry_run: bool = False,
) -> SkillDeleteResult:
    matches = _resolve_local_skill_delete_matches(paths, input_value, source_root=source_root)
    if not matches:
        scope = f" in {source_root}" if source_root else ""
        raise ToolkitError(f"Custom Skill not found{scope}: {input_value}")
    if len(matches) > 1:
        roots = ", ".join(sorted({match.source_root for match in matches}))
        raise ToolkitError(f"Multiple matching Skills found in {roots}; pass --source-root agents|codex")

    return _delete_local_skill_summary(paths, matches[0], dry_run=dry_run)


def delete_local_skills(
    paths: CodexPaths,
    input_values: Sequence[str] = (),
    *,
    source_root: str = "",
    all_skills: bool = False,
    dry_run: bool = False,
) -> list[SkillDeleteResult]:
    if all_skills and input_values:
        raise ToolkitError("Pass either --all or specific Skills, not both.")

    if all_skills:
        targets = _collect_local_skill_delete_candidates(paths)
        if source_root:
            targets = [target for target in targets if target.source_root == source_root]
        if not targets:
            scope = f" in {source_root}" if source_root else ""
            raise ToolkitError(f"No custom Skills found{scope}.")
    else:
        if not input_values:
            raise ToolkitError("Skill name, relative directory, local Skill directory, or --all is required.")
        targets = []
        for input_value in input_values:
            matches = _resolve_local_skill_delete_matches(paths, input_value, source_root=source_root)
            if not matches:
                scope = f" in {source_root}" if source_root else ""
                raise ToolkitError(f"Custom Skill not found{scope}: {input_value}")
            if len(matches) > 1:
                roots = ", ".join(sorted({match.source_root for match in matches}))
                raise ToolkitError(f"Multiple matching Skills found in {roots}; pass --source-root agents|codex")
            targets.append(matches[0])

    results: list[SkillDeleteResult] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        key = (target.source_root, target.relative_dir)
        if key in seen:
            continue
        seen.add(key)
        results.append(_delete_local_skill_summary(paths, target, dry_run=dry_run))
    return results


def _delete_local_skill_summary(
    paths: CodexPaths,
    target: LocalSkillSummary,
    *,
    dry_run: bool = False,
) -> SkillDeleteResult:
    root_dir = _skills_root_for_source(paths, target.source_root)
    ensure_path_within_dir(target.skill_dir, root_dir, "Skill directory")
    if target.location_kind != "custom":
        raise ToolkitError(f"Refusing to delete non-custom Skill: {target.relative_dir}")
    if not (target.skill_dir / "SKILL.md").is_file():
        raise ToolkitError(f"Refusing to delete invalid Skill directory: {target.skill_dir}")

    if not dry_run:
        shutil.rmtree(target.skill_dir)

    return SkillDeleteResult(
        name=target.name,
        source_root=target.source_root,
        relative_dir=target.relative_dir,
        skill_dir=target.skill_dir,
        dry_run=dry_run,
        deleted=not dry_run,
    )


def _resolve_skill_bundle(paths: CodexPaths, input_value: str) -> Path:
    candidate = Path(input_value).expanduser()
    if candidate.is_dir():
        return restrict_to_local_bundle_workspace(paths, candidate, "Skill bundle directory")
    matches = [
        bundle.bundle_dir for bundle in list_skill_bundles(paths, pattern=input_value)
        if input_value in bundle.skills or input_value in str(bundle.bundle_dir)
    ]
    if not matches:
        raise ToolkitError(f"Skill bundle not found: {input_value}")
    return matches[0]


def _resolve_local_skill_delete_matches(
    paths: CodexPaths,
    input_value: str,
    *,
    source_root: str = "",
) -> list[LocalSkillSummary]:
    if source_root and source_root not in {"agents", "codex"}:
        raise ToolkitError(f"Unsupported source root: {source_root}")
    input_value = input_value.strip()
    if not input_value:
        raise ToolkitError("Skill name or relative directory is required.")

    candidates = _collect_local_skill_delete_candidates(paths)
    if source_root:
        candidates = [candidate for candidate in candidates if candidate.source_root == source_root]
    return _resolve_local_skill_matches(candidates, input_value)


def _resolve_local_skill_matches(candidates: Sequence[LocalSkillSummary], input_value: str) -> list[LocalSkillSummary]:
    input_value = input_value.strip()
    candidate_path = Path(input_value).expanduser()
    resolved_input = _path_key(candidate_path) if candidate_path.is_absolute() else ""
    return [
        candidate for candidate in candidates
        if input_value in {candidate.name, candidate.relative_dir, str(candidate.skill_dir)}
        or (resolved_input and _path_key(candidate.skill_dir) == resolved_input)
    ]


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def _collect_local_skill_delete_candidates(paths: CodexPaths) -> list[LocalSkillSummary]:
    candidates: list[LocalSkillSummary] = []
    for source_root in ("agents", "codex"):
        root_dir = _skills_root_for_source(paths, source_root)
        if not root_dir.is_dir():
            continue
        for skill_file in sorted(root_dir.rglob("SKILL.md")):
            skill_dir = skill_file.parent
            try:
                relative_dir = skill_dir.relative_to(root_dir).as_posix()
            except ValueError:
                continue
            location_kind = classify_skill_location(relative_dir)
            if location_kind != "custom":
                continue
            try:
                content_hash = compute_skill_directory_hash(skill_dir)
            except OSError:
                content_hash = ""
            candidates.append(LocalSkillSummary(
                name=skill_dir.name,
                source_root=source_root,
                relative_dir=relative_dir,
                skill_dir=skill_dir,
                location_kind=location_kind,
                content_hash=content_hash,
            ))
    return candidates


def _skills_root_for_source(paths: CodexPaths, source_root: str) -> Path:
    if source_root == "agents":
        return paths.agents_skills_dir
    if source_root == "codex":
        return paths.codex_skills_dir
    raise ToolkitError(f"Unsupported source root: {source_root}")


def _assert_skill_bundle(bundle_dir: Path) -> None:
    manifest_file = bundle_dir / "manifest.env"
    if not manifest_file.is_file():
        raise ToolkitError(f"Missing skill bundle manifest: {manifest_file}")
    manifest = _read_skill_bundle_manifest(manifest_file)
    if manifest.get("BUNDLE_TYPE") != "skills":
        raise ToolkitError(f"Not a standalone Skills bundle: {bundle_dir}")


def _write_skill_bundle_manifest(
    manifest_file: Path,
    *,
    exported_at: str,
    machine_label: str,
    machine_key: str,
    export_group: str,
    skill_count: int,
    bundled_count: int,
) -> None:
    data = OrderedDict(
        BUNDLE_TYPE="skills",
        EXPORTED_AT=exported_at,
        EXPORT_MACHINE=machine_label,
        EXPORT_MACHINE_KEY=machine_key,
        EXPORT_GROUP=export_group,
        SKILL_COUNT=str(skill_count),
        BUNDLED_SKILL_COUNT=str(bundled_count),
    )
    write_manifest(manifest_file, data)


def _read_skill_bundle_manifest(manifest_file: Path) -> dict[str, str]:
    allowed = {
        "BUNDLE_TYPE",
        "EXPORTED_AT",
        "EXPORT_MACHINE",
        "EXPORT_MACHINE_KEY",
        "EXPORT_GROUP",
        "SKILL_COUNT",
        "BUNDLED_SKILL_COUNT",
    }
    values: dict[str, str] = {}
    with manifest_file.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key not in allowed:
                raise ToolkitError(f"Unexpected skill manifest key: {key}")
            parts = shlex.split(value, posix=True)
            if len(parts) != 1:
                raise ToolkitError(f"Invalid skill manifest value for {key}")
            values[key] = parts[0]
    return values


def _format_skill_warning(warning: OperationWarning) -> str:
    return f"{warning.code}: {warning.name or warning.path} {warning.detail}".strip()
