"""Action execution flows extracted from the TUI app shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from .maintenance_modes import run_cleanup_mode, run_clone_mode
from .progress_flows import run_callable_with_progress, run_cli_args_with_progress
from .sync_prompts import maybe_offer_github_sync_after_action
from .terminal import Ansi, render_box, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp
    from .view_models import TuiMenuAction


@dataclass(frozen=True)
class GitHubConnectSelection:
    remote_url: str
    branch: str
    push_after_connect: bool
    message: str


def build_desktop_repair_cli_args(target_provider: str, *, include_cli: bool, dry_run: bool) -> list[str]:
    cli_args = ["repair-desktop"]
    if target_provider:
        cli_args.append(target_provider)
    if include_cli:
        cli_args.append("--include-cli")
    if dry_run:
        cli_args.append("--dry-run")
    return cli_args


def _github_status_snapshot_with_progress(app: "ToolkitTuiApp", *, title: str):
    return run_callable_with_progress(
        app,
        title=title,
        detail_lines=[
            "正在读取本地同步状态。",
            "正在准备同步信息。",
        ],
        task=lambda: app._github_sync_status(),
    )


def _user_action_progress_lines(
    app: "ToolkitTuiApp",
    *,
    action_name: str,
    cli_args: Sequence[str],
    dry_run: bool,
    color: str,
) -> list[str]:
    command_name = cli_args[0] if cli_args else ""
    if command_name in {"connect-github", "pull-github", "sync-github"}:
        mode = "预演，不会写入" if dry_run else "直接执行"
        bundle_root_label = getattr(app.context, "bundle_root_label", "./codex_bundles")
        return [
            f"{style_text('当前操作', Ansi.DIM)} : {style_text(action_name, Ansi.BOLD, color)}",
            f"{style_text('同步目录', Ansi.DIM)} : {bundle_root_label}",
            f"{style_text('同步内容', Ansi.DIM)} : 会话 Bundle 和 Skills Bundle",
            f"{style_text('执行模式', Ansi.DIM)} : {mode}",
        ]
    return [
        f"{style_text('当前操作', Ansi.DIM)} : {style_text(action_name, Ansi.BOLD, color)}",
        f"{style_text('执行模式', Ansi.DIM)} : {'预演，不会写入' if dry_run else '直接执行'}",
    ]


def _collect_github_connect_selection(app: "ToolkitTuiApp") -> Optional[GitHubConnectSelection]:
    status = _github_status_snapshot_with_progress(app, title="连接独立 GitHub 仓库")
    status_lines = app._github_sync_status_lines(status)
    if status.remote_url and not status.uses_project_source_remote:
        status_lines.append("可以沿用当前 remote，也可以输入新的独立 Bundle 仓库地址。")
    remote_url = app._prompt_value(
        title="连接独立 GitHub 仓库",
        prompt_label="独立 GitHub 仓库 URL",
        help_lines=status_lines + [
            "",
            "请先在 GitHub 上创建一个新的独立仓库，专门保存 codex_bundles。",
            "不要填写当前项目源码仓库地址；工具会拒绝连接到源码仓库 remote。",
            "示例：git@github.com:you/codex-bundles.git",
        ],
        default=(status.remote_url if status.remote_url and not status.uses_project_source_remote else ""),
        allow_empty=False,
    )
    if remote_url is None:
        return None
    branch = app._prompt_value(
        title="连接独立 GitHub 仓库",
        prompt_label="目标分支",
        help_lines=["默认推送到 main。"],
        default=status.branch or "main",
        allow_empty=False,
    )
    if branch is None:
        return None
    push_after_connect = app._confirm_toggle(
        title="连接独立 GitHub 仓库",
        question="连接成功后是否立即推送本机 Bundle",
        yes_label="y",
        no_label="n",
        default_yes=True,
    )
    message = "Sync Codex bundles"
    if push_after_connect:
        message_answer = app._prompt_value(
            title="连接独立 GitHub 仓库",
            prompt_label="首次推送提交信息",
            help_lines=["连接成功后会把本机会话 Bundle 和 Skills Bundle 首次推送到这个独立仓库。"],
            default=message,
            allow_empty=False,
        )
        if message_answer is None:
            return None
        message = message_answer
    return GitHubConnectSelection(
        remote_url=remote_url,
        branch=branch,
        push_after_connect=push_after_connect,
        message=message,
    )


def _build_github_connect_request(selection: GitHubConnectSelection, *, dry_run: bool) -> tuple[str, list[str]]:
    args = ["connect-github", selection.remote_url, "--branch", selection.branch]
    if selection.push_after_connect:
        args.extend(["--push-after-connect", "--message", selection.message])
    if dry_run:
        args.append("--dry-run")
    action_name = "连接独立 GitHub 仓库"
    if selection.push_after_connect:
        action_name += "并首次推送"
    if dry_run:
        action_name += "（Dry-run）"
    return action_name, args


def _github_connected_status_or_none(app: "ToolkitTuiApp", *, title: str):
    status = _github_status_snapshot_with_progress(app, title=title)
    status_lines = app._github_sync_status_lines(status)
    if not status.is_connected:
        app._show_detail_panel(
            title,
            status_lines + ["请先返回 GitHub / Sync，选择“连接独立 GitHub 仓库”。"],
            border_codes=(Ansi.DIM, Ansi.YELLOW),
        )
        return None
    return status


def _prompt_github_pull_dry_run(app: "ToolkitTuiApp", status) -> Optional[bool]:
    branch = status.branch or "main"
    summary_lines = app._github_sync_status_lines(status) + [
        "",
        f"{style_text('拉取来源', Ansi.DIM)} : {status.remote_name}/{branch}",
        f"{style_text('同步范围', Ansi.DIM)} : 会话 Bundle 和 Skills Bundle",
        "本地未提交变更如果会被覆盖，工具会停止并提示先处理本地变更。",
    ]
    confirm = app._prompt_choice(
        title="从 GitHub 拉取更新",
        prompt_label="选择拉取方式",
        help_lines=summary_lines,
        choices=[
            ("p", f"从 {status.remote_name}/{branch} 拉取"),
            ("d", "Dry-run 预览"),
            ("q", "返回"),
        ],
        default="p",
    )
    if confirm not in {"p", "d"}:
        return None
    return confirm == "d"


def _build_github_pull_request(status, *, dry_run: bool) -> tuple[str, list[str]]:
    args = ["pull-github", "--branch", status.branch or "main"]
    if dry_run:
        args.append("--dry-run")
    action_name = "从 GitHub 拉取更新"
    if dry_run:
        action_name += "（Dry-run）"
    return action_name, args


def _prompt_github_push_dry_run(app: "ToolkitTuiApp", status) -> Optional[bool]:
    branch = status.branch or "main"
    summary_lines = [
        f"{style_text('推送目标', Ansi.DIM)} : {status.remote_name}/{branch}",
        f"{style_text('同步范围', Ansi.DIM)} : 会话 Bundle 和 Skills Bundle",
        f"{style_text('待同步变更', Ansi.DIM)} : {len(status.changed_files)}",
        f"{style_text('会话变更', Ansi.DIM)} : {len(status.session_changed_files)}",
        f"{style_text('Skills 变更', Ansi.DIM)} : {len(status.skill_changed_files)}",
        "推送前会检查远端更新；冲突时会停止并列出文件。",
    ]
    confirm = app._prompt_choice(
        title="推送本机更新到 GitHub",
        prompt_label="选择推送方式",
        help_lines=summary_lines,
        choices=[
            ("p", f"推送到 {status.remote_name}/{branch}"),
            ("d", "Dry-run 预览"),
            ("q", "返回"),
        ],
        default="p",
    )
    if confirm not in {"p", "d"}:
        return None
    return confirm == "d"


def _build_github_push_request(status, *, dry_run: bool) -> tuple[str, list[str]]:
    branch = status.branch or "main"
    args = ["sync-github", "--branch", branch, "--message", "Sync Codex bundles"]
    if dry_run:
        args.append("--dry-run")
    action_name = "推送本机更新到 GitHub"
    if dry_run:
        action_name += "（Dry-run）"
    return action_name, args


def resolve_menu_action_request(app: "ToolkitTuiApp", menu_action: "TuiMenuAction") -> tuple[Optional[str], Optional[list[str]]]:
    action_name = menu_action.label
    cli_args = list(menu_action.cli_args)

    if menu_action.action_id == "list_sessions":
        app._open_session_browser(mode="view")
        return None, None

    if menu_action.action_id == "project_sessions":
        app._open_project_session_browser()
        return None, None

    if menu_action.action_id == "browse_bundles":
        app._open_bundle_browser(mode="view")
        return None, None

    if menu_action.action_id == "list_skills":
        app._open_local_skill_browser(mode="view")
        return None, None

    if menu_action.action_id == "browse_skill_bundles":
        app._open_skill_bundle_browser(mode="view")
        return None, None

    if menu_action.action_id == "browse_backups":
        app._open_session_backup_browser(mode="view")
        return None, None

    if menu_action.action_id == "export_one":
        summary = app._open_session_browser(mode="select")
        if not summary:
            return None, None
        return f"导出会话 {summary.session_id} 为 Bundle", ["export", summary.session_id]

    if menu_action.action_id == "export_skills_all":
        return "导出全部自定义 Skills", ["export-skills"]

    if menu_action.action_id == "export_skill_one":
        selected = app._open_local_skill_browser(mode="select")
        if not selected:
            return None, None
        if selected.location_kind != "custom":
            app._show_detail_panel(
                "导出 Skill",
                ["系统/运行时 Skills 只记录元数据，不作为 standalone Skills Bundle 导出。"],
                border_codes=(Ansi.DIM, Ansi.YELLOW),
            )
            return None, None
        return f"导出 Skill {selected.relative_dir}", ["export-skills", selected.relative_dir]

    if menu_action.action_id == "import_one":
        bundle = app._open_bundle_browser(mode="select")
        if not bundle:
            return None, None
        desktop_visible = app._confirm_toggle(
            title="导入单个 Bundle 为会话",
            question="如果工作目录缺失，是否自动创建",
            yes_label="y",
            no_label="n",
            default_yes=False,
        )
        args = ["import"]
        if desktop_visible:
            args.append("--desktop-visible")
        args.append(str(bundle.bundle_dir))
        action_name = f"导入 Bundle {bundle.session_id} 为会话"
        if desktop_visible:
            action_name += "（自动创建目录）"
        return action_name, args

    if menu_action.action_id == "import_skill_bundle":
        bundle = app._open_skill_bundle_browser(mode="select")
        if not bundle:
            return None, None
        return f"导入 Skills Bundle {bundle.bundle_dir.name}", ["import-skill-bundle", str(bundle.bundle_dir)]

    if menu_action.action_id == "import_skill_bundles":
        machine_filter = app._prompt_value(
            title="批量导入 Skills Bundle",
            prompt_label="来源机器过滤",
            help_lines=[
                "留空表示导入全部 standalone Skills Bundle。",
                "也可以输入来源机器 key 或 label，只导入这一台设备导出的 Skills Bundle。",
            ],
            allow_empty=True,
        )
        args = ["import-skill-bundles"]
        if machine_filter:
            args.extend(["--machine", machine_filter])
        action_name = "批量导入 Skills Bundle"
        if machine_filter:
            action_name += f"（{machine_filter}）"
        return action_name, args

    if menu_action.action_id == "github_status":
        app._show_github_sync_status()
        return None, None

    if menu_action.action_id == "connect_github":
        selection = _collect_github_connect_selection(app)
        if selection is None:
            return None, None
        dry_run = app._prompt_execution_mode(
            title="连接独立 GitHub 仓库",
            default_dry_run=False,
        )
        if dry_run is None:
            return None, None
        return _build_github_connect_request(selection, dry_run=dry_run)

    if menu_action.action_id == "pull_github":
        status = _github_connected_status_or_none(app, title="从 GitHub 拉取更新")
        if status is None:
            return None, None
        dry_run = _prompt_github_pull_dry_run(app, status)
        if dry_run is None:
            return None, None
        return _build_github_pull_request(status, dry_run=dry_run)

    if menu_action.action_id == "sync_github":
        status = _github_connected_status_or_none(app, title="推送本机更新到 GitHub")
        if status is None:
            return None, None
        dry_run = _prompt_github_push_dry_run(app, status)
        if dry_run is None:
            return None, None
        return _build_github_push_request(status, dry_run=dry_run)

    if menu_action.action_id == "import_desktop_all":
        selection = app._select_batch_bundle_import_scope()
        if not selection:
            return None, None
        create_question = "如果工作目录缺失，是否自动创建"
        default_yes = False
        if selection.target_project_path:
            if Path(selection.target_project_path).exists():
                create_question = "如果目标项目路径或其子目录缺失，是否自动创建"
            else:
                create_question = "目标项目路径不存在，是否先创建后再导入"
                default_yes = True
        desktop_visible = app._confirm_toggle(
            title="批量导入 Bundle 为会话",
            question=create_question,
            yes_label="y",
            no_label="n",
            default_yes=default_yes,
        )
        args = ["import-desktop-all"]
        if selection.machine_filter:
            args.extend(["--machine", selection.machine_filter])
        if selection.export_group_filter:
            args.extend(["--export-group", selection.export_group_filter])
        if selection.project_filter:
            args.extend(["--project", selection.project_filter])
        if selection.target_project_path:
            args.extend(["--target-project-path", selection.target_project_path])
        if desktop_visible:
            args.append("--desktop-visible")
        action_name = f"批量导入 {selection.machine_label}/{selection.export_group_label}（{len(selection.entries)} 个 Bundle）"
        if selection.project_label:
            action_name = (
                f"批量导入 {selection.machine_label}/{selection.export_group_label}/"
                f"{selection.project_label}（{len(selection.entries)} 个 Bundle）"
            )
        if desktop_visible:
            action_name += "（自动创建目录）"
        return action_name, args

    return action_name, cli_args


def execute_menu_action(app: "ToolkitTuiApp", chosen_action: "TuiMenuAction") -> None:
    choice_id = chosen_action.action_id
    if choice_id == "provider_migration":
        while True:
            dry_run = app._prompt_execution_mode(
                title="迁移到当前 Provider",
                default_dry_run=False,
            )
            if dry_run is None:
                return

            cli_args = ["clone-provider"]
            if dry_run:
                cli_args.append("--dry-run")
            action_name = "迁移到当前 Provider（保留原会话，创建副本）"
            if dry_run:
                action_name += "（Dry-run）"
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda dry_run=dry_run: run_clone_mode(
                    target_provider=app.context.target_provider,
                    dry_run=dry_run,
                ),
                danger=False,
            )
            if not dry_run:
                return
        return

    if choice_id == "desktop_repair":
        include_cli = app._prompt_desktop_repair_scope()
        if include_cli is None:
            return

        while True:
            dry_run = app._prompt_execution_mode(
                title="修复会话在 Desktop 中显示",
                default_dry_run=True,
            )
            if dry_run is None:
                return

            cli_args = build_desktop_repair_cli_args(
                app.context.target_provider,
                include_cli=include_cli,
                dry_run=dry_run,
            )
            action_name = "修复会话在 Desktop 中显示"
            if include_cli:
                action_name += "并纳入 CLI 会话"
            if dry_run:
                action_name += "（Dry-run）"
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=False,
            )
            if not dry_run:
                return
        return

    if choice_id == "clean_legacy":
        while True:
            dry_run = app._prompt_execution_mode(
                title="清理旧版无标记副本",
                default_dry_run=True,
            )
            if dry_run is None:
                return

            cli_args = ["clean-clones"]
            action_name = "清理旧版无标记副本"
            if dry_run:
                cli_args.append("--dry-run")
                action_name += "（Dry-run）"
            else:
                if not app._confirm_dangerous_action(cli_args):
                    return
                action_name += "（删除）"
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda dry_run=dry_run: run_cleanup_mode(
                    target_provider=app.context.target_provider,
                    dry_run=dry_run,
                ),
                danger=True,
            )
            if not dry_run:
                return
        return

    if choice_id == "connect_github":
        selection = _collect_github_connect_selection(app)
        if selection is None:
            return
        while True:
            dry_run = app._prompt_execution_mode(
                title="连接独立 GitHub 仓库",
                default_dry_run=False,
            )
            if dry_run is None:
                return
            action_name, cli_args = _build_github_connect_request(selection, dry_run=dry_run)
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=False,
                use_progress=True,
            )
            if not dry_run:
                return
        return

    if choice_id == "pull_github":
        status = _github_connected_status_or_none(app, title="从 GitHub 拉取更新")
        if status is None:
            return
        while True:
            dry_run = _prompt_github_pull_dry_run(app, status)
            if dry_run is None:
                return
            action_name, cli_args = _build_github_pull_request(status, dry_run=dry_run)
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=False,
                use_progress=True,
            )
            if not dry_run:
                return
        return

    if choice_id == "sync_github":
        status = _github_connected_status_or_none(app, title="推送本机更新到 GitHub")
        if status is None:
            return
        while True:
            dry_run = _prompt_github_push_dry_run(app, status)
            if dry_run is None:
                return
            action_name, cli_args = _build_github_push_request(status, dry_run=dry_run)
            app._run_action(
                action_name,
                cli_args,
                dry_run=dry_run,
                runner=lambda args=cli_args: app._run_toolkit(args),
                danger=False,
                use_progress=True,
            )
            if not dry_run:
                return
        return

    if choice_id == "delete_skill":
        selected = app._open_local_skill_browser(mode="select")
        if not selected:
            return
        cli_args = ["delete-skill", selected.relative_dir, "--source-root", selected.source_root]
        if not app._confirm_dangerous_action(
            cli_args,
            warning=f"将删除本机 Skill：{selected.source_root}/{selected.relative_dir}。",
            impact=str(selected.skill_dir),
        ):
            return
        app._run_action(
            f"删除本机 Skill {selected.relative_dir}",
            cli_args,
            dry_run=False,
            runner=lambda args=cli_args: app._run_toolkit(args),
            danger=True,
        )
        return

    action_name, cli_args = app._resolve_menu_action_request(chosen_action)
    if cli_args is not None:
        dry_run = chosen_action.is_dry_run or "--dry-run" in cli_args
        app._run_action(
            action_name or chosen_action.label,
            cli_args,
            dry_run=dry_run,
            runner=lambda args=cli_args: app._run_toolkit(args),
            danger=chosen_action.is_dangerous,
            use_progress=chosen_action.section_id == "github",
        )


def run_action(
    app: "ToolkitTuiApp",
    action_name: str,
    cli_args: Sequence[str],
    *,
    dry_run: bool,
    runner: Callable[[], int],
    danger: bool,
    preview_cmd: Optional[str] = None,
    use_progress: bool = False,
) -> None:
    box_width = app._print_branded_header("执行中…")
    color = Ansi.RED if danger and not dry_run else Ansi.YELLOW if dry_run else Ansi.CYAN
    print(style_text(f"▶ {action_name}", Ansi.BOLD, color))
    print("")

    info_lines = [
        f"{style_text('执行方式', Ansi.DIM)}  : 直接在 TUI 中执行",
        f"{style_text('当前动作', Ansi.DIM)}  : {style_text(action_name, Ansi.BOLD, color)}",
        f"{style_text('目标 Provider', Ansi.DIM)} : {style_text(app.context.target_provider, Ansi.BOLD, Ansi.CYAN)}",
        f"{style_text('会话目录', Ansi.DIM)}      : {style_text(app.context.active_sessions_dir, Ansi.DIM)}",
    ]
    if preview_cmd:
        info_lines.append(f"{style_text('命令预览', Ansi.DIM)}  : {preview_cmd}")
    if danger and not dry_run:
        info_lines.append(style_text("【危险】", Ansi.BOLD, Ansi.RED) + "将删除文件，无法恢复。")
    elif dry_run:
        info_lines.append(style_text("【DRY-RUN】", Ansi.BOLD, Ansi.YELLOW) + "不写入/不删除。")
    display_lines = (
        _user_action_progress_lines(
            app,
            action_name=action_name,
            cli_args=cli_args,
            dry_run=dry_run,
            color=color,
        )
        if use_progress
        else info_lines
    )
    for line in render_box(display_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        print(line)
    print("")

    if use_progress:
        progress_result = run_cli_args_with_progress(
            app,
            title=action_name,
            detail_lines=display_lines,
            cli_args=list(cli_args),
        )
        box_width = app._print_branded_header("执行结果")
        print(style_text(f"▶ {action_name}", Ansi.BOLD, color))
        print("")
        for line in render_box(display_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
            print(line)
        print("")
        if progress_result.stdout.strip():
            print(progress_result.stdout.rstrip())
        if progress_result.stderr.strip():
            if progress_result.stdout.strip():
                print("")
            print(style_text("错误输出：", Ansi.BOLD, Ansi.YELLOW))
            print(progress_result.stderr.rstrip())
        result = progress_result.return_code
    else:
        result = runner()
    if result != 0:
        print(style_text(f"\n操作返回状态码：{result}", Ansi.BOLD, Ansi.YELLOW))
    if maybe_offer_github_sync_after_action(
        app,
        action_name=action_name,
        cli_args=cli_args,
        result_code=result,
        dry_run=dry_run,
    ):
        return
    next_step = "选择" if dry_run else "菜单"
    input(style_text(f"\n按 Enter 返回{next_step}...", Ansi.DIM))
