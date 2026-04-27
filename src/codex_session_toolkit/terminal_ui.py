"""Compatibility wrapper for the refactored TUI terminal module.

Prefer importing from ``codex_session_toolkit.tui.terminal`` directly in new code.
This wrapper is intentionally forwarding-only and should stay limited to
legacy import compatibility until downstream callers migrate.
"""

from __future__ import annotations

from importlib import import_module

_COMPAT_EXPORTS = {
    "ANSI_ESCAPE_RE": (".tui.terminal", "ANSI_ESCAPE_RE"),
    "ASCII_BOX_CHARS": (".tui.terminal", "ASCII_BOX_CHARS"),
    "ASCII_GLYPHS": (".tui.terminal", "ASCII_GLYPHS"),
    "Ansi": (".tui.terminal", "Ansi"),
    "COLOR_ENABLED": (".tui.terminal", "COLOR_ENABLED"),
    "LOGO_FONT_3X7": (".tui.terminal", "LOGO_FONT_3X7"),
    "LOGO_FONT_4X5": (".tui.terminal", "LOGO_FONT_4X5"),
    "LOGO_FONT_4X7": (".tui.terminal", "LOGO_FONT_4X7"),
    "LOGO_FONT_BANNER": (".tui.terminal", "LOGO_FONT_BANNER"),
    "UNICODE_BOX_CHARS": (".tui.terminal", "UNICODE_BOX_CHARS"),
    "UNICODE_GLYPHS": (".tui.terminal", "UNICODE_GLYPHS"),
    "align_line": (".tui.terminal", "align_line"),
    "app_logo_lines": (".tui.terminal", "app_logo_lines"),
    "clear_screen": (".tui.terminal", "clear_screen"),
    "configure_text_streams": (".tui.terminal", "configure_text_streams"),
    "display_width": (".tui.terminal", "display_width"),
    "ellipsize_middle": (".tui.terminal", "ellipsize_middle"),
    "glyphs": (".tui.terminal", "glyphs"),
    "horizontal_rule": (".tui.terminal", "horizontal_rule"),
    "is_interactive_terminal": (".tui.terminal", "is_interactive_terminal"),
    "pad_right": (".tui.terminal", "pad_right"),
    "read_key": (".tui.terminal", "read_key"),
    "render_box": (".tui.terminal", "render_box"),
    "strip_ansi": (".tui.terminal", "strip_ansi"),
    "style_text": (".tui.terminal", "style_text"),
    "term_height": (".tui.terminal", "term_height"),
    "term_width": (".tui.terminal", "term_width"),
    "tui_width": (".tui.terminal", "tui_width"),
}

__all__ = list(_COMPAT_EXPORTS)


def __getattr__(name: str):
    if name in _COMPAT_EXPORTS:
        module_name, attr_name = _COMPAT_EXPORTS[name]
        value = getattr(import_module(module_name, package=__package__), attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
