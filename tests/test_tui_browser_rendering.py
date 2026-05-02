import io
import os
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.tui.browser_flows import render_browser_frame  # noqa: E402
from codex_session_toolkit.tui.progress_flows import _render_progress  # noqa: E402
from codex_session_toolkit.tui.prompt_flows import prompt_choice, render_prompt_choice  # noqa: E402
from codex_session_toolkit.tui.terminal import Ansi  # noqa: E402


class FakeBrowserApp:
    def _fit_lines_to_screen(self, lines):
        return lines


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


if __name__ == "__main__":
    unittest.main()
