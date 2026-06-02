import io
import os
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.models import BundleSummary, LocalSkillSummary, MigratedOriginalSessionSummary, SessionSummary, SkillBundleSummary  # noqa: E402
from codex_session_toolkit.tui.browser_flows import open_archived_session_browser, open_bundle_browser, open_local_skill_browser, open_migrated_original_session_browser, open_project_session_browser, open_session_browser, open_skill_bundle_browser, render_browser_frame  # noqa: E402
from codex_session_toolkit.tui.bundle_flows import bundle_detail_lines  # noqa: E402
from codex_session_toolkit.tui.progress_flows import _render_progress  # noqa: E402
from codex_session_toolkit.tui.prompt_flows import prompt_choice, render_prompt_choice  # noqa: E402
from codex_session_toolkit.tui.terminal import Ansi, strip_ansi  # noqa: E402


class FakeBrowserApp:
    def _fit_lines_to_screen(self, lines):
        return lines


class FakeArchivedBrowserApp:
    paths = SimpleNamespace(archived_sessions_dir=Path("/tmp/home/.codex/archived_sessions"))

    def __init__(self) -> None:
        self.run_calls = []
        self.confirm_calls = []
        self.detail_calls = []

    def _screen_layout(self):
        return 80, True

    def _fit_lines_to_screen(self, lines):
        return lines

    def _show_detail_panel(self, title, lines, **kwargs):
        self.detail_calls.append((title, list(lines), kwargs))

    def _prompt_value(self, **kwargs):
        raise AssertionError("this test should not prompt for search")

    def _confirm_dangerous_action(self, cli_args, **kwargs):
        self.confirm_calls.append((list(cli_args), kwargs))
        return True

    def _run_action(self, action_name, cli_args, **kwargs):
        self.run_calls.append((action_name, list(cli_args), kwargs))

    def _run_toolkit(self, args):
        raise AssertionError("browser tests record run_action without invoking toolkit")

    def _session_detail_lines(self, summary):
        return [summary.session_id]


class FakeMigratedOriginalBrowserApp(FakeArchivedBrowserApp):
    paths = SimpleNamespace()
    context = SimpleNamespace(target_provider="target-provider")


class FakeSkillDeleteBrowserApp:
    paths = SimpleNamespace()

    def __init__(self) -> None:
        self.run_calls = []
        self.confirm_calls = []
        self.detail_calls = []

    def _screen_layout(self):
        return 80, True

    def _fit_lines_to_screen(self, lines):
        return lines

    def _show_detail_panel(self, title, lines, **kwargs):
        self.detail_calls.append((title, list(lines), kwargs))

    def _prompt_value(self, **kwargs):
        raise AssertionError("this test should not prompt for search")

    def _confirm_dangerous_action(self, cli_args, **kwargs):
        self.confirm_calls.append((list(cli_args), kwargs))
        return True

    def _run_action(self, action_name, cli_args, **kwargs):
        self.run_calls.append((action_name, list(cli_args), kwargs))

    def _run_toolkit(self, args):
        raise AssertionError("browser tests record run_action without invoking toolkit")

    def _local_skill_detail_lines(self, summary):
        return [summary.relative_dir]

    def _github_sync_hint_lines(self):
        return []


class FakeSkillBrowserApp(FakeSkillDeleteBrowserApp):
    def _confirm_dangerous_action(self, cli_args, **kwargs):
        raise AssertionError("Skill export browser should not ask dangerous confirmation")


class FakeSkillBundleBrowserApp(FakeSkillDeleteBrowserApp):
    def _skill_bundle_detail_lines(self, summary):
        return [str(summary.bundle_dir)]


class FakeSessionBrowserApp:
    paths = SimpleNamespace()

    def __init__(self) -> None:
        self.run_calls = []
        self.detail_calls = []

    def _screen_layout(self):
        return 80, True

    def _fit_lines_to_screen(self, lines):
        return lines

    def _prompt_value(self, **kwargs):
        raise AssertionError("this test should not prompt for search")

    def _show_detail_panel(self, title, lines, **kwargs):
        self.detail_calls.append((title, list(lines), kwargs))

    def _session_detail_lines(self, summary):
        return [summary.session_id]

    def _run_action(self, action_name, cli_args, **kwargs):
        self.run_calls.append((action_name, list(cli_args), kwargs))

    def _run_toolkit(self, args):
        raise AssertionError("browser tests record run_action without invoking toolkit")


class FakeProjectSessionBrowserApp(FakeSessionBrowserApp):
    context = SimpleNamespace(bundle_root_label="./codex_bundles")
    paths = SimpleNamespace()

    def __init__(self) -> None:
        super().__init__()
        self.project_path = "/tmp/project"
        self.mode_answers = []
        self.mode_calls = 0

    def _prompt_project_path(self, *, default: str):
        return self.project_path

    def _prompt_execution_mode(self, **kwargs):
        self.mode_calls += 1
        return self.mode_answers.pop(0)


class FakeBundleBrowserApp:
    paths = SimpleNamespace(
        local_bundle_workspace=Path("/tmp/codex_bundles"),
        legacy_session_bundle_workspace=Path("/tmp/codex_sessions"),
        legacy_bundle_root=Path("/tmp/codex_sessions/bundles"),
        legacy_desktop_bundle_root=Path("/tmp/codex_sessions/desktop_bundles"),
    )

    def __init__(self) -> None:
        self.run_calls = []
        self.detail_calls = []
        self.confirm_calls = []
        self.confirm_answers = [False]
        self.prompt_answers = []
        self.snapshot_calls = []

    def _screen_layout(self):
        return 80, True

    def _fit_lines_to_screen(self, lines):
        return lines

    def _prompt_value(self, **kwargs):
        if not self.prompt_answers:
            raise AssertionError("this test should not prompt for search")
        return self.prompt_answers.pop(0)

    def _confirm_toggle(self, **kwargs):
        return self.confirm_answers.pop(0)

    def _confirm_dangerous_action(self, cli_args, **kwargs):
        self.confirm_calls.append((list(cli_args), kwargs))
        return True

    def _show_detail_panel(self, title, lines, **kwargs):
        self.detail_calls.append((title, list(lines), kwargs))

    def _bundle_detail_lines(self, bundle):
        return [bundle.session_id]

    def _github_sync_hint_lines(self):
        return []

    def _run_action(self, action_name, cli_args, **kwargs):
        self.run_calls.append((action_name, list(cli_args), kwargs))

    def _run_toolkit(self, args):
        raise AssertionError("browser tests record run_action without invoking toolkit")

    def _bundle_browser_snapshot(self, **kwargs):
        self.snapshot_calls.append(kwargs)
        return self.snapshot, kwargs["machine_filter"], kwargs["export_group_filter"]


class FakeProgressApp:
    def _screen_layout(self):
        return 80, True

    def _fit_lines_to_screen(self, lines):
        return lines

    def _print_branded_header(self, title):
        raise AssertionError("progress repaint must not clear and redraw the full screen")


class FakePromptChoiceApp:
    def _screen_layout(self):
        return 80, True

    def _screen_height(self):
        return 22

    def _fit_lines_to_screen(self, lines):
        raise AssertionError("prompt choices must reserve choice rows before final repaint")


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


def archived_summary(session_id: str) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        scope="archived",
        path=Path(f"/tmp/home/.codex/archived_sessions/rollout-2026-04-10T10-00-00-{session_id}.jsonl"),
        preview=f"Preview {session_id}",
        kind="desktop",
        cwd="/tmp/project",
        model_provider="provider",
        thread_name=f"Thread {session_id}",
    )


def active_summary(session_id: str) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        scope="active",
        path=Path(f"/tmp/home/.codex/sessions/rollout-2026-04-10T10-00-00-{session_id}.jsonl"),
        preview=f"Preview {session_id}",
        kind="desktop",
        cwd="/tmp/project",
        model_provider="provider",
        thread_name=f"Thread {session_id}",
    )


def migrated_original_summary(session_id: str, *, cloned_id: str = "") -> MigratedOriginalSessionSummary:
    cloned_id = cloned_id or session_id.replace("1111", "2222", 1)
    return MigratedOriginalSessionSummary(
        session_id=session_id,
        path=Path(f"/tmp/home/.codex/sessions/rollout-2026-04-10T10-00-00-{session_id}.jsonl"),
        model_provider="old-provider",
        cloned_session_id=cloned_id,
        cloned_path=Path(f"/tmp/home/.codex/sessions/rollout-2026-04-10T10-00-00-{cloned_id}.jsonl"),
        cloned_provider="target-provider",
        kind="desktop",
        cwd="/tmp/project",
        preview=f"Preview {session_id}",
    )


def skill_summary(name: str, *, source_root: str = "agents", location_kind: str = "custom") -> LocalSkillSummary:
    return LocalSkillSummary(
        name=name,
        source_root=source_root,
        relative_dir=name,
        skill_dir=Path(f"/tmp/home/.{source_root}/skills/{name}"),
        location_kind=location_kind,
        content_hash=f"hash-{name}",
    )


def skill_bundle_summary(name: str, *, source_machine: str = "Studio Mac") -> SkillBundleSummary:
    return SkillBundleSummary(
        bundle_dir=Path(f"/tmp/codex_bundles/studio-mac/skills/all/20260411/{name}"),
        exported_at="2026-04-11T10:00:00Z",
        source_machine=source_machine,
        source_machine_key="studio-mac",
        export_group="all",
        skill_count=2,
        bundled_skill_count=2,
        skills=(name,),
    )


def bundle_summary(session_id: str, *, export_group: str = "single") -> BundleSummary:
    return BundleSummary(
        source_group="bundle",
        session_id=session_id,
        bundle_dir=Path(f"/tmp/codex_bundles/Studio-Mac/sessions/{export_group}/20260411/{session_id}"),
        relative_path=f"sessions/2026/04/10/rollout-2026-04-10T10-00-00-{session_id}.jsonl",
        updated_at="2026-04-11T10:00:00Z",
        exported_at="2026-04-11T10:00:00Z",
        thread_name=f"Thread {session_id}",
        session_cwd="/tmp/project",
        session_kind="desktop",
        source_machine="Studio Mac",
        source_machine_key="studio-mac",
        export_group=export_group,
        export_group_label=export_group,
    )


class TuiBrowserRenderingTests(unittest.TestCase):
    def test_browser_frame_repaints_without_full_screen_clear(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            render_browser_frame(
                FakeBrowserApp(),
                title="浏览并导出会话",
                subtitle="↑/↓ 选择",
                info_lines=["搜索词 : （无）"],
                list_lines=["> session-a | desktop/active | preview"],
                list_border_codes=(Ansi.DIM, Ansi.MAGENTA),
                box_width=80,
                center=True,
            )

        rendered = output.getvalue()
        self.assertIn("\033[H", rendered)
        self.assertIn("\033[J", rendered)
        self.assertNotIn("\033[2J", rendered)
        self.assertEqual(rendered.count("搜索词"), 1)

    def test_progress_frame_repaints_without_full_screen_clear(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            _render_progress(
                FakeProgressApp(),
                title="GitHub 同步状态",
                detail_lines=["当前状态 : 正在检查远端更新时间"],
                started_at=0.0,
                tick=1,
            )

        rendered = output.getvalue()
        self.assertIn("\033[H", rendered)
        self.assertIn("\033[J", rendered)
        self.assertNotIn("\033[2J", rendered)
        self.assertIn("GitHub 同步状态", rendered)
        self.assertNotIn("后台执行", rendered)

    def test_prompt_choice_keeps_options_visible_when_help_is_tall(self) -> None:
        output = io.StringIO()
        help_lines = [f"状态行 {idx}" for idx in range(24)]

        with redirect_stdout(output):
            render_prompt_choice(
                FakePromptChoiceApp(),
                title="推送本机更新到 GitHub",
                prompt_label="确认推送目标",
                help_lines=help_lines,
                choices=[("p", "推送到 origin"), ("q", "返回")],
                selected_index=0,
            )

        rendered = output.getvalue()
        self.assertIn("推送到 origin", rendered)
        self.assertIn("q/←/Esc 返回", rendered)
        self.assertNotIn("窗口高度不足", rendered)
        self.assertNotIn("选项保留在下方", rendered)
        self.assertNotIn("\033[2J", rendered)

    def test_prompt_choice_does_not_repaint_while_idle(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.prompt_flows.sys.stdin.isatty", return_value=True))
            stack.enter_context(patch("codex_session_toolkit.tui.prompt_flows.term_width", return_value=80))
            stack.enter_context(patch("codex_session_toolkit.tui.prompt_flows.term_height", return_value=24))
            stack.enter_context(patch("codex_session_toolkit.tui.prompt_flows.read_key", side_effect=[None, None, "ENTER"]))
            render_mock = stack.enter_context(patch("codex_session_toolkit.tui.prompt_flows.render_prompt_choice"))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            result = prompt_choice(
                FakePromptChoiceApp(),
                title="推送本机更新到 GitHub",
                prompt_label="确认推送目标",
                help_lines=["状态行"],
                choices=[("p", "推送到 origin"), ("q", "返回")],
                default="p",
            )

        self.assertEqual(result, "p")
        render_mock.assert_called_once()

    def test_archived_browser_can_delete_selected_session(self) -> None:
        session_id = "11111111-2222-4333-8444-555555555555"
        app = FakeArchivedBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.get_session_summaries", return_value=[archived_summary(session_id)]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_archived_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-archived-sessions", session_id])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, f"删除归档会话 {session_id}")
        self.assertEqual(cli_args, ["delete-archived-sessions", session_id])
        self.assertTrue(kwargs["danger"])

    def test_archived_browser_enter_opens_session_preview(self) -> None:
        session_id = "11111111-2222-4333-8444-555555555555"
        app = FakeArchivedBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["ENTER", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.get_session_summaries", return_value=[archived_summary(session_id)]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_archived_session_browser(app)

        title, lines, _ = app.detail_calls[0]
        self.assertEqual(title, "归档会话预览")
        self.assertTrue(any("会话预览" in line and f"Preview {session_id}" in line for line in lines))

    def test_archived_browser_can_delete_checked_sessions(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeArchivedBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_session_summaries",
                    return_value=[archived_summary(first_id), archived_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_archived_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-archived-sessions", first_id, second_id])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除 2 个归档会话")
        self.assertEqual(cli_args, ["delete-archived-sessions", first_id, second_id])
        self.assertTrue(kwargs["danger"])

    def test_archived_browser_can_select_all_matching_sessions_then_delete(self) -> None:
        app = FakeArchivedBrowserApp()
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_session_summaries",
                    return_value=[archived_summary(first_id), archived_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_archived_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-archived-sessions", first_id, second_id])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除 2 个归档会话")
        self.assertEqual(cli_args, ["delete-archived-sessions", first_id, second_id])
        self.assertTrue(kwargs["danger"])

    def test_migrated_original_browser_can_delete_selected_session(self) -> None:
        session_id = "11111111-2222-4333-8444-555555555555"
        app = FakeMigratedOriginalBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.list_migrated_original_sessions",
                    return_value=[migrated_original_summary(session_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_migrated_original_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-migrated-originals", session_id])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, f"删除旧 Provider 会话 {session_id}")
        self.assertEqual(cli_args, ["delete-migrated-originals", session_id])
        self.assertTrue(kwargs["danger"])

    def test_migrated_original_browser_can_select_all_matching_sessions_then_delete(self) -> None:
        app = FakeMigratedOriginalBrowserApp()
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.list_migrated_original_sessions",
                    return_value=[migrated_original_summary(first_id), migrated_original_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_migrated_original_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-migrated-originals", first_id, second_id])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除 2 个旧 Provider 会话")
        self.assertEqual(cli_args, ["delete-migrated-originals", first_id, second_id])
        self.assertTrue(kwargs["danger"])

    def test_session_browser_can_export_checked_sessions(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "e", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_session_summaries",
                    return_value=[active_summary(first_id), active_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_session_browser(app, mode="view")

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导出 2 个会话为 Bundle")
        self.assertEqual(cli_args, ["export", first_id, second_id])
        self.assertFalse(kwargs["danger"])

    def test_session_browser_can_export_current_and_select_all_matching_sessions(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["e", "a", "e", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_session_summaries",
                    return_value=[active_summary(first_id), active_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_session_browser(app, mode="view")

        self.assertEqual(app.run_calls[0][0], f"导出会话 {first_id} 为 Bundle")
        self.assertEqual(app.run_calls[0][1], ["export", first_id])
        self.assertEqual(app.run_calls[1][0], "导出 2 个会话为 Bundle")
        self.assertEqual(app.run_calls[1][1], ["export", first_id, second_id])

    def test_session_browser_does_not_export_on_delete_key(self) -> None:
        session_id = "11111111-2222-4333-8444-555555555555"
        app = FakeSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_session_summaries",
                    return_value=[active_summary(session_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_session_browser(app, mode="view")

        self.assertFalse(app.run_calls)

    def test_project_session_browser_can_export_current_session(self) -> None:
        session_id = "11111111-2222-4333-8444-555555555555"
        app = FakeProjectSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["e", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_project_session_summaries",
                    return_value=[active_summary(session_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_project_session_browser(app)

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, f"导出会话 {session_id} 为 Bundle")
        self.assertEqual(cli_args, ["export", session_id])
        self.assertFalse(kwargs["danger"])

    def test_project_session_browser_can_export_checked_sessions(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeProjectSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "e", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_project_session_summaries",
                    return_value=[active_summary(first_id), active_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_project_session_browser(app)

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导出 2 个会话为 Bundle")
        self.assertEqual(cli_args, ["export", first_id, second_id])
        self.assertFalse(kwargs["danger"])

    def test_project_session_browser_can_select_all_matching_sessions_then_export(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeProjectSessionBrowserApp()

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "e", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.get_project_session_summaries",
                    return_value=[active_summary(first_id), active_summary(second_id)],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_project_session_browser(app)

        self.assertEqual(app.mode_calls, 0)
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导出 2 个会话为 Bundle")
        self.assertEqual(cli_args, ["export", first_id, second_id])
        self.assertFalse(kwargs["dry_run"])

    def test_bundle_import_browser_can_import_checked_bundles(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id), bundle_summary(second_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "i", "q"]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="import")

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导入 2 个 Bundle 为会话（显示到 Desktop）")
        self.assertEqual(
            cli_args,
            [
                "import",
                "--desktop-visible",
                "--no-create-workspace",
                "--machine",
                "studio-mac",
                "--export-group",
                "single",
                str(app.snapshot.entries[0].bundle_dir),
                str(app.snapshot.entries[1].bundle_dir),
            ],
        )
        self.assertFalse(kwargs["danger"])

    def test_bundle_import_browser_can_import_current_and_select_all_matching_bundles(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeBundleBrowserApp()
        app.confirm_answers = [False, False]
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id), bundle_summary(second_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["i", "a", "i", "q"]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="import")

        self.assertEqual(app.run_calls[0][0], f"导入 Bundle {first_id} 为会话（显示到 Desktop）")
        self.assertEqual(app.run_calls[0][1][-1], str(app.snapshot.entries[0].bundle_dir))
        self.assertEqual(app.run_calls[1][0], "导入 2 个 Bundle 为会话（显示到 Desktop）")
        self.assertEqual(app.run_calls[1][1][-2:], [str(entry.bundle_dir) for entry in app.snapshot.entries])
        self.assertTrue(any(call.get("limit") is None for call in app.snapshot_calls))

    def test_bundle_browse_browser_can_select_all_filtered_entries_and_delete_selected(self) -> None:
        session_ids = [
            "11111111-2222-4333-8444-555555555555",
            "22222222-3333-4444-8555-666666666666",
            "33333333-4444-4555-8666-777777777777",
            "44444444-5555-4666-8777-888888888888",
            "55555555-6666-4777-8888-999999999999",
            "66666666-7777-4888-8999-aaaaaaaaaaaa",
            "77777777-8888-4999-8aaa-bbbbbbbbbbbb",
            "88888888-9999-4aaa-8bbb-cccccccccccc",
            "99999999-aaaa-4bbb-8ccc-dddddddddddd",
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
        ]
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(session_id) for session_id in session_ids],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别")],
            machine_options=[("", "全部机器")],
        )
        delete_results = [
            SimpleNamespace(bundle_dir=entry.bundle_dir, session_id=entry.session_id, deleted=True, error="")
            for entry in app.snapshot.entries
        ]

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "x", "q"]))
            delete_mock = stack.enter_context(
                patch("codex_session_toolkit.tui.browser_flows.delete_bundle_summaries", return_value=delete_results)
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="browse")

        self.assertEqual(len(app.confirm_calls), 1)
        self.assertEqual(app.confirm_calls[0][0], ["delete-bundles", *[str(entry.bundle_dir) for entry in app.snapshot.entries]])
        delete_mock.assert_called_once()
        self.assertEqual([entry.session_id for entry in delete_mock.call_args.args[1]], session_ids)
        self.assertFalse(app.run_calls)
        self.assertTrue(any(title == "删除 Bundle 完成" for title, _, _ in app.detail_calls))

    def test_bundle_browse_browser_does_not_import_on_i(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["i", "q"]))
            delete_mock = stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.delete_bundle_summaries"))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="browse")

        self.assertFalse(app.run_calls)
        self.assertFalse(app.confirm_calls)
        delete_mock.assert_not_called()

    def test_bundle_browse_browser_deletes_on_x(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]))
            stack.enter_context(
                patch(
                    "codex_session_toolkit.tui.browser_flows.delete_bundle_summaries",
                    return_value=[
                        SimpleNamespace(
                            bundle_dir=app.snapshot.entries[0].bundle_dir,
                            session_id=first_id,
                            deleted=True,
                            error="",
                        )
                    ],
                )
            )
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="browse")

        self.assertFalse(app.run_calls)
        self.assertEqual(app.confirm_calls[0][0], ["delete-bundles", str(app.snapshot.entries[0].bundle_dir)])

    def test_bundle_browser_navigation_reuses_cached_snapshot(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        second_id = "22222222-3333-4444-8555-666666666666"
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id), bundle_summary(second_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别"), ("single", "single")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["DOWN", "UP", "d", "q"]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="browse")

        self.assertEqual(len(app.snapshot_calls), 1)
        self.assertEqual(len(app.detail_calls), 1)

    def test_bundle_browser_filter_keys_reload_snapshot(self) -> None:
        first_id = "11111111-2222-4333-8444-555555555555"
        app = FakeBundleBrowserApp()
        app.snapshot = SimpleNamespace(
            entries=[bundle_summary(first_id)],
            current_export_group_label="全部类别",
            current_machine_label="全部机器",
            export_group_options=[("", "全部类别"), ("single", "single")],
            machine_options=[("", "全部机器")],
        )

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["s", "q"]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_bundle_browser(app, mode="browse")

        self.assertEqual(len(app.snapshot_calls), 2)
        self.assertEqual(app.snapshot_calls[-1]["export_group_filter"], "single")

    def test_bundle_detail_uses_import_source_labels(self) -> None:
        app = FakeBundleBrowserApp()
        summary = bundle_summary("11111111-2222-4333-8444-555555555555")

        rendered = "\n".join(strip_ansi(line) for line in bundle_detail_lines(app, summary))

        self.assertIn("来源位置", rendered)
        self.assertIn("codex_bundles", rendered)
        self.assertIn("来源机器", rendered)
        self.assertIn("Bundle 类别", rendered)
        self.assertIn("打包时间", rendered)
        self.assertIn("来源路径", rendered)
        self.assertIn("codex_bundles/Studio-Mac/sessions/single/20260411", rendered)
        self.assertNotIn("导出机器", rendered)
        self.assertNotIn("导出方式", rendered)

    def test_local_skill_browser_can_export_checked_skills(self) -> None:
        app = FakeSkillBrowserApp()
        first = skill_summary("first-skill")
        second = skill_summary("second-skill", source_root="codex")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "e", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="view")

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导出 2 个 Skills")
        self.assertEqual(cli_args, ["export-skills", str(first.skill_dir), str(second.skill_dir)])
        self.assertFalse(kwargs["danger"])

    def test_local_skill_browser_can_export_current_and_select_all_matching_skills(self) -> None:
        app = FakeSkillBrowserApp()
        first = skill_summary("first-skill")
        second = skill_summary("second-skill", source_root="codex")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["e", "a", "e", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="view")

        self.assertEqual(app.run_calls[0][0], "导出 Skill first-skill")
        self.assertEqual(app.run_calls[0][1], ["export-skills", str(first.skill_dir)])
        self.assertEqual(app.run_calls[1][0], "导出 2 个 Skills")
        self.assertEqual(app.run_calls[1][1], ["export-skills", str(first.skill_dir), str(second.skill_dir)])

    def test_skill_bundle_browser_can_import_checked_bundles(self) -> None:
        app = FakeSkillBundleBrowserApp()
        first = skill_bundle_summary("bundle-a")
        second = skill_bundle_summary("bundle-b")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "i", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_skill_bundles", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_skill_bundle_browser(app, mode="view")

        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "导入 2 个 Skills Bundle")
        self.assertEqual(cli_args, ["import-skill-bundle", str(first.bundle_dir), str(second.bundle_dir)])
        self.assertFalse(kwargs["danger"])

    def test_skill_bundle_browser_can_import_current_and_select_all_matching_bundles(self) -> None:
        app = FakeSkillBundleBrowserApp()
        first = skill_bundle_summary("bundle-a")
        second = skill_bundle_summary("bundle-b")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["i", "a", "i", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_skill_bundles", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_skill_bundle_browser(app, mode="view")

        self.assertEqual(app.run_calls[0][0], "导入 Skills Bundle bundle-a")
        self.assertEqual(app.run_calls[0][1], ["import-skill-bundle", str(first.bundle_dir)])
        self.assertEqual(app.run_calls[1][0], "导入 2 个 Skills Bundle")
        self.assertEqual(app.run_calls[1][1], ["import-skill-bundle", str(first.bundle_dir), str(second.bundle_dir)])

    def test_skill_delete_browser_can_delete_selected_skill(self) -> None:
        app = FakeSkillDeleteBrowserApp()
        selected = skill_summary("delete-me")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["x", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[selected]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="delete")

        self.assertEqual(app.confirm_calls[0][0], ["delete-skill", str(selected.skill_dir)])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除本机 Skill delete-me")
        self.assertEqual(cli_args, ["delete-skill", str(selected.skill_dir)])
        self.assertTrue(kwargs["danger"])

    def test_skill_delete_browser_can_delete_checked_skills(self) -> None:
        app = FakeSkillDeleteBrowserApp()
        first = skill_summary("first-skill")
        second = skill_summary("second-skill", source_root="codex")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=[" ", "DOWN", " ", "x", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="delete")

        self.assertEqual(app.confirm_calls[0][0], ["delete-skill", str(first.skill_dir), str(second.skill_dir)])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除 2 个本机 Skills")
        self.assertEqual(cli_args, ["delete-skill", str(first.skill_dir), str(second.skill_dir)])
        self.assertTrue(kwargs["danger"])

    def test_skill_delete_browser_can_select_all_matching_custom_skills_then_delete(self) -> None:
        app = FakeSkillDeleteBrowserApp()
        first = skill_summary("first-skill")
        second = skill_summary("second-skill", source_root="codex")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "x", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[first, second]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="delete")

        self.assertEqual(app.confirm_calls[0][0], ["delete-skill", str(first.skill_dir), str(second.skill_dir)])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除 2 个本机 Skills")
        self.assertEqual(cli_args, ["delete-skill", str(first.skill_dir), str(second.skill_dir)])
        self.assertTrue(kwargs["danger"])


if __name__ == "__main__":
    unittest.main()
