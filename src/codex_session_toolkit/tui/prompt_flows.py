"""Prompt and confirmation flows extracted from the TUI app shell."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from .navigation_state import apply_list_key
from .terminal import (
    Ansi,
    align_line,
    app_logo_lines,
    glyphs,
    render_box,
    style_text,
    term_height,
    term_width,
)
from .terminal_io import read_key

if TYPE_CHECKING:
    from .app import ToolkitTuiApp


def prompt_value(
    app: "ToolkitTuiApp",
    *,
    title: str,
    prompt_label: str,
    help_lines: List[str],
    default: str = "",
    allow_empty: bool = True,
) -> Optional[str]:
    box_width = app._print_branded_header(title)
    rendered_help = list(help_lines)
    rendered_help.append("输入 q 取消并返回。")
    for line in render_box(rendered_help, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        print(line)
    print("")

    suffix = f"（默认：{default}）" if default else ""
    raw = input(style_text(f"{prompt_label}{suffix}：", Ansi.BOLD, Ansi.CYAN)).strip()
    if raw.lower() in {"q", "quit", "esc", "0"}:
        return None
    if not raw:
        if default:
            return default
        if allow_empty:
            return ""
        return None
    return raw


def confirm_toggle(
    app: "ToolkitTuiApp",
    *,
    title: str,
    question: str,
    yes_label: str,
    no_label: str,
    default_yes: bool = False,
) -> bool:
    default_hint = yes_label if default_yes else no_label
    answer = prompt_value(
        app,
        title=title,
        prompt_label=f"{question}（{yes_label}/{no_label}）",
        help_lines=[
            f"输入 {yes_label} 或 {no_label}。",
            f"直接回车默认选择：{default_hint}",
        ],
        default=yes_label if default_yes else no_label,
        allow_empty=False,
    )
    return str(answer).strip().lower() == yes_label.lower()


def render_prompt_choice(
    app: "ToolkitTuiApp",
    *,
    title: str,
    prompt_label: str,
    help_lines: List[str],
    choices: Sequence[Tuple[str, str]],
    selected_index: int,
    allow_cancel: bool = True,
) -> None:
    box_width, center = app._screen_layout()
    pointer = glyphs().get("pointer", ">")

    selected_index = max(0, min(selected_index, len(choices) - 1))
    _, selected_label = choices[selected_index]

    header_lines: List[str] = []
    for line in app_logo_lines(max_width=100):
        header_lines.append(align_line(line, box_width, center=center))
    header_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    header_lines.append(align_line(style_text(title, Ansi.DIM), box_width, center=center))
    header_lines.append(align_line(style_text(f"当前选择：{selected_label}", Ansi.DIM), box_width, center=center))
    header_lines.append("")

    choice_lines = [style_text(prompt_label, Ansi.BOLD)]
    for idx, (key, label) in enumerate(choices):
        hotkey = f"[{key}]"
        item_label = f"{hotkey} {label}"
        if idx == selected_index:
            prefix = style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
            choice_lines.append(prefix + style_text(item_label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.CYAN))
        else:
            choice_lines.append("  " + style_text(hotkey, Ansi.DIM, Ansi.CYAN) + " " + label)
    choice_box_lines = render_box(choice_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.CYAN))

    shortcut_labels = "/".join(key for key, _ in choices)
    footer = "↑/↓ 选择  |  Enter 确认"
    if shortcut_labels:
        footer += f"  |  {shortcut_labels} 快捷选择"
    if allow_cancel:
        footer += "  |  q/←/Esc 返回"
    footer_lines = ["", style_text(footer, Ansi.DIM)]

    max_rows = _prompt_screen_height(app)
    compact_header_lines = [
        align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center),
        align_line(style_text(title, Ansi.DIM), box_width, center=center),
        align_line(style_text(f"当前选择：{selected_label}", Ansi.DIM), box_width, center=center),
        "",
    ]
    minimum_rows = len(header_lines) + 3 + 1 + len(choice_box_lines) + len(footer_lines)
    if minimum_rows > max_rows:
        header_lines = compact_header_lines

    tail_lines = [""] + choice_box_lines + footer_lines
    available_help_rows = max_rows - len(header_lines) - len(tail_lines)
    if available_help_rows >= 3 and help_lines:
        help_content_limit = max(1, available_help_rows - 2)
        help_box_lines = render_box(
            _fit_prompt_help_lines(help_lines, help_content_limit),
            width=box_width,
            border_codes=(Ansi.DIM, Ansi.BLUE),
        )
        output_lines = header_lines + help_box_lines + tail_lines
    else:
        output_lines = header_lines + tail_lines

    if len(output_lines) > max_rows:
        output_lines = output_lines[-max_rows:]

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    full_output = "\n".join(line + clear_to_eol for line in output_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()


def _prompt_screen_height(app: "ToolkitTuiApp") -> int:
    screen_height = getattr(app, "_screen_height", None)
    if callable(screen_height):
        return max(8, int(screen_height()))
    return max(8, term_height())


def _fit_prompt_help_lines(help_lines: List[str], max_lines: int) -> List[str]:
    if max_lines <= 0:
        return []
    if len(help_lines) <= max_lines:
        return list(help_lines)
    if max_lines == 1:
        return list(help_lines[:1])
    visible_count = max_lines
    return list(help_lines[:visible_count])


def prompt_choice(
    app: "ToolkitTuiApp",
    *,
    title: str,
    prompt_label: str,
    help_lines: List[str],
    choices: Sequence[Tuple[str, str]],
    default: str = "",
    allow_cancel: bool = True,
) -> Optional[str]:
    if not choices:
        return None

    stdin_tty = getattr(sys.stdin, "isatty", lambda: False)()
    stdout_tty = getattr(sys.stdout, "isatty", lambda: False)()
    if not (stdin_tty and stdout_tty):
        base_help = list(help_lines)
        valid_keys = {key.lower() for key, _ in choices}

        while True:
            rendered_help = list(base_help)
            rendered_help.append("")
            for key, label in choices:
                rendered_help.append(f"{key} : {label}")
            if allow_cancel:
                rendered_help.append("输入 q 取消。")

            answer = prompt_value(
                app,
                title=title,
                prompt_label=prompt_label,
                help_lines=rendered_help,
                default=default,
                allow_empty=bool(default),
            )
            if answer is None:
                return None

            normalized = str(answer).strip().lower()
            if not normalized and default:
                normalized = default.lower()
            if allow_cancel and normalized in {"q", "quit", "esc", "0"}:
                return None
            if normalized in valid_keys:
                return normalized

            base_help = [style_text("输入无效，请重新选择。", Ansi.BOLD, Ansi.YELLOW)] + list(help_lines)

    normalized_choices = [(key.lower(), label) for key, label in choices]
    key_to_index = {key: idx for idx, (key, _) in enumerate(normalized_choices)}
    selected_index = key_to_index.get(default.lower(), 0) if default else 0
    last_size = (term_width(), term_height())
    needs_render = True

    while True:
        if needs_render:
            render_prompt_choice(
                app,
                title=title,
                prompt_label=prompt_label,
                help_lines=help_lines,
                choices=choices,
                selected_index=selected_index,
                allow_cancel=allow_cancel,
            )
            needs_render = False
        key = read_key(timeout_ms=200)
        current_size = (term_width(), term_height())
        if current_size != last_size:
            last_size = current_size
            needs_render = True
            continue
        if key is None:
            continue

        transition = apply_list_key(
            key,
            selected_index=selected_index,
            item_count=len(choices),
            allow_left_exit=allow_cancel,
            detail_keys=(),
        )
        if transition.selected_index != selected_index:
            selected_index = transition.selected_index
            needs_render = True
        if transition.confirm_selected:
            return normalized_choices[selected_index][0]
        if transition.exit_requested:
            return None
        if transition.matched_hotkey in key_to_index:
            return normalized_choices[key_to_index[transition.matched_hotkey]][0]


def prompt_execution_mode(
    app: "ToolkitTuiApp",
    *,
    title: str,
    default_dry_run: bool = False,
) -> Optional[bool]:
    choice = prompt_choice(
        app,
        title=title,
        prompt_label="选择执行方式",
        help_lines=["同一动作支持直接执行，也支持 Dry-run 预演。"],
        choices=[("r", "直接执行"), ("d", "Dry-run 预演")],
        default=("d" if default_dry_run else "r"),
    )
    if choice is None:
        return None
    return choice == "d"


def prompt_desktop_repair_scope(app: "ToolkitTuiApp") -> Optional[bool]:
    choice = prompt_choice(
        app,
        title="修复会话在 Desktop 中显示",
        prompt_label="选择修复范围",
        help_lines=[
            "默认修复 Desktop 已登记会话，并保留原始 source/originator。",
            "也可额外把尚未登记的 CLI 会话写入 Desktop threads。",
        ],
        choices=[
            ("d", "仅修复已登记会话"),
            ("c", "同时纳入未登记 CLI"),
        ],
        default="d",
    )
    if choice is None:
        return None
    return choice == "c"


def confirm_dangerous_action(
    app: "ToolkitTuiApp",
    cli_args: Sequence[str],
    *,
    title: str = "危险操作确认",
    subtitle: str = "该操作会删除文件，且无法恢复。",
    warning: str = "Clean 会删除旧版无标记副本文件。",
    impact: str = "旧版无标记 clone 文件",
) -> bool:
    box_width = app._print_branded_header(title, subtitle)
    info_lines = [
        style_text("【危险】", Ansi.BOLD, Ansi.RED) + warning,
        f"{style_text('执行方式', Ansi.DIM)} : 直接在 TUI 中执行",
        f"{style_text('影响范围', Ansi.DIM)} : {impact}",
        f"{style_text('命令预览', Ansi.DIM)} : {app._cli_preview(cli_args)}",
        "",
        "确认方式：输入 DELETE 并回车。",
        "取消方式：直接回车。",
    ]
    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.RED)):
        print(line)
    print("")
    return input(style_text("请输入 DELETE 确认执行：", Ansi.BOLD, Ansi.RED)).strip() == "DELETE"
