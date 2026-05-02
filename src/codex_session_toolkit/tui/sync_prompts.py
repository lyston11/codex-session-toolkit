"""Lightweight GitHub sync prompts for TUI workflows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Sequence

from ..errors import ToolkitError
from ..models import GitHubSyncStatus
from ..services.github_sync import DEFAULT_GITHUB_SYNC_MESSAGE
from .terminal import Ansi, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp


LOCAL_SYNC_HINT_TTL_SECONDS = 5.0
BUNDLE_WORKSPACE_WRITE_COMMANDS = {
    "export",
    "export-project",
    "export-desktop-all",
    "export-active-desktop-all",
    "export-cli-all",
    "export-skills",
}
GITHUB_SYNC_COMMANDS = {"connect-github", "pull-github", "sync-github"}


def invalidate_github_sync_hint(app: "ToolkitTuiApp") -> None:
    setattr(app, "_github_sync_hint_cache", None)


def github_sync_hint_lines(app: "ToolkitTuiApp", *, force: bool = False) -> list[str]:
    cached = getattr(app, "_github_sync_hint_cache", None)
    now = time.monotonic()
    if not force and cached:
        expires_at, lines = cached
        if now < expires_at:
            return list(lines)

    try:
        status = app._github_sync_status(check_remote=False)
    except (OSError, ToolkitError):
        lines = [f"{style_text('GitHub', Ansi.DIM)} : 状态暂不可用 · 可稍后从首页 [5] 查看"]
    else:
        lines = [_format_local_sync_hint(status)]

    setattr(app, "_github_sync_hint_cache", (now + LOCAL_SYNC_HINT_TTL_SECONDS, list(lines)))
    return lines


def maybe_offer_github_sync_after_action(
    app: "ToolkitTuiApp",
    *,
    action_name: str,
    cli_args: Sequence[str],
    result_code: int,
    dry_run: bool,
) -> bool:
    command_name = cli_args[0] if cli_args else ""
    if command_name in GITHUB_SYNC_COMMANDS or command_name in BUNDLE_WORKSPACE_WRITE_COMMANDS:
        invalidate_github_sync_hint(app)
    if result_code != 0 or dry_run or command_name not in BUNDLE_WORKSPACE_WRITE_COMMANDS:
        return False

    try:
        status = app._github_sync_status(check_remote=False)
    except (OSError, ToolkitError):
        return False
    invalidate_github_sync_hint(app)
    if not status.is_connected or not status.changed_files:
        return False

    branch = status.branch or "main"
    choice = app._prompt_choice(
        title="稍后同步",
        prompt_label="这个操作已完成，是否现在同步",
        help_lines=[
            f"{style_text('刚完成', Ansi.DIM)} : {action_name}",
            f"{style_text('GitHub', Ansi.DIM)} : 已连接 · 本机有 {len(status.changed_files)} 个待推送",
            f"{style_text('同步范围', Ansi.DIM)} : 会话 Bundle 和 Skills Bundle",
            "现在推送会显示进度；也可以稍后从首页 [5] GitHub / Sync 进入同步中心。",
        ],
        choices=[
            ("g", f"推送到 {status.remote_name}/{branch}"),
            ("5", "打开同步中心"),
            ("q", "稍后同步"),
        ],
        default="q",
    )
    if choice == "g":
        sync_args = ["sync-github", "--branch", branch, "--message", DEFAULT_GITHUB_SYNC_MESSAGE]
        app._run_action(
            "推送本机更新到 GitHub",
            sync_args,
            dry_run=False,
            runner=lambda args=sync_args: app._run_toolkit(list(args)),
            danger=False,
            use_progress=True,
        )
        return True
    if choice == "5":
        app._show_github_sync_status()
        return True
    return True


def _format_local_sync_hint(status: GitHubSyncStatus) -> str:
    label = style_text("GitHub", Ansi.DIM)
    if status.uses_project_source_remote:
        return f"{label} : 需要重新连接独立仓库 · 首页 [5] 处理"
    if not status.is_connected:
        return f"{label} : 未连接 · 首页 [5] 可连接独立仓库"
    if status.changed_files:
        return f"{label} : 已连接 · 本机有 {len(status.changed_files)} 个待推送 · 首页 [5] 同步"
    return f"{label} : 已连接 · 本地无待推送变更 · 首页 [5] 同步"
