"""Canonical CLI command catalog.

This module is the single source of truth for command names, domains, and
top-level help copy. Parser construction and command dispatch stay separate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    domain: str
    help: str
    summary: str


COMMAND_CATALOG: tuple[CommandSpec, ...] = (
    CommandSpec("list", "session", "List local sessions", "Browse local sessions"),
    CommandSpec("list-project-sessions", "session", "List sessions under a project path", "Browse sessions under one project path"),
    CommandSpec("list-bundles", "bundle", "List available bundle exports", "Browse exported bundle folders"),
    CommandSpec("validate-bundles", "bundle", "Validate exported bundle directories", "Validate bundle folder health"),
    CommandSpec("export", "bundle", "Export selected or all session bundles", "Export selected or all session bundles"),
    CommandSpec("export-project", "bundle", "Export all sessions under one project path", "Batch export all sessions under one project path"),
    CommandSpec("export-desktop-all", "bundle", "Export all Desktop sessions in bulk", "Batch export all Desktop sessions"),
    CommandSpec("export-active-desktop-all", "bundle", "Export all active Desktop sessions in bulk", "Batch export all active Desktop sessions"),
    CommandSpec("export-cli-all", "bundle", "Export all CLI sessions in bulk", "Batch export all CLI sessions"),
    CommandSpec("import", "bundle", "Import selected session bundles", "Import selected bundles"),
    CommandSpec("import-desktop-all", "bundle", "Import one machine/category/project bundle folder in bulk", "Batch import one machine/category or project folder"),
    CommandSpec("list-skills", "skills", "List local Skills", "Browse local Skills"),
    CommandSpec("export-skills", "skills", "Export selected or all standalone Skills bundle", "Export selected or all standalone Skills bundle"),
    CommandSpec("list-skill-bundles", "skills", "List standalone Skills bundles", "Browse standalone Skills bundles"),
    CommandSpec("import-skill-bundle", "skills", "Import selected standalone Skills bundles", "Import selected standalone Skills bundles"),
    CommandSpec("import-skill-bundles", "skills", "Import all standalone Skills bundles", "Import all standalone Skills bundles"),
    CommandSpec("delete-skill", "skills", "Delete local custom Skills", "Delete local custom Skills"),
    CommandSpec("connect-github", "github", "Connect local bundles to a dedicated GitHub repository", "Connect bundles to a dedicated repository, optionally push"),
    CommandSpec("github-proxy", "github", "Connect or disconnect the GitHub sync proxy", "Configure proxy for GitHub sync"),
    CommandSpec("pull-github", "github", "Pull remote bundle updates from the connected GitHub repository", "Pull remote bundle updates into local codex_bundles"),
    CommandSpec("sync-github", "github", "Push local bundles to the connected GitHub repository", "Commit, merge remote updates, and push local bundles"),
    CommandSpec("clone-provider", "repair", "Clone active sessions to the target provider", "Clone active sessions to the current provider"),
    CommandSpec("repair-desktop", "repair", "Repair Desktop sidebar visibility", "Repair active Desktop visibility/index/provider"),
    CommandSpec("list-backups", "repair", "List session rollout backups", "Browse session overwrite backups"),
    CommandSpec("restore-backup", "repair", "Restore one session rollout backup", "Restore one session overwrite backup"),
    CommandSpec("delete-backup", "repair", "Delete one session rollout backup", "Delete one session overwrite backup"),
    CommandSpec("delete-archived-sessions", "repair", "Delete all archived session rollouts", "Delete archived sessions"),
    CommandSpec("clean-clones", "repair", "Delete legacy unmarked clone files", "Remove legacy unmarked clone files"),
)

COMMAND_DOMAIN_ORDER = ("session", "bundle", "skills", "repair", "github")
COMMAND_DOMAIN_LABELS = {
    "session": "Session commands",
    "bundle": "Bundle commands",
    "skills": "Skills commands",
    "github": "GitHub sync commands",
    "repair": "Repair / maintenance commands",
}
COMMAND_SPECS_BY_NAME = {spec.name: spec for spec in COMMAND_CATALOG}
CLI_SUBCOMMANDS = frozenset(COMMAND_SPECS_BY_NAME)


def command_domains() -> tuple[str, ...]:
    return COMMAND_DOMAIN_ORDER


def commands_for_domain(domain: str) -> tuple[CommandSpec, ...]:
    return tuple(spec for spec in COMMAND_CATALOG if spec.domain == domain)


def command_help(name: str) -> str:
    return COMMAND_SPECS_BY_NAME[name].help


def command_summary(name: str) -> str:
    return COMMAND_SPECS_BY_NAME[name].summary


def command_domain(name: str) -> str:
    return COMMAND_SPECS_BY_NAME[name].domain
