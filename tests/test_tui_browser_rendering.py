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

from codex_session_toolkit.models import LocalSkillSummary, SessionSummary  # noqa: E402
from codex_session_toolkit.tui.browser_flows import open_archived_session_browser, open_local_skill_browser, render_browser_frame  # noqa: E402
from codex_session_toolkit.tui.progress_flows import _render_progress  # noqa: E402
from codex_session_toolkit.tui.prompt_flows import prompt_choice, render_prompt_choice  # noqa: E402
from codex_session_toolkit.tui.terminal import Ansi  # noqa: E402


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


def skill_summary(name: str, *, source_root: str = "agents", location_kind: str = "custom") -> LocalSkillSummary:
    return LocalSkillSummary(
        name=name,
        source_root=source_root,
        relative_dir=name,
        skill_dir=Path(f"/tmp/home/.{source_root}/skills/{name}"),
        location_kind=location_kind,
        content_hash=f"hash-{name}",
    )


class TuiBrowserRenderingTests(unittest.TestCase):
    def test_browser_frame_repaints_without_full_screen_clear(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            render_browser_frame(
                FakeBrowserApp(),
                title="浏览本机会话",
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

    def test_archived_browser_can_delete_all_sessions(self) -> None:
        app = FakeArchivedBrowserApp()
        dry_run_result = SimpleNamespace(files_to_delete=[Path("/tmp/a.jsonl"), Path("/tmp/b.jsonl")])

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.get_session_summaries", return_value=[archived_summary("11111111-2222-4333-8444-555555555555")]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.delete_archived_sessions", return_value=dry_run_result))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_archived_session_browser(app)

        self.assertEqual(app.confirm_calls[0][0], ["delete-archived-sessions"])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除全部归档会话")
        self.assertEqual(cli_args, ["delete-archived-sessions"])
        self.assertTrue(kwargs["danger"])

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

    def test_skill_delete_browser_can_delete_all_custom_skills(self) -> None:
        app = FakeSkillDeleteBrowserApp()
        custom = skill_summary("custom-skill")

        with ExitStack() as stack:
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.read_key", side_effect=["a", "q"]))
            stack.enter_context(patch("codex_session_toolkit.tui.browser_flows.list_local_skills", return_value=[custom]))
            stack.enter_context(redirect_stdout(TtyStringIO()))
            open_local_skill_browser(app, mode="delete")

        self.assertEqual(app.confirm_calls[0][0], ["delete-skill", "--all"])
        action_name, cli_args, kwargs = app.run_calls[0]
        self.assertEqual(action_name, "删除全部本机 Skills")
        self.assertEqual(cli_args, ["delete-skill", "--all"])
        self.assertTrue(kwargs["danger"])


if __name__ == "__main__":
    unittest.main()
