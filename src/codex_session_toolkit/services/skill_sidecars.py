"""Service helpers for optional Skills sidecars embedded in session bundles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ToolkitError
from ..models import OperationWarning
from ..stores.skills import restore_skills
from ..stores.skills_manifest import (
    SKILLS_MANIFEST_FILENAME,
    SkillRestoreResult,
    read_skills_manifest,
    write_batch_skills_restore_report,
)


@dataclass(frozen=True)
class BundleSkillsRestoreSummary:
    restored_count: int = 0
    already_present_count: int = 0
    conflict_skipped_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    warnings: list[OperationWarning] = field(default_factory=list)


def restore_bundle_skills_sidecar(
    *,
    home: Path,
    bundle_dir: Path,
    session_id: str,
    skills_mode: str,
    report_path: Path | None = None,
) -> BundleSkillsRestoreSummary:
    if skills_mode == "skip":
        return BundleSkillsRestoreSummary()

    skills_manifest_file = bundle_dir / SKILLS_MANIFEST_FILENAME
    warnings: list[OperationWarning] = []
    restore_results: list[SkillRestoreResult] = []

    try:
        skills_manifest = read_skills_manifest(bundle_dir)
        if skills_manifest is None:
            if skills_manifest_file.is_file():
                if skills_mode == "strict":
                    raise ToolkitError(f"Invalid skills manifest: {skills_manifest_file}")
                warnings.append(
                    OperationWarning(
                        code="invalid_skills_manifest",
                        session_id=session_id,
                        path=str(skills_manifest_file),
                    )
                )
        elif skills_manifest.skills:
            restore_outcome = restore_skills(
                skills_manifest,
                bundle_dir,
                home,
                skills_mode=skills_mode,
            )
            restore_results = list(restore_outcome.results)
            warnings.extend(_with_session_id(warning, session_id) for warning in restore_outcome.warnings)
    except ToolkitError:
        raise
    except OSError as exc:
        if skills_mode == "strict":
            raise ToolkitError(f"Failed to restore skills from {bundle_dir}: {exc}") from exc
        warnings.append(
            OperationWarning(
                code="restore_skills_failed",
                session_id=session_id,
                path=str(bundle_dir),
                detail=str(exc),
            )
        )
        return BundleSkillsRestoreSummary(warnings=warnings)

    summary = _summarize_restore_results(session_id, restore_results)
    warnings.extend(summary.warnings)
    if report_path is not None and restore_results:
        try:
            write_batch_skills_restore_report(
                report_path,
                session_id,
                restore_results,
            )
        except OSError as exc:
            warnings.append(
                OperationWarning(
                    code="skills_restore_report_failed",
                    session_id=session_id,
                    path=str(report_path),
                    related_path=str(bundle_dir),
                    detail=str(exc),
                )
            )

    return BundleSkillsRestoreSummary(
        restored_count=summary.restored_count,
        already_present_count=summary.already_present_count,
        conflict_skipped_count=summary.conflict_skipped_count,
        missing_count=summary.missing_count,
        failed_count=summary.failed_count,
        warnings=warnings,
    )


def _with_session_id(warning: OperationWarning, session_id: str) -> OperationWarning:
    return OperationWarning(
        code=warning.code,
        session_id=session_id,
        path=warning.path,
        related_path=warning.related_path,
        detail=warning.detail,
        name=warning.name,
        source_root=warning.source_root,
        relative_dir=warning.relative_dir,
    )


def _summarize_restore_results(
    session_id: str,
    restore_results: list[SkillRestoreResult],
) -> BundleSkillsRestoreSummary:
    restored = 0
    already_present = 0
    conflict_skipped = 0
    missing = 0
    failed = 0
    warnings: list[OperationWarning] = []
    for result in restore_results:
        if result.status == "restored":
            restored += 1
        elif result.status == "already_present":
            already_present += 1
        elif result.status == "conflict_skipped":
            conflict_skipped += 1
        elif result.status == "missing":
            missing += 1
            warnings.append(
                OperationWarning(
                    code="missing_skill",
                    session_id=session_id,
                    name=result.name,
                    source_root=result.source_root,
                    relative_dir=result.relative_dir,
                )
            )
        elif result.status == "failed":
            failed += 1
    return BundleSkillsRestoreSummary(
        restored_count=restored,
        already_present_count=already_present,
        conflict_skipped_count=conflict_skipped,
        missing_count=missing,
        failed_count=failed,
        warnings=warnings,
    )
