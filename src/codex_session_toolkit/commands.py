"""Canonical CLI command dispatcher."""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from .application.command_handlers import COMMAND_HANDLERS
from .command_parser import create_parser
from .errors import ToolkitError
from .paths import CodexPaths


def run_cli(argv: Sequence[str], *, paths: Optional[CodexPaths] = None) -> int:
    paths = paths or CodexPaths()
    parser = create_parser()
    args = parser.parse_args(list(argv))

    handler = COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        return handler(args, paths)

    raise ToolkitError(f"Unknown command: {args.command}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    try:
        return run_cli(argv)
    except ToolkitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
