"""Progress rendering helpers for long-running TUI actions."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Callable, Generic, List, Optional, TypeVar

from .terminal import Ansi, align_line, app_logo_lines, render_box, style_text

if TYPE_CHECKING:
    from .app import ToolkitTuiApp

T = TypeVar("T")


@dataclass(frozen=True)
class ProgressSubprocessResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class _ThreadResult(Generic[T]):
    value: Optional[T] = None
    error: Optional[BaseException] = None


def run_callable_with_progress(
    app: "ToolkitTuiApp",
    *,
    title: str,
    detail_lines: List[str],
    task: Callable[[], T],
) -> T:
    queue: Queue[_ThreadResult[T]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            queue.put(_ThreadResult(value=task()))
        except BaseException as exc:
            queue.put(_ThreadResult(error=exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    started_at = time.monotonic()
    tick = 0
    _render_progress(app, title=title, detail_lines=detail_lines, started_at=started_at, tick=tick)
    tick += 1
    while thread.is_alive():
        _render_progress(app, title=title, detail_lines=detail_lines, started_at=started_at, tick=tick)
        tick += 1
        time.sleep(0.2)
    thread.join()
    result = queue.get()
    if result.error is not None:
        raise result.error
    return result.value  # type: ignore[return-value]


def run_cli_args_with_progress(
    app: "ToolkitTuiApp",
    *,
    title: str,
    detail_lines: List[str],
    cli_args: List[str],
) -> ProgressSubprocessResult:
    command = [sys.executable, "-m", "codex_session_toolkit", *cli_args]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    src_dir = str(Path(__file__).resolve().parents[2])
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_dir if not existing_pythonpath else f"{src_dir}{os.pathsep}{existing_pythonpath}"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        cwd=str(Path.cwd()),
        env=env,
    )

    started_at = time.monotonic()
    tick = 0
    try:
        _render_progress(app, title=title, detail_lines=detail_lines, started_at=started_at, tick=tick)
        tick += 1
        while process.poll() is None:
            _render_progress(app, title=title, detail_lines=detail_lines, started_at=started_at, tick=tick)
            tick += 1
            time.sleep(0.2)

        stdout, stderr = process.communicate()
    except KeyboardInterrupt:
        process.terminate()
        stdout, stderr = process.communicate()
        raise KeyboardInterrupt from None
    return ProgressSubprocessResult(
        return_code=int(process.returncode or 0),
        stdout=stdout,
        stderr=stderr,
    )


def _render_progress(
    app: "ToolkitTuiApp",
    *,
    title: str,
    detail_lines: List[str],
    started_at: float,
    tick: int,
) -> None:
    box_width, center = app._screen_layout()
    elapsed = max(0.0, time.monotonic() - started_at)
    bar_width = max(12, min(36, box_width - 28))
    window = max(4, min(10, bar_width // 3))
    start = tick % max(1, bar_width + window)
    cells = []
    for idx in range(bar_width):
        active = start - window <= idx <= start
        cells.append("#" if active else "-")
    spinner = "|/-\\"[tick % 4]
    lines = list(detail_lines)
    lines.append(f"{style_text('进度', Ansi.DIM)} : {spinner} [{''.join(cells)}]")
    lines.append(f"{style_text('耗时', Ansi.DIM)} : {elapsed:0.1f}s")
    lines.append(style_text("正在处理，请稍等。", Ansi.DIM))

    output_lines: list[str] = []
    for line in app_logo_lines(max_width=100):
        output_lines.append(align_line(line, box_width, center=center))
    output_lines.append(align_line(style_text("Codex 会话工具箱", Ansi.BOLD, Ansi.CYAN), box_width, center=center))
    output_lines.append(align_line(style_text(title, Ansi.DIM), box_width, center=center))
    output_lines.append("")
    for line in render_box(lines, width=box_width, border_codes=(Ansi.DIM, Ansi.YELLOW)):
        output_lines.append(line)

    hide_cursor = "\033[?25l"
    show_cursor = "\033[?25h"
    home_cursor = "\033[H"
    clear_to_eol = "\033[K"
    clear_to_eos = "\033[J"
    visible_lines = app._fit_lines_to_screen(output_lines)
    full_output = "\n".join(line + clear_to_eol for line in visible_lines) + "\n"
    sys.stdout.write(hide_cursor + home_cursor + full_output + clear_to_eos + show_cursor)
    sys.stdout.flush()
