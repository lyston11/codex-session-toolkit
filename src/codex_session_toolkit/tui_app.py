"""Compatibility wrapper for the refactored TUI package.

Prefer importing from ``codex_session_toolkit.tui.app`` directly in new code.
This wrapper is intentionally forwarding-only and should stay limited to
legacy import compatibility until downstream callers migrate.
"""

from __future__ import annotations

from importlib import import_module

_COMPAT_EXPORTS = {
    "BatchBundleImportSelection": (".tui.app", "BatchBundleImportSelection"),
    "BundleBrowserSnapshot": (".tui.app", "BundleBrowserSnapshot"),
    "BundleCategoryFolderOption": (".tui.app", "BundleCategoryFolderOption"),
    "BundleMachineFolderOption": (".tui.app", "BundleMachineFolderOption"),
    "BundleProjectFolderOption": (".tui.app", "BundleProjectFolderOption"),
    "ToolkitAppContext": (".tui.app", "ToolkitAppContext"),
    "ToolkitTuiApp": (".tui.app", "ToolkitTuiApp"),
    "TuiMenuAction": (".tui.app", "TuiMenuAction"),
    "TuiMenuSection": (".tui.app", "TuiMenuSection"),
    "build_tui_menu_actions": (".tui.app", "build_tui_menu_actions"),
    "build_tui_menu_sections": (".tui.app", "build_tui_menu_sections"),
    "format_bundle_source_label": (".tui.app", "format_bundle_source_label"),
    "run_cleanup_mode": (".tui.app", "run_cleanup_mode"),
    "run_clone_mode": (".tui.app", "run_clone_mode"),
    "run_tui": (".tui.app", "run_tui"),
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
