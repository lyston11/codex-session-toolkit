"""Action execution flows extracted from the TUI app shell."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from .terminal import Ansi, render_box, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp, TuiMenuAction


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

    if menu_action.action_id == "export_one":
        summary = app._open_session_browser(mode="select")
        if not summary:
            return None, None
        return f"导出会话 {summary.session_id} 为 Bundle", ["export", summary.session_id]

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
    from .app import run_cleanup_mode, run_clone_mode

    choice_id = chosen_action.action_id
    if choice_id == "provider_migration":
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
        return

    if choice_id == "desktop_repair":
        include_cli = app._prompt_desktop_repair_scope()
        if include_cli is None:
            return

        dry_run = app._prompt_execution_mode(
            title="修复会话在 Desktop 中显示",
            default_dry_run=True,
        )
        if dry_run is None:
            return

        cli_args = ["repair-desktop"]
        action_name = "修复会话在 Desktop 中显示"
        if include_cli:
            cli_args.append("--include-cli")
            action_name += "并纳入 CLI 会话"
        if dry_run:
            cli_args.append("--dry-run")
            action_name += "（Dry-run）"
        app._run_action(
            action_name,
            cli_args,
            dry_run=dry_run,
            runner=lambda args=cli_args: app._run_toolkit(args),
            danger=False,
        )
        return

    if choice_id == "clean_legacy":
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
        return

    action_name, cli_args = app._resolve_menu_action_request(chosen_action)
    if cli_args is not None:
        app._run_action(
            action_name or chosen_action.label,
            cli_args,
            dry_run=chosen_action.is_dry_run,
            runner=lambda args=cli_args: app._run_toolkit(args),
            danger=chosen_action.is_dangerous,
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
    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        print(line)
    print("")

    result = runner()
    if result != 0:
        print(style_text(f"\n操作返回状态码：{result}", Ansi.BOLD, Ansi.YELLOW))
    input(style_text("\n按 Enter 返回菜单...", Ansi.DIM))
