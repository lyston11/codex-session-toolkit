"""Pure navigation helpers shared by interactive TUI flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple


@dataclass(frozen=True)
class HomeNavigationResult:
    selected_section_index: int
    current_view: str
    show_help: bool = False
    exit_requested: bool = False


@dataclass(frozen=True)
class SectionNavigationResult:
    selected_section_index: int
    current_view: str
    action_offset: int
    execute_selected: bool = False
    matched_hotkey: str = ""
    show_help: bool = False
    exit_requested: bool = False


@dataclass(frozen=True)
class PickerNavigationResult:
    selected_index: int
    confirm_selected: bool = False
    show_detail: bool = False
    exit_requested: bool = False


def clamp_selected_index(selected_index: int, item_count: int) -> int:
    if item_count <= 0:
        return 0
    return max(0, min(selected_index, item_count - 1))


def move_wrapped_index(selected_index: int, item_count: int, step: int) -> int:
    if item_count <= 0:
        return 0
    normalized = clamp_selected_index(selected_index, item_count)
    return (normalized + step) % item_count


def selection_window(total_count: int, selected_index: int, max_visible: int) -> Tuple[int, int]:
    if total_count <= 0:
        return 0, 0
    max_visible = max(1, min(max_visible, total_count))
    selected_index = clamp_selected_index(selected_index, total_count)
    start = max(0, selected_index - max_visible // 2)
    start = min(start, max(0, total_count - max_visible))
    return start, min(total_count, start + max_visible)


def cycle_option_key(options: Sequence[Tuple[str, str]], current_key: str) -> str:
    if not options:
        return current_key
    current_index = 0
    for idx, (candidate_key, _) in enumerate(options):
        if candidate_key == current_key:
            current_index = idx
            break
    return options[(current_index + 1) % len(options)][0]


def apply_picker_key(key: object, *, selected_index: int, item_count: int) -> PickerNavigationResult:
    normalized_index = clamp_selected_index(selected_index, item_count)
    key_str = str(key).strip().lower()

    if key in ("UP", "k", "K"):
        return PickerNavigationResult(
            selected_index=move_wrapped_index(normalized_index, item_count, -1),
        )
    if key in ("DOWN", "j", "J"):
        return PickerNavigationResult(
            selected_index=move_wrapped_index(normalized_index, item_count, 1),
        )
    if key == "ENTER":
        return PickerNavigationResult(selected_index=normalized_index, confirm_selected=True)
    if key_str in {"q", "quit", "esc", "0"} or key == "ESC":
        return PickerNavigationResult(selected_index=normalized_index, exit_requested=True)
    if key_str in {"d", " "}:
        return PickerNavigationResult(selected_index=normalized_index, show_detail=True)
    return PickerNavigationResult(selected_index=normalized_index)


def apply_home_key(key: object, *, selected_section_index: int, section_count: int) -> HomeNavigationResult:
    normalized_index = clamp_selected_index(selected_section_index, section_count)
    key_str = str(key).strip().lower()

    if key in ("UP", "k", "K", "LEFT", "PAGE_UP"):
        return HomeNavigationResult(
            selected_section_index=move_wrapped_index(normalized_index, section_count, -1),
            current_view="home",
        )
    if key in ("DOWN", "j", "J", "RIGHT", "PAGE_DOWN"):
        return HomeNavigationResult(
            selected_section_index=move_wrapped_index(normalized_index, section_count, 1),
            current_view="home",
        )
    if key == "ENTER":
        return HomeNavigationResult(selected_section_index=normalized_index, current_view="section")
    if key_str in {"q", "quit", "exit", "0"}:
        return HomeNavigationResult(selected_section_index=normalized_index, current_view="home", exit_requested=True)
    if key_str in {"h", "help", "?"}:
        return HomeNavigationResult(selected_section_index=normalized_index, current_view="home", show_help=True)
    if key_str.isdigit() and key_str != "0":
        requested_index = min(max(1, int(key_str)), max(1, section_count)) - 1
        return HomeNavigationResult(
            selected_section_index=requested_index,
            current_view="section",
        )
    return HomeNavigationResult(selected_section_index=normalized_index, current_view="home")


def apply_section_key(
    key: object,
    *,
    selected_section_index: int,
    section_count: int,
    action_offset: int,
    action_count: int,
) -> SectionNavigationResult:
    normalized_section = clamp_selected_index(selected_section_index, section_count)
    normalized_action = clamp_selected_index(action_offset, action_count)
    key_str = str(key).strip().lower()

    if key in ("UP", "k", "K"):
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="section",
            action_offset=move_wrapped_index(normalized_action, action_count, -1),
        )
    if key in ("DOWN", "j", "J"):
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="section",
            action_offset=move_wrapped_index(normalized_action, action_count, 1),
        )
    if key in ("LEFT", "PAGE_UP"):
        if key == "LEFT":
            return SectionNavigationResult(
                selected_section_index=normalized_section,
                current_view="home",
                action_offset=normalized_action,
            )
        return SectionNavigationResult(
            selected_section_index=move_wrapped_index(normalized_section, section_count, -1),
            current_view="section",
            action_offset=normalized_action,
        )
    if key in ("RIGHT", "PAGE_DOWN"):
        return SectionNavigationResult(
            selected_section_index=move_wrapped_index(normalized_section, section_count, 1),
            current_view="section",
            action_offset=normalized_action,
        )
    if key == "ENTER":
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="section",
            action_offset=normalized_action,
            execute_selected=True,
        )
    if key_str in {"q", "esc", "b", "back"} or key == "ESC":
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="home",
            action_offset=normalized_action,
        )
    if key_str == "0":
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="section",
            action_offset=normalized_action,
            exit_requested=True,
        )
    if key_str in {"h", "help", "?"}:
        return SectionNavigationResult(
            selected_section_index=normalized_section,
            current_view="section",
            action_offset=normalized_action,
            show_help=True,
        )
    return SectionNavigationResult(
        selected_section_index=normalized_section,
        current_view="section",
        action_offset=normalized_action,
        matched_hotkey=key_str,
    )
