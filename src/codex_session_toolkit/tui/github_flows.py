"""GitHub sync status panels for the interactive TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ..models import GitHubSyncStatus
from ..services.github_sync import get_github_sync_status
from .progress_flows import run_callable_with_progress
from .terminal import Ansi, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp


def github_sync_status(app: "ToolkitTuiApp", *, check_remote: bool = False) -> GitHubSyncStatus:
    return get_github_sync_status(app.paths, check_remote=check_remote)


def github_sync_status_lines(
    app: "ToolkitTuiApp",
    status: Optional[GitHubSyncStatus] = None,
) -> List[str]:
    status = status or github_sync_status(app)
    if status.is_connected:
        state = style_text("已连接独立仓库", Ansi.BOLD, Ansi.GREEN)
    elif status.uses_project_source_remote:
        state = style_text("错误：指向项目源码仓库", Ansi.BOLD, Ansi.RED)
    elif status.is_git_repo:
        state = style_text("未完成连接", Ansi.BOLD, Ansi.YELLOW)
    else:
        state = style_text("未连接", Ansi.BOLD, Ansi.YELLOW)

    lines = [
        f"{style_text('同步状态', Ansi.DIM)} : {state}",
        f"{style_text('Bundle 根目录', Ansi.DIM)} : {status.bundle_root}",
        f"{style_text('Git 仓库', Ansi.DIM)} : {'是，且为独立仓库' if status.is_git_repo else '否'}",
        f"{style_text('Remote', Ansi.DIM)} : {status.remote_name} {status.remote_url or '（未配置）'}",
        f"{style_text('目标分支', Ansi.DIM)} : {status.branch or 'main'}",
        f"{style_text('本地最新提交', Ansi.DIM)} : {status.local_commit_hash or '（无）'}",
        f"{style_text('本地更新时间', Ansi.DIM)} : {status.local_updated_at or '（无）'}",
        f"{style_text('远端检查', Ansi.DIM)} : {'已检查' if status.remote_checked else '未检查'}",
        f"{style_text('远端最新提交', Ansi.DIM)} : {status.remote_commit_hash or '（无）'}",
        f"{style_text('远端更新时间', Ansi.DIM)} : {status.remote_updated_at or '（无）'}",
        f"{style_text('本地领先', Ansi.DIM)} : {status.local_ahead_count}",
        f"{style_text('远端领先', Ansi.DIM)} : {status.remote_ahead_count}",
        f"{style_text('待同步变更', Ansi.DIM)} : {len(status.changed_files)}",
        f"{style_text('会话 Bundle 变更', Ansi.DIM)} : {len(status.session_changed_files)}",
        f"{style_text('Skills Bundle 变更', Ansi.DIM)} : {len(status.skill_changed_files)}",
        f"{style_text('其他变更', Ansi.DIM)} : {len(status.other_changed_files)}",
        f"{style_text('说明', Ansi.DIM)} : {status.message}",
    ]
    if status.uses_project_source_remote:
        lines.append(f"{style_text('源码仓库 remote', Ansi.DIM)} : {status.project_remote_url}")
        lines.append("处理：请新建一个专门保存 Bundle 的 GitHub 仓库，然后重新连接。")
    elif status.remote_check_error:
        lines.append(f"{style_text('远端检查失败', Ansi.DIM)} : {status.remote_check_error}")
    elif not status.is_connected:
        lines.append("下一步：先在 GitHub 新建独立仓库，再选择“连接独立 GitHub 仓库”。")
    else:
        lines.append("同步范围：会话 Bundle 和 Skills Bundle 一起同步；不触碰 ~/.codex 原始会话目录。")
        lines.append("Pull：远端领先时先拉取；Push：本地领先或有工作区变更时再推送。")
        lines.append("冲突策略：拉取/推送前会检查远端更新时间；可自动合并则合并，文件冲突时停止并列出冲突文件。")

    for changed_path in status.changed_files[:8]:
        lines.append(f"变更：{changed_path}")
    if len(status.changed_files) > 8:
        lines.append(f"... 还有 {len(status.changed_files) - 8} 个变更")
    return lines


def show_github_sync_status(app: "ToolkitTuiApp") -> None:
    local_status = github_sync_status(app, check_remote=False)
    if local_status.is_connected:
        status = _github_remote_status_with_progress(app, local_status, title="GitHub 同步状态")
    else:
        status = local_status
    app._show_detail_panel(
        "GitHub 同步状态",
        github_sync_status_lines(app, status),
        border_codes=(Ansi.DIM, Ansi.YELLOW),
    )


def _github_remote_status_with_progress(
    app: "ToolkitTuiApp",
    local_status: GitHubSyncStatus,
    *,
    title: str,
) -> GitHubSyncStatus:
    detail_lines = [
        f"{style_text('当前状态', Ansi.DIM)} : 正在检查远端更新时间",
        f"{style_text('Bundle 根目录', Ansi.DIM)} : {local_status.bundle_root}",
        f"{style_text('Remote', Ansi.DIM)} : {local_status.remote_name} {local_status.remote_url}",
        f"{style_text('目标分支', Ansi.DIM)} : {local_status.branch or 'main'}",
        f"{style_text('本地更新时间', Ansi.DIM)} : {local_status.local_updated_at or '（无）'}",
        f"{style_text('待同步变更', Ansi.DIM)} : {len(local_status.changed_files)}",
    ]
    return run_callable_with_progress(
        app,
        title=title,
        detail_lines=detail_lines,
        task=lambda: github_sync_status(app, check_remote=True),
    )
