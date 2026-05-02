"""Synchronize local bundle workspace changes to a GitHub remote."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..errors import ToolkitError
from ..models import GitHubConnectResult, GitHubPullResult, GitHubSyncResult, GitHubSyncStatus
from ..paths import CodexPaths
from ..support import normalize_bundle_root


DEFAULT_GITHUB_SYNC_BRANCH = "main"
DEFAULT_GITHUB_SYNC_MESSAGE = "Sync Codex bundles"
DEFAULT_GITHUB_REMOTE_NAME = "origin"


@dataclass(frozen=True)
class _ChangeGroups:
    sessions: list[str]
    skills: list[str]
    other: list[str]


@dataclass(frozen=True)
class _RemoteIntegrationResult:
    remote_checked: bool = False
    pulled: bool = False
    merged_remote: bool = False
    conflict: bool = False
    conflict_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _RemoteStatusSnapshot:
    remote_checked: bool = False
    remote_branch_exists: bool = False
    remote_commit_hash: str = ""
    remote_updated_at: str = ""
    local_ahead_count: int = 0
    remote_ahead_count: int = 0
    remote_check_error: str = ""


def get_github_sync_status(
    paths: CodexPaths,
    *,
    remote_name: str = DEFAULT_GITHUB_REMOTE_NAME,
    branch: str = DEFAULT_GITHUB_SYNC_BRANCH,
    bundle_root: Optional[Path] = None,
    check_remote: bool = False,
) -> GitHubSyncStatus:
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root, label="GitHub sync bundle root")
    branch = (branch or DEFAULT_GITHUB_SYNC_BRANCH).strip() or DEFAULT_GITHUB_SYNC_BRANCH
    remote_name = (remote_name or DEFAULT_GITHUB_REMOTE_NAME).strip() or DEFAULT_GITHUB_REMOTE_NAME
    root_exists = resolved_root.is_dir()
    repo_exists = _is_git_repo(resolved_root) if root_exists else False
    remote_url = _get_remote_url(resolved_root, remote_name) if repo_exists else ""
    current_branch = _git_output(resolved_root, ["branch", "--show-current"], check=False) if repo_exists else ""
    effective_branch = current_branch or branch
    changed_files = _git_status_paths(resolved_root) if repo_exists else []
    change_groups = _group_bundle_changes(changed_files)
    has_head_commit = _has_head_commit(resolved_root) if repo_exists else False
    local_commit_hash = _commit_hash(resolved_root, "HEAD") if has_head_commit else ""
    local_updated_at = _commit_updated_at(resolved_root, "HEAD") if has_head_commit else ""
    project_remote_url = _matching_project_code_remote(resolved_root, remote_url)
    is_connected = bool(repo_exists and remote_url and not project_remote_url)
    remote_snapshot = (
        _remote_status_snapshot(
            resolved_root,
            remote_name=remote_name,
            branch=effective_branch,
            has_head_commit=has_head_commit,
        )
        if is_connected and check_remote
        else _RemoteStatusSnapshot()
    )

    if not root_exists:
        message = "Bundle root has not been created yet. Export bundles before connecting GitHub sync."
    elif not repo_exists:
        message = "Bundle root is not connected. Create a dedicated GitHub repo, then run connect-github."
    elif not remote_url:
        message = "Bundle git repo has no remote. Run connect-github with a dedicated bundles repository URL."
    elif project_remote_url:
        message = "Bundle remote points at the project source repository. Connect a dedicated bundles repository instead."
    elif check_remote and remote_snapshot.remote_check_error:
        message = "Bundle GitHub sync is connected, but remote update check failed."
    elif check_remote and remote_snapshot.remote_checked and not remote_snapshot.remote_branch_exists:
        message = "Bundle GitHub sync is connected, but the remote branch has no bundle commits yet."
    elif check_remote and remote_snapshot.remote_ahead_count and remote_snapshot.local_ahead_count:
        message = "Local and remote both have bundle updates. Pull will merge or report conflicts before push."
    elif check_remote and remote_snapshot.remote_ahead_count:
        message = "Remote has newer bundle updates available to pull."
    elif (check_remote and remote_snapshot.local_ahead_count) or changed_files:
        message = "Local bundle workspace has updates available to push."
    else:
        message = (
            "Bundle GitHub sync is connected. Remote update time has not been checked yet."
            if is_connected and not check_remote
            else "Bundle GitHub sync is connected and up to date."
        )

    return GitHubSyncStatus(
        bundle_root=resolved_root,
        remote_name=remote_name,
        remote_url=remote_url,
        branch=effective_branch,
        bundle_root_exists=root_exists,
        is_git_repo=repo_exists,
        is_connected=is_connected,
        uses_project_source_remote=bool(project_remote_url),
        project_remote_url=project_remote_url,
        changed_files=changed_files,
        session_changed_files=change_groups.sessions,
        skill_changed_files=change_groups.skills,
        other_changed_files=change_groups.other,
        has_head_commit=has_head_commit,
        local_commit_hash=local_commit_hash,
        local_updated_at=local_updated_at,
        remote_checked=remote_snapshot.remote_checked,
        remote_branch_exists=remote_snapshot.remote_branch_exists,
        remote_commit_hash=remote_snapshot.remote_commit_hash,
        remote_updated_at=remote_snapshot.remote_updated_at,
        local_ahead_count=remote_snapshot.local_ahead_count,
        remote_ahead_count=remote_snapshot.remote_ahead_count,
        remote_check_error=remote_snapshot.remote_check_error,
        message=message,
    )


def connect_bundles_to_github(
    paths: CodexPaths,
    remote_url: str,
    *,
    remote_name: str = DEFAULT_GITHUB_REMOTE_NAME,
    branch: str = DEFAULT_GITHUB_SYNC_BRANCH,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> GitHubConnectResult:
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root, label="GitHub sync bundle root")
    branch = (branch or DEFAULT_GITHUB_SYNC_BRANCH).strip() or DEFAULT_GITHUB_SYNC_BRANCH
    remote_name = (remote_name or DEFAULT_GITHUB_REMOTE_NAME).strip() or DEFAULT_GITHUB_REMOTE_NAME
    remote_url = (remote_url or "").strip()
    if not remote_url:
        raise ToolkitError("GitHub bundle repository URL is required. Create a dedicated GitHub repo first, then connect it.")
    _reject_project_code_remote(resolved_root, remote_url)

    repo_exists = _is_git_repo(resolved_root) if resolved_root.exists() else False
    existing_remote_url = _get_remote_url(resolved_root, remote_name) if repo_exists else ""
    needs_init = not repo_exists
    needs_remote_config = remote_url != existing_remote_url
    commands = _planned_connect_commands(
        resolved_root,
        remote_name=remote_name,
        remote_url=remote_url,
        branch=branch,
        needs_init=needs_init,
        needs_remote_config=needs_remote_config,
        existing_remote_url=existing_remote_url,
    )

    if dry_run:
        return GitHubConnectResult(
            bundle_root=resolved_root,
            remote_name=remote_name,
            remote_url=remote_url,
            branch=branch,
            dry_run=True,
            initialized_repo=needs_init,
            configured_remote=needs_remote_config,
            commands=commands,
        )

    resolved_root.mkdir(parents=True, exist_ok=True)
    executed_commands: list[str] = []
    if needs_init:
        _run_git(resolved_root, ["init"], executed_commands)
        _run_git(resolved_root, ["checkout", "-B", branch], executed_commands)
    if existing_remote_url:
        if existing_remote_url != remote_url:
            _run_git(resolved_root, ["remote", "set-url", remote_name, remote_url], executed_commands)
    else:
        _run_git(resolved_root, ["remote", "add", remote_name, remote_url], executed_commands)

    return GitHubConnectResult(
        bundle_root=resolved_root,
        remote_name=remote_name,
        remote_url=remote_url,
        branch=branch,
        dry_run=False,
        initialized_repo=needs_init,
        configured_remote=needs_remote_config,
        commands=executed_commands,
    )


def pull_bundles_from_github(
    paths: CodexPaths,
    *,
    remote_name: str = DEFAULT_GITHUB_REMOTE_NAME,
    branch: str = DEFAULT_GITHUB_SYNC_BRANCH,
    bundle_root: Optional[Path] = None,
    dry_run: bool = False,
) -> GitHubPullResult:
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root, label="GitHub sync bundle root")
    if not resolved_root.is_dir():
        raise ToolkitError(f"Missing bundle root: {resolved_root}")

    branch = (branch or DEFAULT_GITHUB_SYNC_BRANCH).strip() or DEFAULT_GITHUB_SYNC_BRANCH
    remote_name = (remote_name or DEFAULT_GITHUB_REMOTE_NAME).strip() or DEFAULT_GITHUB_REMOTE_NAME
    commands: list[str] = []

    repo_exists = _is_git_repo(resolved_root)
    if not repo_exists:
        raise ToolkitError("GitHub pull is not connected. Create a dedicated GitHub repo, then run connect-github first.")
    effective_remote_url = _get_remote_url(resolved_root, remote_name).strip()
    if not effective_remote_url:
        raise ToolkitError("GitHub pull remote is missing. Run connect-github with a dedicated bundles repository URL first.")
    _reject_project_code_remote(resolved_root, effective_remote_url)

    changed_files = _git_status_paths(resolved_root)
    change_groups = _group_bundle_changes(changed_files)
    has_head_commit = _has_head_commit(resolved_root)
    local_commit_hash = _commit_hash(resolved_root, "HEAD") if has_head_commit else ""
    local_updated_at = _commit_updated_at(resolved_root, "HEAD") if has_head_commit else ""

    if dry_run:
        remote_branch_exists = _remote_branch_exists(resolved_root, remote_name=remote_name, branch=branch, commands=commands)
        if remote_branch_exists:
            commands.append(_format_git_command(resolved_root, ["fetch", remote_name, branch]))
            commands.append(_format_git_command(resolved_root, ["merge", "--ff-only", _remote_ref(remote_name, branch)]))
        skipped_reason = "local_changes_block_pull" if changed_files and remote_branch_exists else ""
        if not remote_branch_exists:
            skipped_reason = "remote_branch_missing"
        return GitHubPullResult(
            bundle_root=resolved_root,
            remote_name=remote_name,
            remote_url=effective_remote_url,
            branch=branch,
            dry_run=True,
            changed_files=changed_files,
            session_changed_files=change_groups.sessions,
            skill_changed_files=change_groups.skills,
            other_changed_files=change_groups.other,
            local_commit_hash=local_commit_hash,
            local_updated_at=local_updated_at,
            remote_checked=True,
            remote_branch_exists=remote_branch_exists,
            skipped_reason=skipped_reason,
            commands=commands,
        )

    remote_branch_exists = _remote_branch_exists(resolved_root, remote_name=remote_name, branch=branch, commands=commands)
    if not remote_branch_exists:
        return GitHubPullResult(
            bundle_root=resolved_root,
            remote_name=remote_name,
            remote_url=effective_remote_url,
            branch=branch,
            dry_run=False,
            changed_files=changed_files,
            session_changed_files=change_groups.sessions,
            skill_changed_files=change_groups.skills,
            other_changed_files=change_groups.other,
            local_commit_hash=local_commit_hash,
            local_updated_at=local_updated_at,
            remote_checked=True,
            remote_branch_exists=False,
            skipped_reason="remote_branch_missing",
            commands=commands,
        )

    _run_git(resolved_root, ["fetch", remote_name, branch], commands)
    remote_ref = _remote_ref(remote_name, branch)
    remote_commit_hash = _commit_hash(resolved_root, remote_ref)
    remote_updated_at = _commit_updated_at(resolved_root, remote_ref)
    local_ahead_count, remote_ahead_count = _ahead_counts(resolved_root, "HEAD", remote_ref) if has_head_commit else (0, _rev_count(resolved_root, remote_ref))

    if remote_ahead_count and changed_files:
        return GitHubPullResult(
            bundle_root=resolved_root,
            remote_name=remote_name,
            remote_url=effective_remote_url,
            branch=branch,
            dry_run=False,
            changed_files=changed_files,
            session_changed_files=change_groups.sessions,
            skill_changed_files=change_groups.skills,
            other_changed_files=change_groups.other,
            local_commit_hash=local_commit_hash,
            local_updated_at=local_updated_at,
            remote_commit_hash=remote_commit_hash,
            remote_updated_at=remote_updated_at,
            local_ahead_count=local_ahead_count,
            remote_ahead_count=remote_ahead_count,
            remote_checked=True,
            remote_branch_exists=True,
            skipped_reason="local_changes_block_pull",
            commands=commands,
        )

    pulled = False
    merged_remote = False
    conflict = False
    conflict_files: list[str] = []
    skipped_reason = ""
    if not has_head_commit:
        _run_git(resolved_root, ["checkout", "-B", branch, remote_ref], commands)
        pulled = True
    elif remote_ahead_count == 0:
        skipped_reason = "already_up_to_date"
    elif local_ahead_count == 0 and _is_ancestor(resolved_root, "HEAD", remote_ref):
        _run_git(resolved_root, ["merge", "--ff-only", remote_ref], commands)
        pulled = True
    else:
        merge_result = _merge_remote_ref(resolved_root, remote_ref, commands)
        if merge_result.conflict:
            conflict = True
            conflict_files = merge_result.conflict_files
            skipped_reason = "merge_conflict"
        else:
            merged_remote = True

    has_head_after_pull = _has_head_commit(resolved_root)
    return GitHubPullResult(
        bundle_root=resolved_root,
        remote_name=remote_name,
        remote_url=effective_remote_url,
        branch=branch,
        dry_run=False,
        changed_files=changed_files,
        session_changed_files=change_groups.sessions,
        skill_changed_files=change_groups.skills,
        other_changed_files=change_groups.other,
        local_commit_hash=_commit_hash(resolved_root, "HEAD") if has_head_after_pull else local_commit_hash,
        local_updated_at=_commit_updated_at(resolved_root, "HEAD") if has_head_after_pull else local_updated_at,
        remote_commit_hash=remote_commit_hash,
        remote_updated_at=remote_updated_at,
        local_ahead_count=local_ahead_count,
        remote_ahead_count=remote_ahead_count,
        remote_checked=True,
        remote_branch_exists=True,
        pulled=pulled,
        merged_remote=merged_remote,
        conflict=conflict,
        conflict_files=conflict_files,
        skipped_reason=skipped_reason,
        commands=commands,
    )


def sync_bundles_to_github(
    paths: CodexPaths,
    *,
    remote_name: str = DEFAULT_GITHUB_REMOTE_NAME,
    branch: str = DEFAULT_GITHUB_SYNC_BRANCH,
    bundle_root: Optional[Path] = None,
    message: str = DEFAULT_GITHUB_SYNC_MESSAGE,
    dry_run: bool = False,
    push: bool = True,
) -> GitHubSyncResult:
    resolved_root = normalize_bundle_root(paths, bundle_root, paths.default_bundle_root, label="GitHub sync bundle root")
    if not resolved_root.is_dir():
        raise ToolkitError(f"Missing bundle root: {resolved_root}")

    branch = (branch or DEFAULT_GITHUB_SYNC_BRANCH).strip() or DEFAULT_GITHUB_SYNC_BRANCH
    remote_name = (remote_name or DEFAULT_GITHUB_REMOTE_NAME).strip() or DEFAULT_GITHUB_REMOTE_NAME
    message = (message or DEFAULT_GITHUB_SYNC_MESSAGE).strip() or DEFAULT_GITHUB_SYNC_MESSAGE
    commands: list[str] = []

    repo_exists = _is_git_repo(resolved_root)
    if not repo_exists:
        raise ToolkitError("GitHub sync is not connected. Create a dedicated GitHub repo, then run connect-github first.")
    existing_remote_url = _get_remote_url(resolved_root, remote_name) if repo_exists else ""
    effective_remote_url = existing_remote_url.strip()
    if not effective_remote_url:
        raise ToolkitError("GitHub sync remote is missing. Run connect-github with a dedicated bundles repository URL first.")
    _reject_project_code_remote(resolved_root, effective_remote_url)

    if dry_run:
        changed_files = _collect_dry_run_changes(resolved_root, repo_exists=repo_exists)
        change_groups = _group_bundle_changes(changed_files)
        head_exists = _has_head_commit(resolved_root)
        return GitHubSyncResult(
            bundle_root=resolved_root,
            remote_name=remote_name,
            remote_url=effective_remote_url,
            branch=branch,
            dry_run=True,
            push_enabled=push,
            initialized_repo=False,
            configured_remote=False,
            changed_files=changed_files,
            session_changed_files=change_groups.sessions,
            skill_changed_files=change_groups.skills,
            other_changed_files=change_groups.other,
            committed=bool(changed_files),
            remote_checked=bool(push),
            pushed=bool(push and effective_remote_url and (changed_files or head_exists)),
            skipped_reason=_dry_run_skip_reason(changed_files, push=push, head_exists=head_exists),
            commands=_planned_commands(
                resolved_root,
                remote_name=remote_name,
                branch=branch,
                message=message,
                changed_files=changed_files,
                push=push,
                head_exists=head_exists,
            ),
        )

    changed_files = _git_status_paths(resolved_root)
    change_groups = _group_bundle_changes(changed_files)
    if changed_files:
        _run_git(resolved_root, ["add", "-A"], commands)
        if _has_staged_changes(resolved_root):
            _run_git(resolved_root, ["commit", "-m", message], commands)
            committed = True
            commit_hash = _git_output(resolved_root, ["rev-parse", "--short", "HEAD"])
        else:
            committed = False
            commit_hash = ""
    else:
        committed = False
        commit_hash = _git_output(resolved_root, ["rev-parse", "--short", "HEAD"], check=False)

    remote_integration = _RemoteIntegrationResult()
    pushed = False
    skipped_reason = ""
    if push:
        remote_integration = _integrate_remote_before_push(
            resolved_root,
            remote_name=remote_name,
            branch=branch,
            commands=commands,
        )
        if remote_integration.conflict:
            return GitHubSyncResult(
                bundle_root=resolved_root,
                remote_name=remote_name,
                remote_url=effective_remote_url,
                branch=branch,
                dry_run=False,
                push_enabled=push,
                initialized_repo=False,
                configured_remote=False,
                changed_files=changed_files,
                session_changed_files=change_groups.sessions,
                skill_changed_files=change_groups.skills,
                other_changed_files=change_groups.other,
                committed=committed,
                commit_hash=_git_output(resolved_root, ["rev-parse", "--short", "HEAD"], check=False),
                remote_checked=remote_integration.remote_checked,
                pulled=remote_integration.pulled,
                merged_remote=remote_integration.merged_remote,
                conflict=True,
                conflict_files=remote_integration.conflict_files,
                pushed=False,
                skipped_reason="merge_conflict",
                commands=commands,
            )
        commit_hash = _git_output(resolved_root, ["rev-parse", "--short", "HEAD"], check=False)
        if _has_head_commit(resolved_root):
            _run_git(resolved_root, ["push", "-u", remote_name, f"HEAD:{branch}"], commands)
            pushed = True
        else:
            skipped_reason = "no_commits_to_push"
    elif not changed_files:
        skipped_reason = "no_changes"

    return GitHubSyncResult(
        bundle_root=resolved_root,
        remote_name=remote_name,
        remote_url=effective_remote_url,
        branch=branch,
        dry_run=False,
        push_enabled=push,
        initialized_repo=False,
        configured_remote=False,
        changed_files=changed_files,
        session_changed_files=change_groups.sessions,
        skill_changed_files=change_groups.skills,
        other_changed_files=change_groups.other,
        committed=committed,
        commit_hash=commit_hash,
        remote_checked=remote_integration.remote_checked,
        pulled=remote_integration.pulled,
        merged_remote=remote_integration.merged_remote,
        conflict=remote_integration.conflict,
        conflict_files=remote_integration.conflict_files,
        pushed=pushed,
        skipped_reason=skipped_reason,
        commands=commands,
    )


def _is_git_repo(bundle_root: Path) -> bool:
    return _git_root(bundle_root) == _normalize_path(bundle_root)


def _git_root(path: Path) -> str:
    result = _git_process(path, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return ""
    return _normalize_path(Path(result.stdout.strip()))


def _normalize_path(path: Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _reject_project_code_remote(bundle_root: Path, target_remote_url: str) -> None:
    matching_remote = _matching_project_code_remote(bundle_root, target_remote_url)
    if matching_remote:
        raise ToolkitError(
            "GitHub sync target must be a dedicated bundles repository, "
            f"not the project source repository remote: {matching_remote}"
        )


def _matching_project_code_remote(bundle_root: Path, target_remote_url: str) -> str:
    if not target_remote_url:
        return ""

    parent_git_root = _parent_git_root(bundle_root)
    if not parent_git_root:
        return ""

    target_identity = _remote_identity(target_remote_url)
    parent_remote_urls = _remote_urls(Path(parent_git_root))
    for parent_remote_url in parent_remote_urls:
        if target_identity and target_identity == _remote_identity(parent_remote_url):
            return parent_remote_url
    return ""


def _parent_git_root(bundle_root: Path) -> str:
    parent = Path(bundle_root).expanduser().parent
    parent_root = _git_root(parent)
    if not parent_root:
        return ""
    if parent_root == _normalize_path(bundle_root):
        return ""
    return parent_root


def _remote_urls(git_root: Path) -> list[str]:
    raw = _git_output(git_root, ["remote", "-v"], check=False)
    urls: list[str] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in urls:
            urls.append(parts[1])
    return urls


def _remote_identity(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""

    if "://" not in url and "@" in url and ":" in url:
        host, path = url.split(":", 1)
        host = host.split("@", 1)[-1]
        return _normalize_remote_path(host, path)

    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname or parsed.netloc
        return _normalize_remote_path(host, parsed.path)

    return _normalize_path(Path(url))


def _normalize_remote_path(host: str, path: str) -> str:
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    return f"{host.lower()}/{normalized_path.lower()}"


def _get_remote_url(bundle_root: Path, remote_name: str) -> str:
    return _git_output(bundle_root, ["remote", "get-url", remote_name], check=False)


def _git_status_paths(bundle_root: Path) -> list[str]:
    raw_status = _git_output(bundle_root, ["status", "--porcelain", "--untracked-files=all"], check=False)
    paths: list[str] = []
    for raw in raw_status.splitlines():
        if not raw:
            continue
        path = raw[3:] if len(raw) > 3 and raw[2] == " " else raw[2:].lstrip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        normalized_path = _normalize_git_relative_path(path)
        if normalized_path:
            paths.append(normalized_path)
    return sorted(paths)


def _collect_dry_run_changes(bundle_root: Path, *, repo_exists: bool) -> list[str]:
    if repo_exists:
        return _git_status_paths(bundle_root)

    paths = []
    for path in bundle_root.rglob("*"):
        if path.is_dir() or ".git" in path.parts:
            continue
        paths.append(path.relative_to(bundle_root).as_posix())
    return sorted(paths)


def _group_bundle_changes(changed_files: list[str]) -> _ChangeGroups:
    sessions: list[str] = []
    skills: list[str] = []
    other: list[str] = []
    for changed_file in changed_files:
        normalized = _normalize_git_relative_path(changed_file)
        if not normalized:
            continue
        parts = tuple(part for part in normalized.split("/") if part)
        if "sessions" in parts:
            sessions.append(normalized)
        elif "skills" in parts:
            skills.append(normalized)
        else:
            other.append(normalized)
    return _ChangeGroups(
        sessions=sorted(sessions),
        skills=sorted(skills),
        other=sorted(other),
    )


def _normalize_git_relative_path(raw_path: str) -> str:
    normalized = (raw_path or "").strip().replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.strip("/")
    return "/".join(part for part in normalized.split("/") if part and part != ".")


def _has_staged_changes(bundle_root: Path) -> bool:
    result = _git_process(bundle_root, ["diff", "--cached", "--quiet"], check=False)
    return result.returncode != 0


def _has_head_commit(bundle_root: Path) -> bool:
    result = _git_process(bundle_root, ["rev-parse", "--verify", "HEAD"], check=False)
    return result.returncode == 0


def _git_output(bundle_root: Path, args: list[str], *, check: bool = True) -> str:
    result = _git_process(bundle_root, args, check=check)
    return result.stdout.strip()


def _run_git(bundle_root: Path, args: list[str], commands: list[str]) -> str:
    commands.append(_format_git_command(bundle_root, args))
    return _git_output(bundle_root, args)


def _remote_status_snapshot(
    bundle_root: Path,
    *,
    remote_name: str,
    branch: str,
    has_head_commit: bool,
) -> _RemoteStatusSnapshot:
    commands: list[str] = []
    try:
        remote_branch_exists = _remote_branch_exists(bundle_root, remote_name=remote_name, branch=branch, commands=commands)
        if not remote_branch_exists:
            return _RemoteStatusSnapshot(remote_checked=True)

        _run_git(bundle_root, ["fetch", remote_name, branch], commands)
        remote_ref = _remote_ref(remote_name, branch)
        local_ahead_count = 0
        remote_ahead_count = _rev_count(bundle_root, remote_ref)
        if has_head_commit:
            local_ahead_count, remote_ahead_count = _ahead_counts(bundle_root, "HEAD", remote_ref)
        return _RemoteStatusSnapshot(
            remote_checked=True,
            remote_branch_exists=True,
            remote_commit_hash=_commit_hash(bundle_root, remote_ref),
            remote_updated_at=_commit_updated_at(bundle_root, remote_ref),
            local_ahead_count=local_ahead_count,
            remote_ahead_count=remote_ahead_count,
        )
    except ToolkitError as exc:
        return _RemoteStatusSnapshot(remote_checked=True, remote_check_error=str(exc))


def _commit_hash(bundle_root: Path, ref: str) -> str:
    return _git_output(bundle_root, ["rev-parse", "--short", ref], check=False)


def _commit_updated_at(bundle_root: Path, ref: str) -> str:
    return _git_output(bundle_root, ["show", "-s", "--format=%cI", ref], check=False)


def _ahead_counts(bundle_root: Path, left_ref: str, right_ref: str) -> tuple[int, int]:
    raw = _git_output(bundle_root, ["rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"], check=False)
    parts = raw.split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def _rev_count(bundle_root: Path, ref: str) -> int:
    raw = _git_output(bundle_root, ["rev-list", "--count", ref], check=False)
    try:
        return int(raw.strip())
    except ValueError:
        return 0


def _integrate_remote_before_push(
    bundle_root: Path,
    *,
    remote_name: str,
    branch: str,
    commands: list[str],
) -> _RemoteIntegrationResult:
    remote_checked = True
    if not _remote_branch_exists(bundle_root, remote_name=remote_name, branch=branch, commands=commands):
        return _RemoteIntegrationResult(remote_checked=remote_checked)

    _run_git(bundle_root, ["fetch", remote_name, branch], commands)
    remote_ref = _remote_ref(remote_name, branch)
    if not _has_head_commit(bundle_root):
        _run_git(bundle_root, ["checkout", "-B", branch, remote_ref], commands)
        return _RemoteIntegrationResult(remote_checked=remote_checked, pulled=True)

    if _is_ancestor(bundle_root, remote_ref, "HEAD"):
        return _RemoteIntegrationResult(remote_checked=remote_checked)

    if _is_ancestor(bundle_root, "HEAD", remote_ref):
        _run_git(bundle_root, ["merge", "--ff-only", remote_ref], commands)
        return _RemoteIntegrationResult(remote_checked=remote_checked, pulled=True)

    merge_result = _merge_remote_ref(bundle_root, remote_ref, commands)
    if not merge_result.conflict:
        return _RemoteIntegrationResult(remote_checked=remote_checked, merged_remote=True)

    return _RemoteIntegrationResult(
        remote_checked=remote_checked,
        conflict=True,
        conflict_files=merge_result.conflict_files,
    )


def _merge_remote_ref(bundle_root: Path, remote_ref: str, commands: list[str]) -> _RemoteIntegrationResult:
    merge_args = ["merge", "--no-edit", "--allow-unrelated-histories", remote_ref]
    commands.append(_format_git_command(bundle_root, merge_args))
    merge_result = _git_process(bundle_root, merge_args, check=False)
    if merge_result.returncode == 0:
        return _RemoteIntegrationResult(merged_remote=True)

    conflict_files = _conflict_paths(bundle_root)
    abort_args = ["merge", "--abort"]
    commands.append(_format_git_command(bundle_root, abort_args))
    _git_process(bundle_root, abort_args, check=False)
    return _RemoteIntegrationResult(conflict=True, conflict_files=conflict_files)


def _remote_branch_exists(
    bundle_root: Path,
    *,
    remote_name: str,
    branch: str,
    commands: list[str],
) -> bool:
    args = ["ls-remote", "--heads", remote_name, branch]
    commands.append(_format_git_command(bundle_root, args))
    result = _git_process(bundle_root, args, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        command = _format_git_command(bundle_root, args)
        raise ToolkitError(f"Git command failed: {command}\n{detail}")
    return bool(result.stdout.strip())


def _remote_ref(remote_name: str, branch: str) -> str:
    return f"refs/remotes/{remote_name}/{branch}"


def _is_ancestor(bundle_root: Path, ancestor: str, descendant: str) -> bool:
    result = _git_process(bundle_root, ["merge-base", "--is-ancestor", ancestor, descendant], check=False)
    if result.returncode in {0, 1}:
        return result.returncode == 0
    detail = (result.stderr or result.stdout or "").strip()
    raise ToolkitError(f"Git merge-base failed: {detail}")


def _conflict_paths(bundle_root: Path) -> list[str]:
    raw_status = _git_output(bundle_root, ["status", "--porcelain", "--untracked-files=all"], check=False)
    conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
    paths: list[str] = []
    for raw in raw_status.splitlines():
        if len(raw) < 4:
            continue
        if raw[:2] in conflict_codes:
            path = raw[3:] if len(raw) > 3 and raw[2] == " " else raw[2:].lstrip()
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[-1]
            normalized_path = _normalize_git_relative_path(path)
            if normalized_path:
                paths.append(normalized_path)
    return sorted(paths)


def _git_process(bundle_root: Path, args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Codex Session Toolkit")
    env.setdefault("GIT_AUTHOR_EMAIL", "codex-session-toolkit@example.local")
    env.setdefault("GIT_COMMITTER_NAME", "Codex Session Toolkit")
    env.setdefault("GIT_COMMITTER_EMAIL", "codex-session-toolkit@example.local")
    result = subprocess.run(
        ["git", "-C", str(bundle_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        command = _format_git_command(bundle_root, args)
        raise ToolkitError(f"Git command failed: {command}\n{detail}")
    return result


def _planned_commands(
    bundle_root: Path,
    *,
    remote_name: str,
    branch: str,
    message: str,
    changed_files: list[str],
    push: bool,
    head_exists: bool,
) -> list[str]:
    commands = []
    if changed_files:
        commands.append(_format_git_command(bundle_root, ["add", "-A"]))
        commands.append(_format_git_command(bundle_root, ["commit", "-m", message]))
    if push and (changed_files or head_exists):
        commands.append(_format_git_command(bundle_root, ["ls-remote", "--heads", remote_name, branch]))
        commands.append(_format_git_command(bundle_root, ["fetch", remote_name, branch]))
        commands.append(_format_git_command(bundle_root, ["merge", "--no-edit", "--allow-unrelated-histories", _remote_ref(remote_name, branch)]))
        commands.append(_format_git_command(bundle_root, ["push", "-u", remote_name, f"HEAD:{branch}"]))
    return commands


def _dry_run_skip_reason(changed_files: list[str], *, push: bool, head_exists: bool) -> str:
    if changed_files:
        return ""
    if push and not head_exists:
        return "no_commits_to_push"
    if not push:
        return "no_changes"
    return ""


def _planned_connect_commands(
    bundle_root: Path,
    *,
    remote_name: str,
    remote_url: str,
    branch: str,
    needs_init: bool,
    needs_remote_config: bool,
    existing_remote_url: str,
) -> list[str]:
    commands = []
    if needs_init:
        commands.append(_format_git_command(bundle_root, ["init"]))
        commands.append(_format_git_command(bundle_root, ["checkout", "-B", branch]))
    if needs_remote_config:
        remote_command = "set-url" if existing_remote_url else "add"
        commands.append(_format_git_command(bundle_root, ["remote", remote_command, remote_name, remote_url]))
    return commands


def _format_git_command(bundle_root: Path, args: list[str]) -> str:
    return " ".join(["git", "-C", str(bundle_root), *args])
