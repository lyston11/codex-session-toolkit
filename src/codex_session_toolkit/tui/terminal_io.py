"""Terminal input and stream helpers shared by the TUI."""

from __future__ import annotations

import os
import sys
import time
from typing import Optional


def is_interactive_terminal() -> bool:
    stdin_tty = getattr(sys.stdin, "isatty", lambda: False)()
    stdout_tty = getattr(sys.stdout, "isatty", lambda: False)()
    return bool(stdin_tty and stdout_tty)


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

    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        return None

    try:
        tty.setraw(fd)
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
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass
