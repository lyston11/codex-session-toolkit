"""Canonical CLI command parser and dispatcher."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import APP_COMMAND
from .errors import ToolkitError
from .paths import CodexPaths
from .presenters.reports import (
    print_batch_export_result,
    print_batch_import_result,
    print_bundle_rows,
    print_cleanup_result,
    print_clone_run_result,
    print_export_result,
    print_import_result,
    print_local_skill_rows,
    print_repair_result,
    print_session_backup_delete_result,
    print_session_backup_restore_result,
    print_session_backup_rows,
    print_session_rows,
    print_skill_bundle_rows,
    print_skill_delete_result,
    print_skill_export_result,
    print_skill_import_result,
    print_validation_report,
)
from .services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles
from .services.backups import delete_session_backup, list_session_backups, restore_session_backup
from .services.clone import cleanup_clones, clone_to_provider
from .services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_project_sessions, export_session
from .services.importing import import_desktop_all, import_session
from .services.repair import repair_desktop
from .services.skills_transfer import (
    delete_local_skill,
    export_skills,
    import_all_skill_bundles,
    import_skill_bundle,
    list_local_skills,
    list_skill_bundles,
)
from .support import build_single_export_root


SKILLS_MODE_CHOICES = ["best-effort", "strict", "skip", "overwrite"]


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description="Codex session clone/export/import/repair toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List local sessions")
    list_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")

    list_project_parser = subparsers.add_parser("list-project-sessions", help="List sessions under a project path")
    list_project_parser.add_argument("project_path", help="Project root path used to match session cwd")
    list_project_parser.add_argument("--pattern", default="", help="Optional filter substring")
    list_project_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")

    list_bundles_parser = subparsers.add_parser("list-bundles", help="List available bundle exports")
    list_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_bundles_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")
    list_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan",
    )

    validate_bundles_parser = subparsers.add_parser("validate-bundles", help="Validate exported bundle directories")
    validate_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    validate_bundles_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan",
    )
    validate_bundles_parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for validation count (0 means no limit)",
    )
    validate_bundles_parser.add_argument("--verbose", action="store_true", help="Print successful bundle validations too")

    clone_parser = subparsers.add_parser("clone-provider", help="Clone active sessions to the target provider")
    clone_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clone_parser.add_argument("--dry-run", action="store_true")

    clean_parser = subparsers.add_parser("clean-clones", help="Delete legacy unmarked clone files")
    clean_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    clean_parser.add_argument("--dry-run", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export one session bundle")
    export_parser.add_argument("session_id")
    export_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill export (default: best-effort)")

    export_project_parser = subparsers.add_parser("export-project", help="Export all sessions under one project path")
    export_project_parser.add_argument("project_path", help="Project root path used to match session cwd")
    export_project_parser.add_argument("--dry-run", action="store_true")
    export_project_parser.add_argument("--active-only", action="store_true", help="Only export active sessions")
    export_project_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill export (default: best-effort)")

    export_all_parser = subparsers.add_parser("export-desktop-all", help="Export all Desktop sessions in bulk")
    export_all_parser.add_argument("--dry-run", action="store_true")
    export_all_parser.add_argument("--active-only", action="store_true", help="Legacy compatibility flag")
    export_all_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill export (default: best-effort)")

    export_active_desktop_parser = subparsers.add_parser(
        "export-active-desktop-all",
        help="Export all active Desktop sessions in bulk",
    )
    export_active_desktop_parser.add_argument("--dry-run", action="store_true")
    export_active_desktop_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill export (default: best-effort)")

    export_cli_parser = subparsers.add_parser("export-cli-all", help="Export all CLI sessions in bulk")
    export_cli_parser.add_argument("--dry-run", action="store_true")
    export_cli_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill export (default: best-effort)")

    import_parser = subparsers.add_parser("import", help="Import one session bundle")
    import_parser.add_argument("input_value", help="Session id or bundle directory")
    import_parser.add_argument("--desktop-visible", action="store_true")
    import_parser.add_argument(
        "--source",
        choices=["all", "bundle", "desktop"],
        default="all",
        help="Which bundle categories to scan when importing by session id",
    )
    import_parser.add_argument("--machine", default="", help="Only search bundles from this machine key")
    import_parser.add_argument("--export-group", default="", help="Only search bundles from this export folder (desktop/active/cli/project/single)")
    import_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill import (default: best-effort)")

    import_all_parser = subparsers.add_parser("import-desktop-all", help="Import one machine/category/project bundle folder in bulk")
    import_all_parser.add_argument("--desktop-visible", action="store_true")
    import_all_parser.add_argument("--machine", default="", help="Only import bundles from this machine key")
    import_all_parser.add_argument("--export-group", default="", help="Only import bundles from this export folder (desktop/active/cli/project/single)")
    import_all_parser.add_argument("--project", default="", help="Only import one project folder under project exports")
    import_all_parser.add_argument("--target-project-path", default="", help="Remap imported project cwd values to this local project path")
    import_all_parser.add_argument("--latest-only", action="store_true", help="Only import the latest bundle per machine and session id")
    import_all_parser.add_argument("--skills-mode", choices=["best-effort", "strict", "skip", "overwrite"], default="best-effort", help="How to handle skill import (default: best-effort)")

    list_skills_parser = subparsers.add_parser("list-skills", help="List local Skills")
    list_skills_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_skills_parser.add_argument("--include-system", action="store_true", help="Include system/runtime Skills")

    export_skills_parser = subparsers.add_parser("export-skills", help="Export standalone Skills bundle")
    export_skills_parser.add_argument("pattern", nargs="?", default="", help="Optional Skill name/path filter")
    export_skills_parser.add_argument("--include-system", action="store_true", help="Include system/runtime Skills in the manifest")
    export_skills_parser.add_argument("--skills-mode", choices=SKILLS_MODE_CHOICES, default="best-effort", help="How to handle skill export (default: best-effort)")

    list_skill_bundles_parser = subparsers.add_parser("list-skill-bundles", help="List standalone Skills bundles")
    list_skill_bundles_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")

    import_skill_bundle_parser = subparsers.add_parser("import-skill-bundle", help="Import one standalone Skills bundle")
    import_skill_bundle_parser.add_argument("input_value", help="Skill bundle directory or Skill name")
    import_skill_bundle_parser.add_argument("--skills-mode", choices=SKILLS_MODE_CHOICES, default="best-effort", help="How to handle skill import (default: best-effort)")

    import_skill_bundles_parser = subparsers.add_parser("import-skill-bundles", help="Import all standalone Skills bundles")
    import_skill_bundles_parser.add_argument("--machine", default="", help="Only import Skills bundles from this machine key or label")
    import_skill_bundles_parser.add_argument("--skills-mode", choices=SKILLS_MODE_CHOICES, default="best-effort", help="How to handle skill import (default: best-effort)")

    delete_skill_parser = subparsers.add_parser("delete-skill", help="Delete one local custom Skill")
    delete_skill_parser.add_argument("input_value", help="Exact Skill name, relative directory, or local Skill directory")
    delete_skill_parser.add_argument("--source-root", choices=["agents", "codex"], default="", help="Limit deletion to one local Skills root")
    delete_skill_parser.add_argument("--dry-run", action="store_true", help="Preview the Skill that would be deleted")

    list_backups_parser = subparsers.add_parser("list-backups", help="List session rollout backups")
    list_backups_parser.add_argument("pattern", nargs="?", default="", help="Optional filter substring")
    list_backups_parser.add_argument("--limit", type=int, default=30, help="Maximum rows to print")

    restore_backup_parser = subparsers.add_parser("restore-backup", help="Restore one session rollout backup")
    restore_backup_parser.add_argument("input_value", help="Backup path, backup filename, or session id")
    restore_backup_parser.add_argument("--dry-run", action="store_true", help="Preview the backup that would be restored")

    delete_backup_parser = subparsers.add_parser("delete-backup", help="Delete one session rollout backup")
    delete_backup_parser.add_argument("input_value", help="Backup path, backup filename, or session id")
    delete_backup_parser.add_argument("--dry-run", action="store_true", help="Preview the backup that would be deleted")

    repair_parser = subparsers.add_parser("repair-desktop", help="Repair Desktop sidebar visibility")
    repair_parser.add_argument("target_provider", nargs="?", default="", help="Optional provider override")
    repair_parser.add_argument("--dry-run", action="store_true")
    repair_parser.add_argument("--include-cli", action="store_true")
    repair_parser.add_argument("--include-archived", action="store_true", help="Also repair archived session files")

    return parser


def run_cli(argv: Sequence[str], *, paths: Optional[CodexPaths] = None) -> int:
    paths = paths or CodexPaths()
    parser = create_parser()
    args = parser.parse_args(list(argv))

    if args.command == "list":
        return print_session_rows(get_session_summaries(paths, pattern=args.pattern, limit=max(1, args.limit)))
    if args.command == "list-project-sessions":
        return print_session_rows(
            get_project_session_summaries(
                paths,
                project_path=args.project_path,
                pattern=args.pattern,
                limit=max(1, args.limit),
            )
        )
    if args.command == "list-bundles":
        return print_bundle_rows(
            get_bundle_summaries(
                paths,
                pattern=args.pattern,
                limit=max(1, args.limit),
                source_group=args.source,
            )
        )
    if args.command == "validate-bundles":
        return print_validation_report(
            validate_bundles(
                paths,
                pattern=args.pattern,
                source_group=args.source,
                limit=(None if args.limit <= 0 else args.limit),
            ),
            verbose=args.verbose,
        )
    if args.command == "clone-provider":
        return print_clone_run_result(clone_to_provider(paths, target_provider=args.target_provider, dry_run=args.dry_run))
    if args.command == "clean-clones":
        return print_cleanup_result(cleanup_clones(paths, target_provider=args.target_provider, dry_run=args.dry_run))
    if args.command == "export":
        return print_export_result(
            export_session(
                paths,
                args.session_id,
                bundle_root=build_single_export_root(paths.default_bundle_root),
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "export-project":
        return print_batch_export_result(
            export_project_sessions(
                paths,
                args.project_path,
                dry_run=args.dry_run,
                active_only=args.active_only,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "export-desktop-all":
        return print_batch_export_result(export_desktop_all(paths, dry_run=args.dry_run, active_only=args.active_only, skills_mode=args.skills_mode))
    if args.command == "export-active-desktop-all":
        return print_batch_export_result(export_active_desktop_all(paths, dry_run=args.dry_run, skills_mode=args.skills_mode))
    if args.command == "export-cli-all":
        return print_batch_export_result(export_cli_all(paths, dry_run=args.dry_run, skills_mode=args.skills_mode))
    if args.command == "import":
        return print_import_result(
            import_session(
                paths,
                args.input_value,
                source_group=args.source,
                machine_filter=args.machine,
                export_group_filter=args.export_group,
                desktop_visible=args.desktop_visible,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "import-desktop-all":
        return print_batch_import_result(
            import_desktop_all(
                paths,
                machine_filter=args.machine,
                export_group_filter=args.export_group,
                project_filter=args.project,
                target_project_path=args.target_project_path,
                latest_only=args.latest_only,
                desktop_visible=args.desktop_visible,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "list-skills":
        return print_local_skill_rows(
            list_local_skills(
                paths,
                pattern=args.pattern,
                include_system=args.include_system,
            )
        )
    if args.command == "export-skills":
        return print_skill_export_result(
            export_skills(
                paths,
                pattern=args.pattern,
                include_system=args.include_system,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "list-skill-bundles":
        return print_skill_bundle_rows(
            list_skill_bundles(
                paths,
                pattern=args.pattern,
            )
        )
    if args.command == "import-skill-bundle":
        return print_skill_import_result(
            import_skill_bundle(
                paths,
                args.input_value,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "import-skill-bundles":
        return print_skill_import_result(
            import_all_skill_bundles(
                paths,
                machine_filter=args.machine,
                skills_mode=args.skills_mode,
            )
        )
    if args.command == "delete-skill":
        return print_skill_delete_result(
            delete_local_skill(
                paths,
                args.input_value,
                source_root=args.source_root,
                dry_run=args.dry_run,
            )
        )
    if args.command == "list-backups":
        return print_session_backup_rows(
            list_session_backups(
                paths,
                pattern=args.pattern,
                limit=max(1, args.limit),
            )
        )
    if args.command == "restore-backup":
        return print_session_backup_restore_result(
            restore_session_backup(
                paths,
                args.input_value,
                dry_run=args.dry_run,
            )
        )
    if args.command == "delete-backup":
        return print_session_backup_delete_result(
            delete_session_backup(
                paths,
                args.input_value,
                dry_run=args.dry_run,
            )
        )
    if args.command == "repair-desktop":
        return print_repair_result(
            repair_desktop(
                paths,
                target_provider=args.target_provider,
                dry_run=args.dry_run,
                include_cli=args.include_cli,
                include_archived=args.include_archived,
            )
        )

    raise ToolkitError(f"Unknown command: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        return run_cli(argv)
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
