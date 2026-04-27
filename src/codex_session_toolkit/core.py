"""Legacy compatibility facade with a smaller stable public surface.

Stable programmatic imports should come from ``codex_session_toolkit.api``.
This module remains forwarding-only for historical callers and should not
gain new business logic or new stable exports.
"""

from __future__ import annotations

from importlib import import_module

from .api import *  # noqa: F401,F403
from .api import __all__ as _API_ALL

_LEGACY_EXPORTS = {
    "backup_file": (".support", "backup_file"),
    "build_batch_export_root": (".support", "build_batch_export_root"),
    "build_clone_index": (".services.clone", "build_clone_index"),
    "build_single_export_root": (".support", "build_single_export_root"),
    "bundle_directory_sort_key": (".stores.bundle_scanner", "bundle_directory_sort_key"),
    "classify_session_kind": (".support", "classify_session_kind"),
    "clone_session_file": (".services.clone", "clone_session_file"),
    "collect_bundle_summaries": (".stores.bundle_scanner", "collect_bundle_summaries"),
    "collect_history_lines_for_session": (".stores.history", "collect_history_lines_for_session"),
    "collect_known_bundle_summaries": (".stores.bundle_scanner", "collect_known_bundle_summaries"),
    "collect_session_ids_for_kind": (".stores.session_files", "collect_session_ids_for_kind"),
    "collect_session_summaries": (".stores.session_files", "collect_session_summaries"),
    "ensure_desktop_workspace_root": (".stores.desktop_state", "ensure_desktop_workspace_root"),
    "ensure_path_within_dir": (".validation", "ensure_path_within_dir"),
    "export_batch_slug": (".support", "export_batch_slug"),
    "extract_iso_timestamp": (".support", "extract_iso_timestamp"),
    "extract_last_timestamp": (".stores.session_files", "extract_last_timestamp"),
    "extract_session_field_from_file": (".stores.session_files", "extract_session_field_from_file"),
    "extract_timestamp_from_rollout_name": (".stores.session_files", "extract_timestamp_from_rollout_name"),
    "find_session_file": (".stores.session_files", "find_session_file"),
    "first_history_messages": (".stores.history", "first_history_messages"),
    "first_history_text": (".stores.history", "first_history_text"),
    "iso_to_epoch": (".support", "iso_to_epoch"),
    "iter_bundle_directories_under_root": (".stores.bundle_scanner", "iter_bundle_directories_under_root"),
    "iter_known_bundle_directories": (".stores.bundle_scanner", "iter_known_bundle_directories"),
    "iter_session_files": (".stores.session_files", "iter_session_files"),
    "load_existing_index": (".stores.index", "load_existing_index"),
    "load_manifest": (".validation", "load_manifest"),
    "nearest_existing_parent": (".support", "nearest_existing_parent"),
    "normalize_bundle_root": (".support", "normalize_bundle_root"),
    "normalize_iso": (".support", "normalize_iso"),
    "normalize_updated_at": (".validation", "normalize_updated_at"),
    "parse_jsonl_records": (".stores.session_files", "parse_jsonl_records"),
    "prepare_session_for_import": (".stores.desktop_state", "prepare_session_for_import"),
    "read_session_payload": (".stores.session_files", "read_session_payload"),
    "resolve_bundle_dir": (".stores.bundle_repository", "resolve_bundle_dir"),
    "restrict_to_local_bundle_workspace": (".support", "restrict_to_local_bundle_workspace"),
    "salvage_index_line": (".stores.index", "salvage_index_line"),
    "session_id_from_filename": (".stores.session_files", "session_id_from_filename"),
    "upsert_session_index": (".stores.index", "upsert_session_index"),
    "upsert_threads_table": (".stores.desktop_state", "upsert_threads_table"),
    "validate_bundle_directory": (".stores.bundle_validation", "validate_bundle_directory"),
    "validate_jsonl_file": (".validation", "validate_jsonl_file"),
    "validate_relative_path": (".validation", "validate_relative_path"),
    "validate_session_id": (".validation", "validate_session_id"),
}

__all__ = list(_API_ALL)


def __getattr__(name: str):
    if name in _LEGACY_EXPORTS:
        module_name, attr_name = _LEGACY_EXPORTS[name]
        value = getattr(import_module(module_name, package=__package__), attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LEGACY_EXPORTS))
