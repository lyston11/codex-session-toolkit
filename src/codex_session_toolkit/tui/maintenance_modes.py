"""TUI/legacy maintenance execution wrappers."""

from __future__ import annotations

import sys
from typing import Optional

from ..errors import ToolkitError
from ..paths import CodexPaths
from ..presenters.reports import print_cleanup_result, print_clone_run_result
from ..services.clone import cleanup_clones, clone_to_provider
from .terminal import Ansi, style_text


def run_clone_mode(*, target_provider: str, dry_run: bool) -> int:
    try:
        return print_clone_run_result(clone_to_provider(CodexPaths(), target_provider=target_provider, dry_run=dry_run))
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def run_cleanup_mode(
    *,
    target_provider: str,
    dry_run: bool,
    delete_warning: Optional[str] = None,
) -> int:
    if delete_warning and not dry_run:
        print(style_text(delete_warning, Ansi.BOLD, Ansi.YELLOW))
    try:
        return print_cleanup_result(cleanup_clones(CodexPaths(), target_provider=target_provider, dry_run=dry_run))
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
