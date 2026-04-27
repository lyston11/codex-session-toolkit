import os
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(SRC_DIR))

from codex_session_toolkit.tui.navigation_state import (  # noqa: E402
    apply_home_key,
    apply_section_key,
    clamp_selected_index,
    cycle_option_key,
    move_wrapped_index,
    selection_window,
)


class TuiNavigationStateTests(unittest.TestCase):
    def test_list_navigation_helpers_wrap_and_clamp(self) -> None:
        self.assertEqual(clamp_selected_index(8, 3), 2)
        self.assertEqual(clamp_selected_index(-5, 0), 0)
        self.assertEqual(move_wrapped_index(0, 3, -1), 2)
        self.assertEqual(move_wrapped_index(2, 3, 1), 0)

    def test_selection_window_keeps_selected_item_visible(self) -> None:
        self.assertEqual(selection_window(0, 0, 5), (0, 0))
        self.assertEqual(selection_window(3, 0, 10), (0, 3))
        self.assertEqual(selection_window(20, 7, 6), (4, 10))
        self.assertEqual(selection_window(20, 19, 6), (14, 20))

    def test_cycle_option_key_advances_and_falls_back_to_first(self) -> None:
        options = [("", "全部"), ("a", "A"), ("b", "B")]

        self.assertEqual(cycle_option_key(options, ""), "a")
        self.assertEqual(cycle_option_key(options, "a"), "b")
        self.assertEqual(cycle_option_key(options, "missing"), "a")
        self.assertEqual(cycle_option_key([], "missing"), "missing")

    def test_apply_home_key_handles_navigation_help_exit_and_numeric_shortcuts(self) -> None:
        self.assertEqual(apply_home_key("UP", selected_section_index=0, section_count=3).selected_section_index, 2)
        self.assertEqual(apply_home_key("RIGHT", selected_section_index=1, section_count=3).selected_section_index, 2)
        self.assertEqual(apply_home_key("ENTER", selected_section_index=1, section_count=3).current_view, "section")
        self.assertTrue(apply_home_key("?", selected_section_index=1, section_count=3).show_help)
        self.assertTrue(apply_home_key("0", selected_section_index=1, section_count=3).exit_requested)
        shortcut = apply_home_key("3", selected_section_index=0, section_count=2)
        self.assertEqual(shortcut.selected_section_index, 1)
        self.assertEqual(shortcut.current_view, "section")

    def test_apply_section_key_handles_view_switching_and_hotkeys(self) -> None:
        prev_action = apply_section_key(
            "UP",
            selected_section_index=1,
            section_count=3,
            action_offset=0,
            action_count=4,
        )
        self.assertEqual(prev_action.action_offset, 3)

        next_section = apply_section_key(
            "PAGE_DOWN",
            selected_section_index=1,
            section_count=3,
            action_offset=2,
            action_count=4,
        )
        self.assertEqual(next_section.selected_section_index, 2)

        go_home = apply_section_key(
            "LEFT",
            selected_section_index=1,
            section_count=3,
            action_offset=2,
            action_count=4,
        )
        self.assertEqual(go_home.current_view, "home")

        execute_selected = apply_section_key(
            "ENTER",
            selected_section_index=1,
            section_count=3,
            action_offset=2,
            action_count=4,
        )
        self.assertTrue(execute_selected.execute_selected)

        show_help = apply_section_key(
            "?",
            selected_section_index=1,
            section_count=3,
            action_offset=2,
            action_count=4,
        )
        self.assertTrue(show_help.show_help)

        hotkey = apply_section_key(
            "x",
            selected_section_index=1,
            section_count=3,
            action_offset=2,
            action_count=4,
        )
        self.assertEqual(hotkey.matched_hotkey, "x")


if __name__ == "__main__":
    unittest.main()
