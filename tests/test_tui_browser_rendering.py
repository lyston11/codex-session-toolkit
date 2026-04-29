import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.tui.browser_flows import render_browser_frame  # noqa: E402
from codex_session_toolkit.tui.terminal import Ansi  # noqa: E402


class FakeBrowserApp:
    def _fit_lines_to_screen(self, lines):
        return lines


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


if __name__ == "__main__":
    unittest.main()
