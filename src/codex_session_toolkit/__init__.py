"""Codex Session Toolkit package metadata and stable top-level exports."""

from __future__ import annotations

from importlib import import_module

APP_DISPLAY_NAME = "Codex Session Toolkit"
APP_COMMAND = "codex-session-toolkit"
__version__ = "0.1.0"

_STABLE_EXPORTS = {
    "CodexPaths": (".paths", "CodexPaths"),
    "ToolkitError": (".errors", "ToolkitError"),
    "build_app_context": (".cli", "build_app_context"),
    "main": (".cli", "main"),
    "resolve_target_model_provider": (".cli", "resolve_target_model_provider"),
    "run_cli": (".commands", "run_cli"),
}

__all__ = [
    "APP_COMMAND",
    "APP_DISPLAY_NAME",
    "__version__",
    "CodexPaths",
    "ToolkitError",
    "build_app_context",
    "main",
    "resolve_target_model_provider",
    "run_cli",
]


def __getattr__(name: str):
    if name in _STABLE_EXPORTS:
        module_name, attr_name = _STABLE_EXPORTS[name]
        value = getattr(import_module(module_name, package=__name__), attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
