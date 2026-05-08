"""Path helpers for Codex session data and local bundle workspaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")


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

    def latest_state_db(self) -> Optional[Path]:
        matches = sorted(self.code_dir.glob("state_*.sqlite"), key=_state_db_sort_key)
        return matches[-1] if matches else None


def _state_db_sort_key(path: Path) -> tuple[int, int, str]:
    match = STATE_DB_RE.match(path.name)
    version = int(match.group(1)) if match else -1
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return version, modified_ns, path.name
