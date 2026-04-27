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
    for line in render_box(help_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        print(line)
    print("")

    suffix = f"（默认：{default}）" if default else ""
    raw = input(style_text(f"{prompt_label}{suffix}：", Ansi.BOLD, Ansi.CYAN)).strip()
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
    output_lines: List[str] = []

    selected_index = max(0, min(selected_index, len(choices) - 1))
    _, selected_label = choices[selected_index]

    for line in app_logo_lines(max_width=100):
        output_lines.append(align_line(line, box_width, center=center))
    output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    output_lines.append(align_line(style_text(title, Ansi.DIM), box_width, center=center))
    output_lines.append(align_line(style_text(f"当前选择：{selected_label}", Ansi.DIM), box_width, center=center))
    output_lines.append("")

    for line in render_box(help_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.BLUE)):
        output_lines.append(line)
    output_lines.append("")

    choice_lines = [style_text(prompt_label, Ansi.BOLD)]
    for idx, (key, label) in enumerate(choices):
        hotkey = f"[{key}]"
        item_label = f"{hotkey} {label}"
        if idx == selected_index:
            prefix = style_text(pointer, Ansi.BOLD, Ansi.BRIGHT_CYAN) + " "
            choice_lines.append(prefix + style_text(item_label, Ansi.BOLD, Ansi.UNDERLINE, Ansi.CYAN))
        else:
            choice_lines.append("  " + style_text(hotkey, Ansi.DIM, Ansi.CYAN) + " " + label)
    for line in render_box(choice_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.CYAN)):
        output_lines.append(line)
    output_lines.append("")

    shortcut_labels = "/".join(key for key, _ in choices)
    footer = "↑/↓ 选择  |  Enter 确认"
    if shortcut_labels:
        footer += f"  |  {shortcut_labels} 快捷选择"
    if allow_cancel:
        footer += "  |  q/←/Esc 返回"
    output_lines.append(style_text(footer, Ansi.DIM))

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    visible_lines = app._fit_lines_to_screen(output_lines)
    full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()


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

    while True:
        render_prompt_choice(
            app,
            title=title,
            prompt_label=prompt_label,
            help_lines=help_lines,
            choices=choices,
            selected_index=selected_index,
            allow_cancel=allow_cancel,
        )
        key = read_key(timeout_ms=200)
        current_size = (term_width(), term_height())
        if current_size != last_size:
            last_size = current_size
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
        selected_index = transition.selected_index
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
            "可只修复 Desktop 会话，也可顺手把 CLI 会话纳入 Desktop。",
        ],
        choices=[
            ("d", "仅修复 Desktop 会话"),
            ("c", "同时纳入 CLI 会话"),
        ],
        default="d",
    )
    if choice is None:
        return None
    return choice == "c"


def confirm_dangerous_action(app: "ToolkitTuiApp", cli_args: Sequence[str]) -> bool:
    box_width = app._print_branded_header("危险操作确认", "该操作会删除文件，且无法恢复。")
    info_lines = [
        style_text("【危险】", Ansi.BOLD, Ansi.RED) + "Clean 会删除旧版无标记副本文件。",
        f"{style_text('执行方式', Ansi.DIM)} : 直接在 TUI 中执行",
        f"{style_text('影响范围', Ansi.DIM)} : 旧版无标记 clone 文件",
        f"{style_text('命令预览', Ansi.DIM)} : {app._cli_preview(cli_args)}",
        "",
        "确认方式：输入 DELETE 并回车。",
        "取消方式：直接回车。",
    ]
    for line in render_box(info_lines, width=box_width, border_codes=(Ansi.DIM, Ansi.RED)):
        print(line)
    print("")
    return input(style_text("请输入 DELETE 确认执行：", Ansi.BOLD, Ansi.RED)).strip() == "DELETE"
