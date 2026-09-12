"""Terminal input and stream helpers shared by the TUI."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Optional

# Held key mode keeps the tty in cbreak (echo off, per-char input, output
# processing untouched) across a whole interactive flow.  Without it, bytes
# that arrive between two read_key calls - e.g. the escape-sequence bursts a
# terminal sends for mouse-wheel scrolling in the alternate screen - land in
# a cooked window with ECHO enabled and the kernel echoes them as "^[[B".
_key_mode_depth = 0
_key_mode_saved = None


def is_interactive_terminal() -> bool:
    stdin_tty = getattr(sys.stdin, "isatty", lambda: False)()
    stdout_tty = getattr(sys.stdout, "isatty", lambda: False)()
    return bool(stdin_tty and stdout_tty)


def stdin_is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def enter_key_mode() -> bool:
    """Hold the tty in cbreak mode; returns False when stdin is not a tty."""
    global _key_mode_depth, _key_mode_saved
    if os.name == "nt":
        return False
    if _key_mode_depth:
        _key_mode_depth += 1
        return True
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        return False
    _key_mode_saved = saved
    _key_mode_depth = 1
    return True


def exit_key_mode() -> None:
    global _key_mode_depth, _key_mode_saved
    if not _key_mode_depth:
        return
    _key_mode_depth -= 1
    if _key_mode_depth:
        return
    saved = _key_mode_saved
    _key_mode_saved = None
    if saved is None:
        return
    try:
        import termios

        fd = sys.stdin.fileno()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    except Exception:
        pass


def key_mode_active() -> bool:
    return _key_mode_depth > 0


@contextmanager
def suspend_key_mode():
    """Temporarily restore cooked input while a flow blocks on ``input()``."""
    active = key_mode_active()
    if active:
        exit_key_mode()
    try:
        yield
    finally:
        if active:
            enter_key_mode()


def configure_text_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


def read_key(timeout_ms: Optional[int] = None) -> Optional[str]:
    if os.name == "nt":
        try:
            import msvcrt
        except Exception:
            return None

        if timeout_ms is not None:
            deadline = time.monotonic() + max(0, timeout_ms) / 1000
            while not msvcrt.kbhit():
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.01)

        first = msvcrt.getwch()
        if first in ("\r", "\n"):
            return "ENTER"
        if first == "\x03":
            raise KeyboardInterrupt
        if first in ("\x00", "\xe0"):
            second = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
                "I": "PAGE_UP",
                "Q": "PAGE_DOWN",
            }.get(second)
        if first == "\x1b":
            return "ESC"
        return first

    try:
        import select
        import termios
        import tty
    except Exception:
        return None

    held = key_mode_active()
    old = None
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return None
    if not held:
        try:
            old = termios.tcgetattr(fd)
        except Exception:
            return None
        tty.setraw(fd)

    try:
        return _read_key_posix(fd, select, timeout_ms)
    finally:
        if not held and old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass


def _read_key_posix(fd, select, timeout_ms: Optional[int]) -> Optional[str]:
    timeout = None if timeout_ms is None else max(0, timeout_ms) / 1000
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    ch = os.read(fd, 1)
    if not ch:
        return None
    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b"\x03":
        raise KeyboardInterrupt
    if ch == b"\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            ch2 = os.read(fd, 1)
            if ch2 in (b"[", b"O") and select.select([fd], [], [], 0.05)[0]:
                ch3 = os.read(fd, 1)
                if ch3 in (b"5", b"6") and select.select([fd], [], [], 0.05)[0]:
                    ch4 = os.read(fd, 1)
                    if ch4 == b"~":
                        return {
                            b"5": "PAGE_UP",
                            b"6": "PAGE_DOWN",
                        }.get(ch3, "ESC")
                return {
                    b"A": "UP",
                    b"B": "DOWN",
                    b"C": "RIGHT",
                    b"D": "LEFT",
                }.get(ch3, "ESC")
            return "ESC"
        return "ESC"
    try:
        return ch.decode("utf-8")
    except Exception:
        return chr(ch[0])
