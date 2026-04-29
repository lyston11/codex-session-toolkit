"""Provider resolution helpers."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..errors import ToolkitError
from ..paths import CodexPaths

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None


def detect_provider(paths: CodexPaths, explicit: str = "") -> str:
    if explicit:
        return explicit

    config_file = paths.config_file
    if config_file.exists():
        provider = _provider_from_config(config_file)
        if provider:
            return provider

    provider = _provider_from_latest_threads(paths)
    if provider:
        return provider

    provider = _provider_from_latest_session(paths)
    if provider:
        return provider

    raise ToolkitError("Could not detect model_provider from ~/.codex/config.toml")


def _provider_from_config(config_file: Path) -> str:
    if tomllib is not None:
        try:
            with config_file.open("rb") as fh:
                data = tomllib.load(fh)
            provider = data.get("model_provider")
            if isinstance(provider, str) and provider:
                return provider
        except Exception:
            pass

    text = config_file.read_text(encoding="utf-8")
    match = re.search(r'^\s*model_provider\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match:
        return match.group(1)
    return ""


def _provider_from_latest_threads(paths: CodexPaths) -> str:
    state_db = paths.latest_state_db()
    if not state_db or not state_db.is_file():
        return ""

    try:
        conn = sqlite3.connect(state_db)
        try:
            row = conn.execute(
                """
                select model_provider
                from threads
                where model_provider is not null and model_provider != ''
                order by updated_at desc
                limit 1
                """
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""

    provider = row[0] if row else ""
    return provider if isinstance(provider, str) else ""


def _provider_from_latest_session(paths: CodexPaths) -> str:
    session_roots = [paths.sessions_dir, paths.archived_sessions_dir]
    session_files: list[Path] = []
    for root in session_roots:
        if root.exists():
            session_files.extend(root.rglob("rollout-*.jsonl"))

    def sort_key(path: Path) -> tuple[int, str]:
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return modified, str(path)

    for session_file in sorted(session_files, key=sort_key, reverse=True):
        provider = _provider_from_session_file(session_file)
        if provider:
            return provider
    return ""


def _provider_from_session_file(session_file: Path) -> str:
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                payload = obj.get("payload")
                if obj.get("type") != "session_meta" or not isinstance(payload, dict):
                    continue
                provider = payload.get("model_provider")
                return provider if isinstance(provider, str) else ""
    except OSError:
        return ""
    return ""
