"""Configurable local Skill roots shared by discovery and transfer flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_ROOT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class SkillRoot:
    root_id: str
    label: str
    relative_path: str
    kind: str = "agent"
    enabled: bool = True
    builtin: bool = True

    def resolve(self, home: Path) -> Path:
        path = Path(self.relative_path).expanduser()
        return path if path.is_absolute() else home / path

    @property
    def marker(self) -> str:
        return "/" + self.relative_path.replace("\\", "/").strip("/") + "/"


DEFAULT_SKILL_ROOTS: tuple[SkillRoot, ...] = (
    SkillRoot("agents", "Shared / agents", ".agents/skills", "shared"),
    SkillRoot("codex", "Codex", ".codex/skills"),
    SkillRoot("pi", "Pi", ".pi/skills"),
    SkillRoot("claude", "Claude Code", ".claude/skills"),
    SkillRoot("zcode", "ZCode", ".zcode/skills"),
)


def validate_root_id(root_id: str) -> str:
    value = str(root_id or "").strip()
    if not _ROOT_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid Skill root id: {root_id!r}")
    return value


def merge_skill_roots(
    roots: Iterable[SkillRoot],
    *,
    home: Path,
    bundle_workspace: Optional[Path] = None,
) -> tuple[SkillRoot, ...]:
    merged: dict[str, SkillRoot] = {root.root_id: root for root in DEFAULT_SKILL_ROOTS}
    for root in roots:
        validate_root_id(root.root_id)
        if root.kind not in {"shared", "agent"}:
            raise ValueError(f"Invalid Skill root kind: {root.kind!r}")
        resolved = root.resolve(home)
        if bundle_workspace is not None:
            try:
                resolved.resolve().relative_to(bundle_workspace.resolve())
            except ValueError:
                pass
            else:
                raise ValueError(f"Skill root must not be inside Bundle workspace: {resolved}")
        merged[root.root_id] = root
    return tuple(root for root in merged.values() if root.enabled)


def root_by_id(roots: Iterable[SkillRoot], root_id: str) -> Optional[SkillRoot]:
    return next((root for root in roots if root.root_id == root_id), None)


def default_skill_roots(home: Path, bundle_workspace: Optional[Path] = None) -> tuple[SkillRoot, ...]:
    return merge_skill_roots((), home=home, bundle_workspace=bundle_workspace)


def parse_skill_roots_config(
    config_path: Path,
    *,
    home: Path,
    bundle_workspace: Optional[Path] = None,
) -> tuple[SkillRoot, ...]:
    if not config_path.is_file():
        return default_skill_roots(home, bundle_workspace)
    try:
        import tomllib
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid Skill root config: {config_path}: {exc}") from exc
    configured = data.get("skill_roots", [])
    if not isinstance(configured, list):
        raise ValueError("skill_roots must be an array of tables")
    roots: list[SkillRoot] = []
    for raw in configured:
        if not isinstance(raw, dict):
            raise ValueError("Each skill_roots entry must be a table")
        roots.append(SkillRoot(
            root_id=validate_root_id(raw.get("id", "")),
            label=str(raw.get("label") or raw.get("id") or ""),
            relative_path=str(raw.get("path") or ""),
            kind=str(raw.get("kind") or "agent"),
            enabled=bool(raw.get("enabled", True)),
            builtin=False,
        ))
    return merge_skill_roots(roots, home=home, bundle_workspace=bundle_workspace)
