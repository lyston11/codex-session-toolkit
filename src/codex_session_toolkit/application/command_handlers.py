"""CLI command handlers wired to service-layer operations."""

from __future__ import annotations

import argparse
from typing import Callable, Mapping

from ..paths import CodexPaths
from ..presenters.reports import (
    print_batch_export_result,
    print_batch_import_result,
    print_bundle_rows,
    print_cleanup_result,
    print_clone_run_result,
    print_export_result,
    print_github_connect_result,
    print_github_pull_result,
    print_github_sync_result,
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
from ..services.backups import delete_session_backup, list_session_backups, restore_session_backup
from ..services.browse import get_bundle_summaries, get_project_session_summaries, get_session_summaries, validate_bundles
from ..services.clone import cleanup_clones, clone_to_provider
from ..services.exporting import export_active_desktop_all, export_cli_all, export_desktop_all, export_project_sessions, export_session
from ..services.importing import import_desktop_all, import_session
from ..services.github_sync import connect_bundles_to_github, pull_bundles_from_github, sync_bundles_to_github
from ..services.repair import repair_desktop
from ..services.skills_transfer import (
    delete_local_skill,
    export_skills,
    import_all_skill_bundles,
    import_skill_bundle,
    list_local_skills,
    list_skill_bundles,
)
from ..support import build_single_export_root


CommandHandler = Callable[[argparse.Namespace, CodexPaths], int]


def _handle_list(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_session_rows(get_session_summaries(paths, pattern=args.pattern, limit=max(1, args.limit)))


def _handle_list_project_sessions(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_session_rows(
        get_project_session_summaries(
            paths,
            project_path=args.project_path,
            pattern=args.pattern,
            limit=max(1, args.limit),
        )
    )


def _handle_list_bundles(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_bundle_rows(
        get_bundle_summaries(
            paths,
            pattern=args.pattern,
            limit=max(1, args.limit),
            source_group=args.source,
        )
    )


def _handle_validate_bundles(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_validation_report(
        validate_bundles(
            paths,
            pattern=args.pattern,
            source_group=args.source,
            limit=(None if args.limit <= 0 else args.limit),
        ),
        verbose=args.verbose,
    )


def _handle_clone_provider(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_clone_run_result(clone_to_provider(paths, target_provider=args.target_provider, dry_run=args.dry_run))


def _handle_clean_clones(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_cleanup_result(cleanup_clones(paths, target_provider=args.target_provider, dry_run=args.dry_run))


def _handle_export(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_export_result(
        export_session(
            paths,
            args.session_id,
            bundle_root=build_single_export_root(paths.default_bundle_root),
            skills_mode=args.skills_mode,
        )
    )


def _handle_export_project(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_batch_export_result(
        export_project_sessions(
            paths,
            args.project_path,
            dry_run=args.dry_run,
            active_only=args.active_only,
            skills_mode=args.skills_mode,
        )
    )


def _handle_export_desktop_all(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_batch_export_result(
        export_desktop_all(paths, dry_run=args.dry_run, active_only=args.active_only, skills_mode=args.skills_mode)
    )


def _handle_export_active_desktop_all(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_batch_export_result(export_active_desktop_all(paths, dry_run=args.dry_run, skills_mode=args.skills_mode))


def _handle_export_cli_all(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_batch_export_result(export_cli_all(paths, dry_run=args.dry_run, skills_mode=args.skills_mode))


def _handle_import(args: argparse.Namespace, paths: CodexPaths) -> int:
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


def _handle_import_desktop_all(args: argparse.Namespace, paths: CodexPaths) -> int:
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


def _handle_list_skills(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_local_skill_rows(
        list_local_skills(
            paths,
            pattern=args.pattern,
            include_system=args.include_system,
        )
    )


def _handle_export_skills(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_skill_export_result(
        export_skills(
            paths,
            pattern=args.pattern,
            include_system=args.include_system,
            skills_mode=args.skills_mode,
        )
    )


def _handle_list_skill_bundles(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_skill_bundle_rows(
        list_skill_bundles(
            paths,
            pattern=args.pattern,
        )
    )


def _handle_import_skill_bundle(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_skill_import_result(
        import_skill_bundle(
            paths,
            args.input_value,
            skills_mode=args.skills_mode,
        )
    )


def _handle_import_skill_bundles(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_skill_import_result(
        import_all_skill_bundles(
            paths,
            machine_filter=args.machine,
            skills_mode=args.skills_mode,
        )
    )


def _handle_delete_skill(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_skill_delete_result(
        delete_local_skill(
            paths,
            args.input_value,
            source_root=args.source_root,
            dry_run=args.dry_run,
        )
    )


def _handle_connect_github(args: argparse.Namespace, paths: CodexPaths) -> int:
    result = connect_bundles_to_github(
        paths,
        remote_url=args.remote_url,
        remote_name=args.remote_name,
        branch=args.branch,
        dry_run=args.dry_run,
    )
    exit_code = print_github_connect_result(result)
    if not args.push_after_connect:
        return exit_code

    if args.dry_run and result.initialized_repo:
        print("")
        print("Initial push preview skipped: bundle repo is not connected yet in dry-run mode.")
        return exit_code

    print("")
    print("Initial push after connect:")
    push_exit_code = print_github_sync_result(
        sync_bundles_to_github(
            paths,
            remote_name=args.remote_name,
            branch=args.branch,
            message=args.message,
            dry_run=args.dry_run,
            push=True,
        )
    )
    return exit_code or push_exit_code


def _handle_sync_github(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_github_sync_result(
        sync_bundles_to_github(
            paths,
            remote_name=args.remote_name,
            branch=args.branch,
            message=args.message,
            dry_run=args.dry_run,
            push=not args.no_push,
        )
    )


def _handle_pull_github(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_github_pull_result(
        pull_bundles_from_github(
            paths,
            remote_name=args.remote_name,
            branch=args.branch,
            dry_run=args.dry_run,
        )
    )


def _handle_list_backups(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_session_backup_rows(
        list_session_backups(
            paths,
            pattern=args.pattern,
            limit=max(1, args.limit),
        )
    )


def _handle_restore_backup(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_session_backup_restore_result(
        restore_session_backup(
            paths,
            args.input_value,
            dry_run=args.dry_run,
        )
    )


def _handle_delete_backup(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_session_backup_delete_result(
        delete_session_backup(
            paths,
            args.input_value,
            dry_run=args.dry_run,
        )
    )


def _handle_repair_desktop(args: argparse.Namespace, paths: CodexPaths) -> int:
    return print_repair_result(
        repair_desktop(
            paths,
            target_provider=args.target_provider,
            dry_run=args.dry_run,
            include_cli=args.include_cli,
            include_archived=args.include_archived,
        )
    )


COMMAND_HANDLERS: Mapping[str, CommandHandler] = {
    "list": _handle_list,
    "list-project-sessions": _handle_list_project_sessions,
    "list-bundles": _handle_list_bundles,
    "validate-bundles": _handle_validate_bundles,
    "clone-provider": _handle_clone_provider,
    "clean-clones": _handle_clean_clones,
    "export": _handle_export,
    "export-project": _handle_export_project,
    "export-desktop-all": _handle_export_desktop_all,
    "export-active-desktop-all": _handle_export_active_desktop_all,
    "export-cli-all": _handle_export_cli_all,
    "import": _handle_import,
    "import-desktop-all": _handle_import_desktop_all,
    "list-skills": _handle_list_skills,
    "export-skills": _handle_export_skills,
    "list-skill-bundles": _handle_list_skill_bundles,
    "import-skill-bundle": _handle_import_skill_bundle,
    "import-skill-bundles": _handle_import_skill_bundles,
    "delete-skill": _handle_delete_skill,
    "connect-github": _handle_connect_github,
    "pull-github": _handle_pull_github,
    "sync-github": _handle_sync_github,
    "list-backups": _handle_list_backups,
    "restore-backup": _handle_restore_backup,
    "delete-backup": _handle_delete_backup,
    "repair-desktop": _handle_repair_desktop,
}
