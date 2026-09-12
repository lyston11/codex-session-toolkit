"""Read-only browsing services."""

from __future__ import annotations

from typing import Optional

from ..errors import ToolkitError
from ..models import BundleSummary, SessionSummary, ValidationReport
from ..paths import CodexPaths
from ..stores.agent_sessions import (
    NON_CODEX_SESSION_AGENTS,
    collect_agent_session_summaries,
)
from ..stores.bundle_scanner import (
    collect_known_bundle_summaries,
    iter_known_bundle_directories,
)
from ..stores.bundle_validation import validate_bundle_directory
from ..stores.session_files import collect_session_summaries


def get_session_summaries(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
    project_path: str = "",
    active_only: bool = False,
    archived_only: bool = False,
    agent: str = "codex",
) -> list[SessionSummary]:
    if agent not in {"codex", "", *NON_CODEX_SESSION_AGENTS}:
        raise ToolkitError(f"Unsupported session agent: {agent}")
    if agent in NON_CODEX_SESSION_AGENTS:
        return collect_agent_session_summaries(
            paths.home,
            agents=(agent,),
            pattern=pattern,
            limit=limit,
        )
    summaries = list(
        collect_session_summaries(
            paths,
            pattern=pattern,
            limit=limit,
            project_path=project_path,
            active_only=active_only,
        )
    )
    if agent == "":
        summaries.extend(
            collect_agent_session_summaries(paths.home, pattern=pattern, limit=limit)
        )
    if archived_only:
        return [summary for summary in summaries if summary.scope == "archived"]
    return summaries


def get_project_session_summaries(
    paths: CodexPaths,
    *,
    project_path: str,
    pattern: str = "",
    limit: Optional[int] = None,
) -> list[SessionSummary]:
    return collect_session_summaries(paths, pattern=pattern, limit=limit, project_path=project_path)


def get_bundle_summaries(
    paths: CodexPaths,
    *,
    pattern: str = "",
    limit: Optional[int] = None,
    source_group: str = "all",
    machine_filter: str = "",
    export_group_filter: str = "",
) -> list[BundleSummary]:
    return collect_known_bundle_summaries(
        paths,
        pattern=pattern,
        limit=limit,
        source_group=source_group,
        machine_filter=machine_filter,
        export_group_filter=export_group_filter,
    )


def validate_bundles(
    paths: CodexPaths,
    *,
    pattern: str = "",
    source_group: str = "all",
    limit: Optional[int] = None,
) -> ValidationReport:
    bundle_entries = iter_known_bundle_directories(paths, source_group=source_group)
    results = []

    for entry_source_group, bundle_dir in bundle_entries:
        result = validate_bundle_directory(bundle_dir, source_group=entry_source_group)
        if pattern:
            haystack = " ".join(
                [
                    result.source_group,
                    result.session_id,
                    str(result.bundle_dir),
                    result.message,
                ]
            )
            if pattern not in haystack:
                continue
        results.append(result)
        if limit is not None and len(results) >= max(1, limit):
            break

    return ValidationReport(source_group=source_group, results=results)
