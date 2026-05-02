"""Argument parser construction for the canonical CLI."""

from __future__ import annotations

import argparse

from . import APP_COMMAND
from .command_catalog import command_help


SOURCE_GROUP_CHOICES = ["all", "bundle", "desktop"]
SKILLS_MODE_CHOICES = ["best-effort", "strict", "skip", "overwrite"]


def _add_optional_pattern(parser: argparse.ArgumentParser, *, help_text: str = "Optional filter substring") -> None:
    parser.add_argument("pattern", nargs="?", default="", help=help_text)


def _add_limit(parser: argparse.ArgumentParser, *, default: int = 30, help_text: str = "Maximum rows to print") -> None:
    parser.add_argument("--limit", type=int, default=default, help=help_text)


def _add_bundle_source(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    parser.add_argument(
        "--source",
        choices=SOURCE_GROUP_CHOICES,
        default="all",
        help=help_text,
    )


def _add_skills_mode(parser: argparse.ArgumentParser, *, action: str) -> None:
    parser.add_argument(
        "--skills-mode",
        choices=SKILLS_MODE_CHOICES,
        default="best-effort",
        help=f"How to handle skill {action} (default: best-effort)",
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description="Codex session clone/export/import/repair toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help=command_help("list"))
    _add_optional_pattern(list_parser)
    _add_limit(list_parser)

    list_project_parser = subparsers.add_parser("list-project-sessions", help=command_help("list-project-sessions"))
    list_project_parser.add_argument("project_path", help="Project root path used to match session cwd")
    list_project_parser.add_argument("--pattern", default="", help="Optional filter substring")
    _add_limit(list_project_parser)

    list_bundles_parser = subparsers.add_parser("list-bundles", help=command_help("list-bundles"))
    _add_optional_pattern(list_bundles_parser)
    _add_limit(list_bundles_parser)
    _add_bundle_source(list_bundles_parser, help_text="Which bundle categories to scan")

    validate_bundles_parser = subparsers.add_parser("validate-bundles", help=command_help("validate-bundles"))
    _add_optional_pattern(validate_bundles_parser)
    _add_bundle_source(validate_bundles_parser, help_text="Which bundle categories to scan")
    _add_limit(validate_bundles_parser, default=0, help_text="Optional limit for validation count (0 means no limit)")
    validate_bundles_parser.add_argument("--verbose", action="store_true", help="Print successful bundle validations too")

    clone_parser = subparsers.add_parser("clone-provider", help=command_help("clone-provider"))
    clone_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clone_parser.add_argument("--dry-run", action="store_true")

    clean_parser = subparsers.add_parser("clean-clones", help=command_help("clean-clones"))
    clean_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clean_parser.add_argument("--dry-run", action="store_true")

    export_parser = subparsers.add_parser("export", help=command_help("export"))
    export_parser.add_argument("session_id")
    _add_skills_mode(export_parser, action="export")

    export_project_parser = subparsers.add_parser("export-project", help=command_help("export-project"))
    export_project_parser.add_argument("project_path", help="Project root path used to match session cwd")
    export_project_parser.add_argument("--dry-run", action="store_true")
    export_project_parser.add_argument("--active-only", action="store_true", help="Only export active sessions")
    _add_skills_mode(export_project_parser, action="export")

    export_all_parser = subparsers.add_parser("export-desktop-all", help=command_help("export-desktop-all"))
    export_all_parser.add_argument("--dry-run", action="store_true")
    export_all_parser.add_argument("--active-only", action="store_true", help="Legacy compatibility flag")
    _add_skills_mode(export_all_parser, action="export")

    export_active_desktop_parser = subparsers.add_parser(
        "export-active-desktop-all",
        help=command_help("export-active-desktop-all"),
    )
    export_active_desktop_parser.add_argument("--dry-run", action="store_true")
    _add_skills_mode(export_active_desktop_parser, action="export")

    export_cli_parser = subparsers.add_parser("export-cli-all", help=command_help("export-cli-all"))
    export_cli_parser.add_argument("--dry-run", action="store_true")
    _add_skills_mode(export_cli_parser, action="export")

    import_parser = subparsers.add_parser("import", help=command_help("import"))
    import_parser.add_argument("input_value", help="Session id or bundle directory")
    import_parser.add_argument("--desktop-visible", action="store_true")
    _add_bundle_source(import_parser, help_text="Which bundle categories to scan when importing by session id")
    import_parser.add_argument("--machine", default="", help="Only search bundles from this machine key")
    import_parser.add_argument("--export-group", default="", help="Only search bundles from this export folder (desktop/active/cli/project/single)")
    _add_skills_mode(import_parser, action="import")

    import_all_parser = subparsers.add_parser("import-desktop-all", help=command_help("import-desktop-all"))
    import_all_parser.add_argument("--desktop-visible", action="store_true")
    import_all_parser.add_argument("--machine", default="", help="Only import bundles from this machine key")
    import_all_parser.add_argument("--export-group", default="", help="Only import bundles from this export folder (desktop/active/cli/project/single)")
    import_all_parser.add_argument("--project", default="", help="Only import one project folder under project exports")
    import_all_parser.add_argument("--target-project-path", default="", help="Remap imported project cwd values to this local project path")
    import_all_parser.add_argument("--latest-only", action="store_true", help="Only import the latest bundle per machine and session id")
    _add_skills_mode(import_all_parser, action="import")

    list_skills_parser = subparsers.add_parser("list-skills", help=command_help("list-skills"))
    _add_optional_pattern(list_skills_parser)
    list_skills_parser.add_argument("--include-system", action="store_true", help="Include system/runtime Skills")

    export_skills_parser = subparsers.add_parser("export-skills", help=command_help("export-skills"))
    _add_optional_pattern(export_skills_parser, help_text="Optional Skill name/path filter")
    export_skills_parser.add_argument("--include-system", action="store_true", help="Include system/runtime Skills in the manifest")
    _add_skills_mode(export_skills_parser, action="export")

    list_skill_bundles_parser = subparsers.add_parser("list-skill-bundles", help=command_help("list-skill-bundles"))
    _add_optional_pattern(list_skill_bundles_parser)

    import_skill_bundle_parser = subparsers.add_parser("import-skill-bundle", help=command_help("import-skill-bundle"))
    import_skill_bundle_parser.add_argument("input_value", help="Skill bundle directory or Skill name")
    _add_skills_mode(import_skill_bundle_parser, action="import")

    import_skill_bundles_parser = subparsers.add_parser("import-skill-bundles", help=command_help("import-skill-bundles"))
    import_skill_bundles_parser.add_argument("--machine", default="", help="Only import Skills bundles from this machine key or label")
    _add_skills_mode(import_skill_bundles_parser, action="import")

    delete_skill_parser = subparsers.add_parser("delete-skill", help=command_help("delete-skill"))
    delete_skill_parser.add_argument("input_value", help="Exact Skill name, relative directory, or local Skill directory")
    delete_skill_parser.add_argument("--source-root", choices=["agents", "codex"], default="", help="Limit deletion to one local Skills root")
    delete_skill_parser.add_argument("--dry-run", action="store_true", help="Preview the Skill that would be deleted")

    connect_github_parser = subparsers.add_parser("connect-github", help=command_help("connect-github"))
    connect_github_parser.add_argument("remote_url", help="Dedicated GitHub repository URL for ./codex_bundles")
    connect_github_parser.add_argument("--branch", default="main", help="Remote branch to push to")
    connect_github_parser.add_argument("--remote-name", default="origin", help="Git remote name")
    connect_github_parser.add_argument("--dry-run", action="store_true", help="Preview git connection setup without writing")
    connect_github_parser.add_argument("--push-after-connect", action="store_true", help="After connecting, commit and push local bundles")
    connect_github_parser.add_argument("--message", default="Sync Codex bundles", help="Initial push commit message")

    pull_github_parser = subparsers.add_parser("pull-github", help=command_help("pull-github"))
    pull_github_parser.add_argument("--branch", default="main", help="Remote branch to pull from")
    pull_github_parser.add_argument("--remote-name", default="origin", help="Git remote name")
    pull_github_parser.add_argument("--dry-run", action="store_true", help="Preview pull operations without writing")

    sync_github_parser = subparsers.add_parser("sync-github", help=command_help("sync-github"))
    sync_github_parser.add_argument("--branch", default="main", help="Remote branch to push to")
    sync_github_parser.add_argument("--remote-name", default="origin", help="Git remote name")
    sync_github_parser.add_argument("--message", default="Sync Codex bundles", help="Commit message")
    sync_github_parser.add_argument("--dry-run", action="store_true", help="Preview git operations without writing")
    sync_github_parser.add_argument("--no-push", action="store_true", help="Commit locally without pushing")

    list_backups_parser = subparsers.add_parser("list-backups", help=command_help("list-backups"))
    _add_optional_pattern(list_backups_parser)
    _add_limit(list_backups_parser)

    restore_backup_parser = subparsers.add_parser("restore-backup", help=command_help("restore-backup"))
    restore_backup_parser.add_argument("input_value", help="Backup path, backup filename, or session id")
    restore_backup_parser.add_argument("--dry-run", action="store_true", help="Preview the backup that would be restored")

    delete_backup_parser = subparsers.add_parser("delete-backup", help=command_help("delete-backup"))
    delete_backup_parser.add_argument("input_value", help="Backup path, backup filename, or session id")
    delete_backup_parser.add_argument("--dry-run", action="store_true", help="Preview the backup that would be deleted")

    repair_parser = subparsers.add_parser("repair-desktop", help=command_help("repair-desktop"))
    repair_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.add_argument("--include-cli", action="store_true")
    repair_parser.add_argument("--include-archived", action="store_true", help="Also repair archived session files")

    return parser
