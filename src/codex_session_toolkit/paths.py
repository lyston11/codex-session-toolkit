"""Path helpers for Codex session data and local bundle workspaces."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ImportError:  # pragma: no cover - exercised through the fallback parser tests
    tomllib = None  # type: ignore[assignment]


STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")
THREAD_HISTORY_DB_RE = re.compile(r"^thread_history_(\d+)\.sqlite$")


@dataclass(frozen=True)
class CodexPaths:
    home: Path = Path.home()

    @property
    def code_dir(self) -> Path:
        return self.home / ".codex"

    @property
    def sessions_dir(self) -> Path:
        return self.code_dir / "sessions"

    @property
    def archived_sessions_dir(self) -> Path:
        return self.code_dir / "archived_sessions"

    @property
    def history_file(self) -> Path:
        return self.code_dir / "history.jsonl"

    @property
    def index_file(self) -> Path:
        return self.code_dir / "session_index.jsonl"

    @property
    def state_file(self) -> Path:
        return self.code_dir / ".codex-global-state.json"

    @property
    def config_file(self) -> Path:
        return self.code_dir / "config.toml"

    @property
    def local_bundle_workspace(self) -> Path:
        return Path.cwd() / "codex_bundles"

    @property
    def legacy_session_bundle_workspace(self) -> Path:
        return Path.cwd() / "codex_sessions"

    @property
    def default_bundle_root(self) -> Path:
        return self.local_bundle_workspace

    @property
    def default_desktop_bundle_root(self) -> Path:
        return self.local_bundle_workspace

    @property
    def legacy_bundle_root(self) -> Path:
        return self.legacy_session_bundle_workspace / "bundles"

    @property
    def legacy_desktop_bundle_root(self) -> Path:
        return self.legacy_session_bundle_workspace / "desktop_bundles"

    @property
    def legacy_session_bundle_root(self) -> Path:
        return self.legacy_session_bundle_workspace

    @property
    def skills_bundle_root(self) -> Path:
        return self.local_bundle_workspace

    @property
    def agents_skills_dir(self) -> Path:
        return self.home / ".agents" / "skills"

    @property
    def codex_skills_dir(self) -> Path:
        return self.code_dir / "skills"

    @property
    def sqlite_dir(self) -> Path:
        configured = _configured_sqlite_home(self.config_file)
        if configured:
            return _resolve_configured_path(configured)
        environment_value = os.environ.get("CODEX_SQLITE_HOME", "").strip()
        if environment_value:
            return _resolve_configured_path(environment_value)
        return self.code_dir

    @property
    def thread_history_db(self) -> Path:
        return self.sqlite_dir / "thread_history_1.sqlite"

    def latest_state_db(self) -> Optional[Path]:
        matches = sorted(self.sqlite_dir.glob("state_*.sqlite"), key=_state_db_sort_key)
        return matches[-1] if matches else None

    def latest_thread_history_db(self) -> Optional[Path]:
        matches = sorted(
            self.sqlite_dir.glob("thread_history_*.sqlite"),
            key=_thread_history_db_sort_key,
        )
        return matches[-1] if matches else None


def _state_db_sort_key(path: Path) -> tuple[int, int, str]:
    match = STATE_DB_RE.match(path.name)
    version = int(match.group(1)) if match else -1
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return version, modified_ns, path.name


def _thread_history_db_sort_key(path: Path) -> tuple[int, int, str]:
    match = THREAD_HISTORY_DB_RE.match(path.name)
    version = int(match.group(1)) if match else -1
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return version, modified_ns, path.name


def _configured_sqlite_home(config_file: Path) -> str:
    try:
        raw = config_file.read_bytes()
    except OSError:
        return ""

    if tomllib is not None:
        try:
            parsed: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return ""
        value = parsed.get("sqlite_home")
        return value.strip() if isinstance(value, str) else ""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return _fallback_top_level_string(text, "sqlite_home")


def _fallback_top_level_string(text: str, key: str) -> str:
    assignment = re.compile(rf"^{re.escape(key)}\s*=\s*(.+?)\s*$")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            break
        match = assignment.match(line)
        if not match:
            continue
        raw_value = _strip_toml_comment(match.group(1)).strip()
        if len(raw_value) < 2:
            return ""
        if raw_value.startswith('"') and raw_value.endswith('"'):
            try:
                value = json.loads(raw_value)
            except (TypeError, ValueError):
                return ""
            return value.strip() if isinstance(value, str) else ""
        if raw_value.startswith("'") and raw_value.endswith("'"):
            return raw_value[1:-1].strip()
        return ""
    return ""


def _strip_toml_comment(value: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in {'"', "'"}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            continue
        if char == "#" and not quote:
            return value[:index]
    return value


def _resolve_configured_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path
