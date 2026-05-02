import argparse
import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit import APP_COMMAND, CodexPaths, ToolkitError, __version__, build_app_context, resolve_target_model_provider, run_cli  # noqa: E402
from codex_session_toolkit import core as core_api  # noqa: E402
from codex_session_toolkit.command_catalog import CLI_SUBCOMMANDS, COMMAND_CATALOG, command_domain, command_domains, commands_for_domain  # noqa: E402
from codex_session_toolkit.application.command_handlers import COMMAND_HANDLERS  # noqa: E402
from codex_session_toolkit.command_parser import create_parser as build_command_parser  # noqa: E402
from codex_session_toolkit.commands import create_parser  # noqa: E402
from codex_session_toolkit.models import GitHubSyncStatus  # noqa: E402
from codex_session_toolkit.stores import skills as skills_store  # noqa: E402
from codex_session_toolkit.stores import skills_manifest as skills_manifest_store  # noqa: E402
import codex_session_toolkit.terminal_ui as terminal_ui_compat  # noqa: E402
import codex_session_toolkit.tui_app as tui_app_compat  # noqa: E402
from codex_session_toolkit.cli import DEFAULT_MODEL_PROVIDER, create_arg_parser  # noqa: E402
from codex_session_toolkit.tui.maintenance_modes import run_cleanup_mode, run_clone_mode  # noqa: E402
from codex_session_toolkit.tui.action_flows import build_desktop_repair_cli_args, execute_menu_action, resolve_menu_action_request, run_action  # noqa: E402
from codex_session_toolkit.tui.browser_flows import open_project_session_browser  # noqa: E402
from codex_session_toolkit.tui.github_flows import show_github_sync_status  # noqa: E402
from codex_session_toolkit.tui.menu_catalog import TUI_ACTION_SECTION_OVERRIDES, build_tui_menu_actions, build_tui_menu_sections, tui_action_section  # noqa: E402
from codex_session_toolkit.tui.progress_flows import ProgressSubprocessResult  # noqa: E402
from codex_session_toolkit.tui.prompt_flows import prompt_value  # noqa: E402
from codex_session_toolkit.tui.sync_prompts import github_sync_hint_lines  # noqa: E402
from codex_session_toolkit.tui.terminal import LOGO_FONT_BANNER  # noqa: E402
from codex_session_toolkit.tui.terminal_io import read_key  # noqa: E402
from codex_session_toolkit.tui import view_models as tui_view_models  # noqa: E402
from codex_session_toolkit.tui.view_models import ToolkitAppContext  # noqa: E402


def _module_env() -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) if not existing else f"{SRC_DIR}{os.pathsep}{existing}"
    return env


class PackagingSmokeTests(unittest.TestCase):
    def test_package_root_exposes_stable_runtime_api(self) -> None:
        self.assertIs(CodexPaths, core_api.CodexPaths)
        self.assertIs(ToolkitError, core_api.ToolkitError)
        self.assertTrue(callable(build_app_context))
        self.assertTrue(callable(resolve_target_model_provider))
        self.assertTrue(callable(run_cli))

    def test_cli_parser_uses_packaged_command_name(self) -> None:
        parser = create_arg_parser()
        self.assertEqual(parser.prog, APP_COMMAND)

    def test_canonical_cli_commands_have_registered_handlers(self) -> None:
        parser = create_parser()
        command_names = set()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                command_names.update(action.choices)

        self.assertEqual(set(CLI_SUBCOMMANDS), command_names)
        self.assertEqual(set(COMMAND_HANDLERS), set(CLI_SUBCOMMANDS))
        self.assertTrue(all(callable(handler) for handler in COMMAND_HANDLERS.values()))

    def test_canonical_cli_command_catalog_is_grouped_by_domain(self) -> None:
        self.assertEqual(command_domains(), ("session", "bundle", "skills", "repair", "github"))
        self.assertEqual(len({spec.name for spec in COMMAND_CATALOG}), len(COMMAND_CATALOG))
        self.assertEqual({spec.domain for spec in COMMAND_CATALOG}, set(command_domains()))
        self.assertTrue(all(commands_for_domain(domain) for domain in command_domains()))

    def test_github_sync_cli_connects_before_syncing(self) -> None:
        parser = build_command_parser()
        connect_args = parser.parse_args(["connect-github", "git@github.com:example/codex-bundles.git", "--push-after-connect"])
        pull_args = parser.parse_args(["pull-github"])
        sync_args = parser.parse_args(["sync-github"])

        self.assertEqual(connect_args.remote_url, "git@github.com:example/codex-bundles.git")
        self.assertTrue(connect_args.push_after_connect)
        self.assertFalse(hasattr(pull_args, "remote_url"))
        self.assertFalse(hasattr(sync_args, "remote_url"))
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["pull-github", "git@github.com:example/codex-bundles.git"])
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            parser.parse_args(["sync-github", "git@github.com:example/codex-bundles.git"])

    def test_bundle_workspace_is_not_tracked_by_project_source_repo(self) -> None:
        gitignore_lines = (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("codex_bundles/", gitignore_lines)

    def test_tui_prompt_value_can_cancel_with_q(self) -> None:
        class PromptApp:
            def _print_branded_header(self, title: str) -> int:
                return 80

        with patch("builtins.input", return_value="q"), redirect_stdout(io.StringIO()):
            result = prompt_value(
                PromptApp(),
                title="连接独立 GitHub 仓库",
                prompt_label="独立 GitHub 仓库 URL",
                help_lines=[],
                default="git@github.com:example/codex-bundles.git",
                allow_empty=False,
            )
        self.assertIsNone(result)

    def test_tui_github_status_checks_remote_with_progress_when_connected(self) -> None:
        class StatusApp:
            def __init__(self) -> None:
                self.panel_title = ""
                self.panel_lines = []

            def _show_detail_panel(self, title: str, lines: list[str], *, border_codes=None) -> None:
                self.panel_title = title
                self.panel_lines = lines

        app = StatusApp()
        local_status = GitHubSyncStatus(
            bundle_root=Path("/tmp/codex_bundles"),
            remote_name="origin",
            remote_url="git@github.com:example/codex-bundles.git",
            branch="main",
            bundle_root_exists=True,
            is_git_repo=True,
            is_connected=True,
            local_updated_at="2026-05-02T10:00:00+08:00",
        )
        remote_status = GitHubSyncStatus(
            bundle_root=Path("/tmp/codex_bundles"),
            remote_name="origin",
            remote_url="git@github.com:example/codex-bundles.git",
            branch="main",
            bundle_root_exists=True,
            is_git_repo=True,
            is_connected=True,
            local_updated_at="2026-05-02T10:00:00+08:00",
            remote_checked=True,
            remote_branch_exists=True,
            remote_commit_hash="abc123",
            remote_updated_at="2026-05-02T10:05:00+08:00",
        )

        def run_progress(_app, *, title, detail_lines, task):
            self.assertEqual(title, "GitHub 同步状态")
            self.assertTrue(any("正在检查远端更新时间" in line for line in detail_lines))
            self.assertFalse(any("慢步骤" in line or "后台" in line for line in detail_lines))
            return task()

        with patch("codex_session_toolkit.tui.github_flows.github_sync_status", side_effect=[local_status, remote_status]) as status_mock:
            with patch("codex_session_toolkit.tui.github_flows.run_callable_with_progress", side_effect=run_progress) as progress_mock:
                show_github_sync_status(app)

        self.assertEqual(status_mock.call_count, 2)
        progress_mock.assert_called_once()
        self.assertEqual(app.panel_title, "GitHub 同步状态")
        self.assertTrue(any("远端检查" in line and "已检查" in line for line in app.panel_lines))
        self.assertTrue(any("abc123" in line for line in app.panel_lines))

    def test_tui_github_action_uses_progress_subprocess(self) -> None:
        class ActionApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
            )

            def _print_branded_header(self, title: str, subtitle: str = "") -> int:
                return 80

            def _cli_preview(self, args) -> str:
                return "codex-session-toolkit " + " ".join(args)

        runner_called = False

        def runner() -> int:
            nonlocal runner_called
            runner_called = True
            return 7

        with patch(
            "codex_session_toolkit.tui.action_flows.run_cli_args_with_progress",
            return_value=ProgressSubprocessResult(return_code=0, stdout="done\n", stderr=""),
        ) as progress_mock:
            with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
                run_action(
                    ActionApp(),
                    "推送本机更新到 GitHub",
                    ["sync-github", "--dry-run"],
                    dry_run=True,
                    runner=runner,
                    danger=False,
                    use_progress=True,
                )

        self.assertFalse(runner_called)
        progress_mock.assert_called_once()
        self.assertEqual(progress_mock.call_args.kwargs["cli_args"], ["sync-github", "--dry-run"])
        progress_lines = progress_mock.call_args.kwargs["detail_lines"]
        self.assertTrue(any("同步内容" in line and "Skills Bundle" in line for line in progress_lines))
        self.assertFalse(any("慢步骤" in line or "后台" in line or "命令输出" in line for line in progress_lines))
        self.assertFalse(any("目标 Provider" in line or "会话目录" in line for line in progress_lines))

    def test_tui_sync_hint_uses_cached_local_status_only(self) -> None:
        class HintApp:
            def __init__(self) -> None:
                self.check_remote_values = []

            def _github_sync_status(self, *, check_remote: bool = False):
                self.check_remote_values.append(check_remote)
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                    changed_files=["machine/sessions/demo/manifest.env", "machine/skills/demo/SKILL.md"],
                )

        app = HintApp()
        first_lines = github_sync_hint_lines(app)
        second_lines = github_sync_hint_lines(app)

        self.assertEqual(app.check_remote_values, [False])
        self.assertEqual(first_lines, second_lines)
        self.assertTrue(any("本机有 2 个待推送" in line for line in first_lines))

    def test_tui_export_completion_offers_sync_without_remote_check(self) -> None:
        class ExportApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
                bundle_root_label="./codex_bundles",
            )

            def __init__(self) -> None:
                self.check_remote_values = []
                self.prompt_kwargs = {}

            def _print_branded_header(self, title: str, subtitle: str = "") -> int:
                return 80

            def _github_sync_status(self, *, check_remote: bool = False):
                self.check_remote_values.append(check_remote)
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                    changed_files=["machine/sessions/demo/manifest.env"],
                    session_changed_files=["machine/sessions/demo/manifest.env"],
                )

            def _prompt_choice(self, **kwargs):
                self.prompt_kwargs = kwargs
                return "q"

            def _run_action(self, *args, **kwargs):
                raise AssertionError("choosing later should not run GitHub push")

            def _show_github_sync_status(self):
                raise AssertionError("choosing later should not open sync status")

        app = ExportApp()
        with redirect_stdout(io.StringIO()):
            run_action(
                app,
                "导出会话 demo 为 Bundle",
                ["export", "demo"],
                dry_run=False,
                runner=lambda: 0,
                danger=False,
            )

        self.assertEqual(app.check_remote_values, [False])
        self.assertEqual(app.prompt_kwargs["prompt_label"], "这个操作已完成，是否现在同步")
        self.assertEqual([key for key, _ in app.prompt_kwargs["choices"]], ["g", "5", "q"])

    def test_tui_import_completion_does_not_prompt_for_github_pull(self) -> None:
        class ImportApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
                bundle_root_label="./codex_bundles",
            )

            def _print_branded_header(self, title: str, subtitle: str = "") -> int:
                return 80

            def _github_sync_status(self, *, check_remote: bool = False):
                raise AssertionError("import should not query GitHub sync status or ask to pull")

            def _prompt_choice(self, **kwargs):
                raise AssertionError("import should not show a GitHub sync choice")

        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            run_action(
                ImportApp(),
                "导入 Bundle demo 为会话",
                ["import", "/tmp/copied-bundle"],
                dry_run=False,
                runner=lambda: 0,
                danger=False,
            )

    def test_tui_github_push_uses_single_choice_flow(self) -> None:
        test_case = self

        class PushApp:
            def __init__(self) -> None:
                self.prompt_calls = 0

            def _github_sync_status(self):
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                    changed_files=["machine/sessions/demo/manifest.env", "machine/skills/demo/SKILL.md"],
                    session_changed_files=["machine/sessions/demo/manifest.env"],
                    skill_changed_files=["machine/skills/demo/SKILL.md"],
                )

            def _github_sync_status_lines(self, status):
                return ["同步状态 : 已连接独立仓库"]

            def _prompt_choice(self, **kwargs):
                self.prompt_calls += 1
                test_case.assertEqual(kwargs["prompt_label"], "选择推送方式")
                test_case.assertEqual([key for key, _ in kwargs["choices"]], ["p", "d", "q"])
                return "p"

            def _prompt_value(self, **kwargs):
                raise AssertionError("TUI push should not ask for branch or commit message in the default flow")

            def _prompt_execution_mode(self, **kwargs):
                raise AssertionError("TUI push should select Dry-run from the single choice screen")

            def _confirm_toggle(self, **kwargs):
                raise AssertionError("TUI push should not ask whether to push after choosing push")

            def _show_detail_panel(self, *args, **kwargs):
                raise AssertionError("connected push should not show a missing-connection panel")

        app = PushApp()
        with patch("codex_session_toolkit.tui.action_flows.run_callable_with_progress", side_effect=lambda _app, task, **kwargs: task()):
            action_name, cli_args = resolve_menu_action_request(
                app,
                SimpleNamespace(action_id="sync_github", label="推送本机更新到 GitHub", cli_args=("sync-github",)),
            )

        self.assertEqual(action_name, "推送本机更新到 GitHub")
        self.assertEqual(cli_args, ["sync-github", "--branch", "main", "--message", "Sync Codex bundles"])
        self.assertEqual(app.prompt_calls, 1)

    def test_tui_github_pull_uses_single_choice_flow(self) -> None:
        test_case = self

        class PullApp:
            def __init__(self) -> None:
                self.prompt_calls = 0

            def _github_sync_status(self):
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                )

            def _github_sync_status_lines(self, status):
                return ["同步状态 : 已连接独立仓库"]

            def _prompt_choice(self, **kwargs):
                self.prompt_calls += 1
                test_case.assertEqual(kwargs["prompt_label"], "选择拉取方式")
                test_case.assertEqual([key for key, _ in kwargs["choices"]], ["p", "d", "q"])
                test_case.assertTrue(any("origin/main" in line for line in kwargs["help_lines"]))
                return "p"

            def _prompt_value(self, **kwargs):
                raise AssertionError("TUI pull should not ask for a branch in the default flow")

            def _prompt_execution_mode(self, **kwargs):
                raise AssertionError("TUI pull should select Dry-run from the single choice screen")

            def _show_detail_panel(self, *args, **kwargs):
                raise AssertionError("connected pull should not show a missing-connection panel")

        app = PullApp()
        with patch("codex_session_toolkit.tui.action_flows.run_callable_with_progress", side_effect=lambda _app, task, **kwargs: task()):
            action_name, cli_args = resolve_menu_action_request(
                app,
                SimpleNamespace(action_id="pull_github", label="从 GitHub 拉取更新", cli_args=("pull-github",)),
            )

        self.assertEqual(action_name, "从 GitHub 拉取更新")
        self.assertEqual(cli_args, ["pull-github", "--branch", "main"])
        self.assertEqual(app.prompt_calls, 1)

    def test_tui_github_pull_dry_run_returns_to_pull_choice(self) -> None:
        test_case = self

        class PullApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
                bundle_root_label="./codex_bundles",
            )

            def __init__(self) -> None:
                self.prompt_answers = ["d", "q"]
                self.prompt_calls = 0
                self.status_calls = 0
                self.run_calls = []

            def _github_sync_status(self):
                self.status_calls += 1
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                )

            def _github_sync_status_lines(self, status):
                return ["同步状态 : 已连接独立仓库"]

            def _prompt_choice(self, **kwargs):
                self.prompt_calls += 1
                test_case.assertEqual(kwargs["prompt_label"], "选择拉取方式")
                test_case.assertEqual([key for key, _ in kwargs["choices"]], ["p", "d", "q"])
                return self.prompt_answers.pop(0)

            def _prompt_value(self, **kwargs):
                raise AssertionError("TUI pull should not ask for a branch after Dry-run")

            def _prompt_execution_mode(self, **kwargs):
                raise AssertionError("TUI pull should return to the single choice screen after Dry-run")

            def _run_action(self, action_name, cli_args, **kwargs):
                self.run_calls.append((action_name, list(cli_args), kwargs))

            def _run_toolkit(self, args):
                raise AssertionError("progress GitHub actions should not call the fallback runner in this test")

            def _show_detail_panel(self, *args, **kwargs):
                raise AssertionError("connected pull should not show a missing-connection panel")

        app = PullApp()
        action = SimpleNamespace(
            action_id="pull_github",
            label="从 GitHub 拉取更新",
            cli_args=("pull-github",),
            section_id="github",
            is_dangerous=False,
            is_dry_run=False,
        )
        with patch("codex_session_toolkit.tui.action_flows.run_callable_with_progress", side_effect=lambda _app, task, **kwargs: task()):
            execute_menu_action(app, action)

        self.assertEqual(app.status_calls, 1)
        self.assertEqual(app.prompt_calls, 2)
        self.assertEqual(len(app.run_calls), 1)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "从 GitHub 拉取更新（Dry-run）")
        self.assertEqual(cli_args, ["pull-github", "--branch", "main", "--dry-run"])
        self.assertTrue(kwargs["dry_run"])
        self.assertTrue(kwargs["use_progress"])

    def test_tui_github_push_dry_run_returns_to_push_choice(self) -> None:
        test_case = self

        class PushApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
                bundle_root_label="./codex_bundles",
            )

            def __init__(self) -> None:
                self.prompt_answers = ["d", "q"]
                self.prompt_calls = 0
                self.status_calls = 0
                self.run_calls = []

            def _github_sync_status(self):
                self.status_calls += 1
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    remote_url="git@github.com:example/codex-bundles.git",
                    branch="main",
                    bundle_root_exists=True,
                    is_git_repo=True,
                    is_connected=True,
                    changed_files=["machine/sessions/demo/manifest.env", "machine/skills/demo/SKILL.md"],
                    session_changed_files=["machine/sessions/demo/manifest.env"],
                    skill_changed_files=["machine/skills/demo/SKILL.md"],
                )

            def _github_sync_status_lines(self, status):
                return ["同步状态 : 已连接独立仓库"]

            def _prompt_choice(self, **kwargs):
                self.prompt_calls += 1
                test_case.assertEqual(kwargs["prompt_label"], "选择推送方式")
                test_case.assertEqual([key for key, _ in kwargs["choices"]], ["p", "d", "q"])
                return self.prompt_answers.pop(0)

            def _run_action(self, action_name, cli_args, **kwargs):
                self.run_calls.append((action_name, list(cli_args), kwargs))

            def _run_toolkit(self, args):
                raise AssertionError("progress GitHub actions should not call the fallback runner in this test")

            def _show_detail_panel(self, *args, **kwargs):
                raise AssertionError("connected push should not show a missing-connection panel")

        app = PushApp()
        action = SimpleNamespace(
            action_id="sync_github",
            label="推送本机更新到 GitHub",
            cli_args=("sync-github",),
            section_id="github",
            is_dangerous=False,
            is_dry_run=False,
        )
        with patch("codex_session_toolkit.tui.action_flows.run_callable_with_progress", side_effect=lambda _app, task, **kwargs: task()):
            execute_menu_action(app, action)

        self.assertEqual(app.status_calls, 1)
        self.assertEqual(app.prompt_calls, 2)
        self.assertEqual(len(app.run_calls), 1)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "推送本机更新到 GitHub（Dry-run）")
        self.assertEqual(cli_args, ["sync-github", "--branch", "main", "--message", "Sync Codex bundles", "--dry-run"])
        self.assertTrue(kwargs["dry_run"])
        self.assertTrue(kwargs["use_progress"])

    def test_tui_connect_github_dry_run_returns_to_execution_mode(self) -> None:
        class ConnectApp:
            context = SimpleNamespace(
                target_provider="demo-provider",
                active_sessions_dir="/tmp/demo-sessions",
                bundle_root_label="./codex_bundles",
            )

            def __init__(self) -> None:
                self.prompt_values = ["git@github.com:example/codex-bundles.git", "main", "Initial sync"]
                self.mode_answers = [True, None]
                self.prompt_value_calls = 0
                self.mode_calls = 0
                self.run_calls = []

            def _github_sync_status(self):
                return GitHubSyncStatus(
                    bundle_root=Path("/tmp/codex_bundles"),
                    remote_name="origin",
                    branch="main",
                    bundle_root_exists=True,
                )

            def _github_sync_status_lines(self, status):
                return ["同步状态 : 未连接"]

            def _prompt_value(self, **kwargs):
                self.prompt_value_calls += 1
                return self.prompt_values.pop(0)

            def _confirm_toggle(self, **kwargs):
                return True

            def _prompt_execution_mode(self, **kwargs):
                self.mode_calls += 1
                return self.mode_answers.pop(0)

            def _run_action(self, action_name, cli_args, **kwargs):
                self.run_calls.append((action_name, list(cli_args), kwargs))

            def _run_toolkit(self, args):
                raise AssertionError("progress GitHub actions should not call the fallback runner in this test")

        app = ConnectApp()
        action = SimpleNamespace(action_id="connect_github", label="连接独立 GitHub 仓库", cli_args=("connect-github",))
        with patch("codex_session_toolkit.tui.action_flows.run_callable_with_progress", side_effect=lambda _app, task, **kwargs: task()):
            execute_menu_action(app, action)

        self.assertEqual(app.prompt_value_calls, 3)
        self.assertEqual(app.mode_calls, 2)
        self.assertEqual(len(app.run_calls), 1)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "连接独立 GitHub 仓库并首次推送（Dry-run）")
        self.assertEqual(
            cli_args,
            [
                "connect-github",
                "git@github.com:example/codex-bundles.git",
                "--branch",
                "main",
                "--push-after-connect",
                "--message",
                "Initial sync",
                "--dry-run",
            ],
        )
        self.assertTrue(kwargs["dry_run"])
        self.assertTrue(kwargs["use_progress"])

    def test_tui_desktop_repair_dry_run_returns_to_execution_mode(self) -> None:
        class RepairApp:
            context = SimpleNamespace(target_provider="demo-provider")

            def __init__(self) -> None:
                self.scope_calls = 0
                self.mode_answers = [True, None]
                self.mode_calls = 0
                self.run_calls = []

            def _prompt_desktop_repair_scope(self):
                self.scope_calls += 1
                return False

            def _prompt_execution_mode(self, **kwargs):
                self.mode_calls += 1
                return self.mode_answers.pop(0)

            def _run_action(self, action_name, cli_args, **kwargs):
                self.run_calls.append((action_name, list(cli_args), kwargs))

            def _run_toolkit(self, args):
                raise AssertionError("dry-run loop should not execute the fallback runner in this test")

        app = RepairApp()
        execute_menu_action(app, SimpleNamespace(action_id="desktop_repair", label="修复会话在 Desktop 中显示"))

        self.assertEqual(app.scope_calls, 1)
        self.assertEqual(app.mode_calls, 2)
        self.assertEqual(len(app.run_calls), 1)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "修复会话在 Desktop 中显示（Dry-run）")
        self.assertEqual(cli_args, ["repair-desktop", "demo-provider", "--dry-run"])
        self.assertTrue(kwargs["dry_run"])

    def test_project_export_dry_run_returns_to_execution_mode(self) -> None:
        class ProjectExportApp:
            context = SimpleNamespace(bundle_root_label="./codex_bundles")
            paths = CodexPaths(home=Path("/tmp"))

            def __init__(self) -> None:
                self.mode_answers = [True, None]
                self.mode_calls = 0
                self.run_calls = []

            def _prompt_project_path(self, *, default: str):
                return "/tmp/demo-project"

            def _screen_layout(self):
                return 80, False

            def _fit_lines_to_screen(self, lines):
                return lines

            def _prompt_execution_mode(self, **kwargs):
                self.mode_calls += 1
                return self.mode_answers.pop(0)

            def _run_action(self, action_name, cli_args, **kwargs):
                self.run_calls.append((action_name, list(cli_args), kwargs))

            def _run_toolkit(self, args):
                raise AssertionError("dry-run loop should not execute the fallback runner in this test")

            def _session_action_center(self, summary):
                raise AssertionError("x hotkey should export, not open session action center")

            def _show_detail_panel(self, *args, **kwargs):
                raise AssertionError("project export should not show a detail panel in this test")

        app = ProjectExportApp()
        summary = SimpleNamespace(
            session_id="demo-session",
            kind="desktop",
            scope="active",
            thread_name="Demo thread",
            preview="",
            path=Path("/tmp/demo-session.jsonl"),
            cwd="/tmp/demo-project",
            model_provider="demo-provider",
        )
        with patch("codex_session_toolkit.tui.browser_flows.get_project_session_summaries", return_value=[summary]):
            with patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]):
                with redirect_stdout(io.StringIO()):
                    open_project_session_browser(app)

        self.assertEqual(app.mode_calls, 2)
        self.assertEqual(len(app.run_calls), 1)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导出项目 demo-project 下的 1 个会话为 Bundle（Dry-run）")
        self.assertEqual(cli_args, ["export-project", "--dry-run", "/tmp/demo-project"])
        self.assertTrue(kwargs["dry_run"])

    def test_commands_module_reexports_parser_builder(self) -> None:
        self.assertIs(create_parser, build_command_parser)

    def test_cli_parser_module_stays_decoupled_from_service_layers(self) -> None:
        forbidden_prefixes = (".presenters", ".services", ".support")

        for path in (
            ROOT_DIR / "src" / "codex_session_toolkit" / "commands.py",
            ROOT_DIR / "src" / "codex_session_toolkit" / "command_parser.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module_name = "." * node.level + (node.module or "")
                    self.assertFalse(
                        module_name.startswith(forbidden_prefixes),
                        f"{path.relative_to(ROOT_DIR)} should route execution through command_handlers, not {module_name}",
                    )

    def test_skills_store_reexports_manifest_models_for_compatibility(self) -> None:
        self.assertIs(skills_store.SkillDescriptor, skills_manifest_store.SkillDescriptor)
        self.assertIs(skills_store.SkillsManifest, skills_manifest_store.SkillsManifest)
        self.assertIs(skills_store.read_skills_manifest, skills_manifest_store.read_skills_manifest)

    def test_tui_context_uses_packaged_command_name(self) -> None:
        context = ToolkitAppContext(
            target_provider="demo-provider",
            active_sessions_dir="/tmp/demo-sessions",
            config_path="/tmp/demo-config.toml",
        )
        self.assertEqual(context.entry_command, APP_COMMAND)

    def test_tui_compat_wrappers_expose_explicit_lazy_exports(self) -> None:
        self.assertIn("ToolkitAppContext", tui_app_compat.__all__)
        self.assertIn("run_tui", tui_app_compat.__all__)
        self.assertIs(ToolkitAppContext, tui_app_compat.ToolkitAppContext)
        self.assertIs(run_cleanup_mode, tui_app_compat.run_cleanup_mode)
        self.assertIs(run_clone_mode, tui_app_compat.run_clone_mode)
        self.assertIs(build_tui_menu_actions, tui_app_compat.build_tui_menu_actions)
        self.assertIs(build_tui_menu_actions, tui_view_models.build_tui_menu_actions)
        self.assertIn("render_box", terminal_ui_compat.__all__)
        self.assertIs(LOGO_FONT_BANNER, terminal_ui_compat.LOGO_FONT_BANNER)
        self.assertIs(read_key, terminal_ui_compat.read_key)

    def test_tui_main_sections_are_grouped_by_domain(self) -> None:
        section_ids = [section.section_id for section in build_tui_menu_sections()]
        self.assertEqual(section_ids, list(command_domains()))

        actions_by_section = {}
        labels_by_action = {}
        for action in build_tui_menu_actions():
            actions_by_section.setdefault(action.section_id, set()).add(action.action_id)
            labels_by_action[action.action_id] = action.label
            self.assertEqual(action.section_id, tui_action_section(action.action_id, action.cli_args))
            if action.cli_args:
                self.assertIn(action.cli_args[0], CLI_SUBCOMMANDS)
                if action.action_id not in TUI_ACTION_SECTION_OVERRIDES:
                    self.assertEqual(action.section_id, command_domain(action.cli_args[0]))

        self.assertEqual(actions_by_section["session"], {"list_sessions", "export_one", "project_sessions"})
        self.assertEqual(
            actions_by_section["bundle"],
            {
                "browse_bundles",
                "validate_bundles",
                "export_desktop_all",
                "export_desktop_active",
                "export_cli_all",
                "import_one",
                "import_desktop_all",
            },
        )
        self.assertEqual(
            actions_by_section["github"],
            {
                "github_status",
                "connect_github",
                "pull_github",
                "sync_github",
            },
        )
        self.assertEqual(
            [action.action_id for action in build_tui_menu_actions() if action.section_id == "github"],
            ["connect_github", "github_status", "pull_github", "sync_github"],
        )
        self.assertEqual(
            actions_by_section["repair"],
            {
                "provider_migration",
                "desktop_repair",
                "browse_backups",
                "clean_legacy",
            },
        )
        self.assertEqual(
            actions_by_section["skills"],
            {
                "list_skills",
                "export_skill_one",
                "export_skills_all",
                "browse_skill_bundles",
                "import_skill_bundle",
                "import_skill_bundles",
                "delete_skill",
            },
        )
        self.assertEqual(labels_by_action["provider_migration"], "迁移到当前 Provider")
        self.assertEqual(labels_by_action["desktop_repair"], "修复会话在 Desktop 中显示")
        self.assertEqual(labels_by_action["browse_backups"], "管理会话备份")
        self.assertEqual(labels_by_action["clean_legacy"], "清理旧版无标记副本")
        self.assertEqual(labels_by_action["project_sessions"], "按项目路径查看并导出会话")
        self.assertEqual(labels_by_action["list_skills"], "浏览本机 Skills")
        self.assertEqual(labels_by_action["export_skill_one"], "导出单个 Skill")
        self.assertEqual(labels_by_action["delete_skill"], "删除本机 Skill")
        self.assertEqual(labels_by_action["github_status"], "查看 GitHub 同步状态")
        self.assertEqual(labels_by_action["connect_github"], "连接独立 GitHub 仓库")
        self.assertEqual(labels_by_action["pull_github"], "从 GitHub 拉取更新")
        self.assertEqual(labels_by_action["sync_github"], "推送本机更新到 GitHub")

    def test_tui_desktop_repair_passes_target_provider_explicitly(self) -> None:
        self.assertEqual(
            build_desktop_repair_cli_args("account-provider", include_cli=True, dry_run=True),
            ["repair-desktop", "account-provider", "--include-cli", "--dry-run"],
        )

    def test_logo_font_covers_toolkit_wordmark(self) -> None:
        missing = {ch for ch in "CODEX SESSION TOOLKIT" if ch != " " and ch not in LOGO_FONT_BANNER}
        self.assertEqual(missing, set())

    def test_build_app_context_reads_provider_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            code_dir = home / ".codex"
            code_dir.mkdir(parents=True, exist_ok=True)
            (code_dir / "config.toml").write_text('model_provider = "runtime-provider"\n', encoding="utf-8")

            context = build_app_context(CodexPaths(home=home))

        self.assertEqual(context.target_provider, "runtime-provider")
        self.assertEqual(context.active_sessions_dir, str(home / ".codex" / "sessions"))
        self.assertEqual(context.config_path, str(home / ".codex" / "config.toml"))

    def test_build_app_context_falls_back_when_config_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context = build_app_context(CodexPaths(home=Path(tmpdir)))

        self.assertEqual(context.target_provider, DEFAULT_MODEL_PROVIDER)

    def test_core_exports_smaller_stable_api(self) -> None:
        self.assertIn("clone_to_provider", core_api.__all__)
        self.assertIn("repair_desktop", core_api.__all__)
        self.assertNotIn("parse_jsonl_records", core_api.__all__)
        self.assertNotIn("validate_jsonl_file", core_api.__all__)

    def test_core_keeps_lazy_legacy_compatibility(self) -> None:
        self.assertTrue(callable(core_api.parse_jsonl_records))
        self.assertTrue(callable(core_api.validate_jsonl_file))

    def test_package_source_avoids_internal_compatibility_imports(self) -> None:
        package_root = ROOT_DIR / "src" / "codex_session_toolkit"
        compat_paths = {
            package_root / "core.py",
            package_root / "tui_app.py",
            package_root / "terminal_ui.py",
            package_root / "stores" / "bundles.py",
        }
        blocked_imports = {
            "codex_session_toolkit.core",
            "codex_session_toolkit.tui_app",
            "codex_session_toolkit.terminal_ui",
            "codex_session_toolkit.stores.bundles",
            ".core",
            ".tui_app",
            ".terminal_ui",
            ".stores.bundles",
        }

        for path in package_root.rglob("*.py"):
            if path in compat_paths:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    module_name = "." * node.level + (node.module or "")
                    imported = {module_name}
                else:
                    continue
                self.assertTrue(
                    imported.isdisjoint(blocked_imports),
                    f"{path.relative_to(ROOT_DIR)} should import canonical modules, not compatibility facades: {sorted(imported & blocked_imports)}",
                )

    def test_module_help_mentions_packaged_command(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("clone-provider", result.stdout)

    def test_module_version_matches_package_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "codex_session_toolkit", "--version"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), f"{APP_COMMAND} {__version__}")

    def test_repo_local_launcher_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn(f"usage: {APP_COMMAND}", result.stdout)
        self.assertIn("--version", result.stdout)

    def test_repo_local_launcher_prefers_source_mode_in_git_worktree(self) -> None:
        result = subprocess.run(
            ["sh", "./codex-session-toolkit", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Launcher (Source Mode)", result.stdout)

    def test_unix_install_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./install.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./install.sh", result.stdout)
        self.assertIn("--editable", result.stdout)
        self.assertIn("isolated local virtual environment", result.stdout)

    def test_installers_are_configured_for_isolated_local_venv(self) -> None:
        unix_installer = (ROOT_DIR / "scripts" / "install" / "install.unix.sh").read_text(encoding="utf-8")
        windows_installer = (ROOT_DIR / "scripts" / "install" / "install.windows.ps1").read_text(encoding="utf-8")
        unix_launcher = (ROOT_DIR / "codex-session-toolkit").read_text(encoding="utf-8")
        windows_launcher = (ROOT_DIR / "codex-session-toolkit.ps1").read_text(encoding="utf-8")
        makefile = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

        self.assertNotIn("--system-site-packages", unix_installer)
        self.assertNotIn("--system-site-packages", windows_installer)
        self.assertIn("isolated", unix_installer.lower())
        self.assertIn("isolated", windows_installer.lower())
        self.assertIn('VENV_PYTHON="$VENV_DIR/bin/python"', unix_launcher)
        self.assertIn('Join-Path $venvScriptsDir "python.exe"', windows_launcher)
        self.assertIn("install: bootstrap-editable", makefile)
        self.assertIn("DEV_PIP_PACKAGES := 'ruff>=0.6,<1.0'", makefile)
        self.assertIn("$(VENV_PYTHON) -m pip install $(DEV_PIP_PACKAGES)", makefile)

    def test_release_script_help_runs(self) -> None:
        result = subprocess.run(
            ["sh", "./release.sh", "--help"],
            cwd=ROOT_DIR,
            env=_module_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
        self.assertIn("Usage: ./release.sh", result.stdout)
        self.assertIn("--output-dir", result.stdout)

    def test_release_folder_install_and_launcher_work_offline_for_end_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "releases"
            subprocess.run(
                ["sh", "./release.sh", "--output-dir", str(output_dir)],
                cwd=ROOT_DIR,
                env=_module_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            release_dir = output_dir / f"{APP_COMMAND}-{__version__}"
            self.assertTrue((release_dir / "install.sh").exists())
            self.assertTrue((release_dir / "codex-session-toolkit").exists())

            install_result = subprocess.run(
                ["sh", "./install.sh", "--force"],
                cwd=release_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            self.assertIn("Isolation: enabled", install_result.stdout)
            self.assertIn("Install complete.", install_result.stdout)

            version_result = subprocess.run(
                ["sh", "./codex-session-toolkit", "--version"],
                cwd=release_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
            self.assertIn("Launcher (Local Venv)", version_result.stdout)
            self.assertIn(f"{APP_COMMAND} {__version__}", version_result.stdout)


if __name__ == "__main__":
    unittest.main()
