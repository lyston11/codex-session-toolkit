"""Compatibility wrapper for the refactored TUI package.

Prefer importing runtime behavior from ``codex_session_toolkit.tui.app`` and
TUI-only dataclasses from ``codex_session_toolkit.tui.view_models``. Menu
metadata lives in ``codex_session_toolkit.tui.menu_catalog``.
This wrapper is intentionally forwarding-only and should stay limited to
legacy import compatibility until downstream callers migrate.
"""

from __future__ import annotations

from importlib import import_module

_COMPAT_EXPORTS = {
    "BatchBundleImportSelection": (".tui.view_models", "BatchBundleImportSelection"),
    "BundleBrowserSnapshot": (".tui.view_models", "BundleBrowserSnapshot"),
    "BundleCategoryFolderOption": (".tui.view_models", "BundleCategoryFolderOption"),
    "BundleMachineFolderOption": (".tui.view_models", "BundleMachineFolderOption"),
    "BundleProjectFolderOption": (".tui.view_models", "BundleProjectFolderOption"),
    "ToolkitAppContext": (".tui.view_models", "ToolkitAppContext"),
    "ToolkitTuiApp": (".tui.app", "ToolkitTuiApp"),
    "TuiMenuAction": (".tui.view_models", "TuiMenuAction"),
    "TuiMenuSection": (".tui.view_models", "TuiMenuSection"),
    "build_tui_menu_actions": (".tui.menu_catalog", "build_tui_menu_actions"),
    "build_tui_menu_sections": (".tui.menu_catalog", "build_tui_menu_sections"),
    "format_bundle_source_label": (".tui.app", "format_bundle_source_label"),
    "run_cleanup_mode": (".tui.maintenance_modes", "run_cleanup_mode"),
    "run_clone_mode": (".tui.maintenance_modes", "run_clone_mode"),
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
