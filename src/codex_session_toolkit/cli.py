"""Primary CLI entrypoint for the packaged Codex Session Toolkit."""

from __future__ import annotations

import argparse
import platform
import sys
from typing import Optional, Sequence

from . import APP_COMMAND, APP_DISPLAY_NAME, __version__
from .command_catalog import CLI_SUBCOMMANDS, COMMAND_CATALOG, COMMAND_DOMAIN_LABELS, command_domains, commands_for_domain
from .commands import run_cli as run_toolkit_cli
from .errors import ToolkitError
from .paths import CodexPaths
from .services.provider import detect_provider
from .tui.app import run_tui
from .tui.maintenance_modes import run_cleanup_mode, run_clone_mode
from .tui.terminal import (
    Ansi,
    horizontal_rule as _hr,
    style_text as _style,
)
from .tui.terminal_io import configure_text_streams as _configure_text_streams
from .tui.terminal_io import is_interactive_terminal as _is_interactive
from .tui.view_models import ToolkitAppContext

# Configuration
DEFAULT_MODEL_PROVIDER = "cliproxyapi"


def resolve_target_model_provider(paths: Optional[CodexPaths] = None) -> str:
    paths = paths or CodexPaths()
    try:
        return detect_provider(paths)
    except ToolkitError:
        return DEFAULT_MODEL_PROVIDER


def build_app_context(paths: Optional[CodexPaths] = None) -> ToolkitAppContext:
    paths = paths or CodexPaths()
    return ToolkitAppContext(
        target_provider=resolve_target_model_provider(paths),
        active_sessions_dir=str(paths.sessions_dir),
        config_path=str(paths.config_file),
        bundle_root_label=str(paths.default_bundle_root),
        desktop_bundle_root_label=str(paths.default_desktop_bundle_root),
    )


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description=f"{APP_DISPLAY_NAME}: clone, transfer, import and repair Codex sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_canonical_commands_help(),
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    parser.add_argument("--clean", action="store_true", help="Remove unmarked clones from previous runs")
    parser.add_argument("--no-tui", action="store_true", help="Force CLI mode even in interactive terminal")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _canonical_commands_help() -> str:
    command_width = max(len(spec.name) for spec in COMMAND_CATALOG)
    lines = ["Canonical toolkit commands:"]
    for domain in command_domains():
        domain_commands = commands_for_domain(domain)
        if not domain_commands:
            continue
        lines.append(f"  {COMMAND_DOMAIN_LABELS[domain]}:")
        for spec in domain_commands:
            lines.append(f"    {spec.name:<{command_width}}  {spec.summary}")
    lines.extend([
        "",
        "Legacy flags still work:",
        "  --dry-run             Preview clone mode",
        "  --clean               Cleanup legacy clone files",
    ])
    return "\n".join(lines)


def print_header(context: ToolkitAppContext, dry_run: bool) -> None:
    title = _style(f"{APP_DISPLAY_NAME} (Clone Mode)", Ansi.BOLD, Ansi.CYAN)
    print(_hr("="))
    print(title)
    print(_hr("="))
    print(f"OS:            {platform.system()} ({sys.platform})")
    print(f"Python:        {sys.version.split()[0]}")
    print(f"TargetProvider:{context.target_provider}")
    print(f"SessionsDir:   {context.active_sessions_dir}")
    print(f"ConfigFile:    {context.config_path}")
    if dry_run:
        print(_style("DRY-RUN MODE (no write / no delete)", Ansi.BOLD, Ansi.YELLOW))
    print(_hr())


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_text_streams()
    if argv is None:
        argv = sys.argv[1:]

    paths = CodexPaths()
    context = build_app_context(paths)

    if argv and argv[0] in CLI_SUBCOMMANDS:
        try:
            return run_toolkit_cli(argv, paths=paths)
        except ToolkitError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    parser = create_arg_parser()
    if not argv and _is_interactive():
        try:
            return run_tui(context)
        except KeyboardInterrupt:
            return 130

    args = parser.parse_args(argv)
    print_header(context, dry_run=bool(args.dry_run))

    if args.clean:
        return run_cleanup_mode(
            target_provider=context.target_provider,
            dry_run=bool(args.dry_run),
            delete_warning="WARNING: --clean will DELETE files.",
        )
    return run_clone_mode(target_provider=context.target_provider, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
