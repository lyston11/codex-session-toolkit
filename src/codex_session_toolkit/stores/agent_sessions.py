"""Cross-agent local session discovery for Claude Code, Pi, and ZCode.

Codex sessions keep their rich pipeline in :mod:`.session_files`; this module
adds lightweight file-level discovery for the other supported agent
ecosystems so the session browser can list them with the same ``g`` agent
switch used by the Skills browser.  Parsers are deliberately defensive:
session files written by other agents may change shape, so every field degrades
to an empty string instead of failing the whole listing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from ..models import SessionSummary
from .session_parser import normalize_session_text

SESSION_AGENT_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "pi": "Pi",
    "zcode": "ZCode",
}
NON_CODEX_SESSION_AGENTS: tuple[str, ...] = ("claude", "pi", "zcode")

_PREVIEW_MAX_LINES = 400
_PREVIEW_MAX_BYTES = 512 * 1024
_ZCODE_SESSION_RE = re.compile(r"^model-io-(sess_[0-9a-zA-Z-]+)\.jsonl$")


def agent_session_dirs(home: Path, agent: str) -> tuple[Path, ...]:
    home = Path(home)
    if agent == "claude":
        return (home / ".claude" / "projects",)
    if agent == "pi":
        return (home / ".pi" / "agent" / "sessions",)
    if agent == "zcode":
        return (home / ".zcode" / "cli" / "rollout",)
    raise ValueError(f"Unsupported session agent: {agent}")


def agent_session_label(agent: str) -> str:
    return SESSION_AGENT_LABELS.get(agent, agent or "全部 Agent")


def iter_agent_session_files(home: Path, agent: str) -> List[Path]:
    dirs = agent_session_dirs(home, agent)
    files: list[Path] = []
    seen: set[Path] = set()
    for root in dirs:
        if not root.is_dir():
            continue
        pattern = "model-io-*.jsonl" if agent == "zcode" else "*.jsonl"
        for path in sorted(root.rglob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            files.append(path)
    files.sort(key=lambda path: (path.stat().st_mtime_ns if _safe_stat(path) else 0, path.name), reverse=True)
    return files


def collect_agent_session_summaries(
    home: Path,
    *,
    agents: tuple[str, ...] = NON_CODEX_SESSION_AGENTS,
    pattern: str = "",
    limit: Optional[int] = None,
) -> List[SessionSummary]:
    summaries: List[SessionSummary] = []
    for agent in agents:
        for path in iter_agent_session_files(home, agent):
            summary = _agent_session_summary(path, agent)
            if pattern and pattern not in " ".join([
                summary.session_id,
                summary.kind,
                summary.scope,
                summary.thread_name,
                summary.cwd,
                summary.preview,
                str(summary.path),
            ]):
                continue
            summaries.append(summary)
            if limit is not None and len(summaries) >= max(1, limit):
                return summaries
    return summaries


def _agent_session_summary(path: Path, agent: str) -> SessionSummary:
    if agent == "claude":
        parsed = _parse_claude_session(path)
    elif agent == "pi":
        parsed = _parse_pi_session(path)
    elif agent == "zcode":
        parsed = _parse_zcode_session(path)
    else:
        raise ValueError(f"Unsupported session agent: {agent}")
    preview = build_agent_session_preview(
        parsed["first_user_text"],
        path,
        parsed["cwd"],
        parsed["timestamp"],
    )
    return SessionSummary(
        session_id=parsed["session_id"] or path.stem,
        scope="active",
        path=path,
        preview=preview,
        kind="cli",
        cwd=parsed["cwd"],
        model_provider=parsed["model_provider"],
        thread_name="",
        agent=agent,
    )


def build_agent_session_preview(
    first_user_text: str,
    session_file: Path,
    cwd: str,
    timestamp: str,
) -> str:
    text = normalize_session_text(first_user_text)
    if text:
        return text
    workspace_name = (cwd or "").rstrip("/\\").rsplit("/", 1)[-1] if cwd else ""
    if workspace_name and timestamp:
        return f"{workspace_name} · {timestamp}"
    if workspace_name:
        return f"工作区：{workspace_name}"
    if timestamp:
        return f"会话开始于 {timestamp}"
    return session_file.name


def _parse_claude_session(path: Path) -> dict:
    session_id = ""
    cwd = ""
    timestamp = ""
    first_user_text = ""
    for obj in _iter_bounded_json_lines(path):
        if not session_id:
            value = obj.get("sessionId")
            if isinstance(value, str):
                session_id = value
        if not cwd:
            value = obj.get("cwd")
            if isinstance(value, str) and value:
                cwd = value
        if not timestamp:
            value = obj.get("timestamp")
            if isinstance(value, str) and value:
                timestamp = _normalize_iso_timestamp(value)
        if not first_user_text:
            first_user_text = _claude_user_text(obj)
        if session_id and cwd and timestamp and first_user_text:
            break
    if not session_id:
        session_id = path.stem
    return {
        "session_id": session_id,
        "cwd": cwd,
        "timestamp": timestamp,
        "first_user_text": first_user_text,
        "model_provider": "",
    }


def _claude_user_text(obj: dict) -> str:
    if obj.get("type") != "user" or obj.get("isMeta"):
        return ""
    message = obj.get("message")
    if not isinstance(message, dict):
        return ""
    return _text_from_message_content(message.get("content"))


def _parse_pi_session(path: Path) -> dict:
    session_id = ""
    cwd = ""
    timestamp = ""
    first_user_text = ""
    for obj in _iter_bounded_json_lines(path):
        obj_type = obj.get("type")
        if obj_type == "session":
            if not session_id:
                value = obj.get("id")
                if isinstance(value, str):
                    session_id = value
            if not cwd:
                value = obj.get("cwd")
                if isinstance(value, str):
                    cwd = value
            if not timestamp:
                value = obj.get("timestamp")
                if isinstance(value, str):
                    timestamp = _normalize_iso_timestamp(value)
        elif obj_type == "message" and not first_user_text:
            first_user_text = _pi_user_text(obj)
        if session_id and cwd and timestamp and first_user_text:
            break
    return {
        "session_id": session_id,
        "cwd": cwd,
        "timestamp": timestamp,
        "first_user_text": first_user_text,
        "model_provider": "",
    }


def _pi_user_text(obj: dict) -> str:
    message = obj.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return ""
    return _text_from_message_content(message.get("content"))


def _parse_zcode_session(path: Path) -> dict:
    match = _ZCODE_SESSION_RE.match(path.name)
    session_id = match.group(1) if match else path.stem
    return {
        "session_id": session_id,
        "cwd": "",
        "timestamp": _mtime_label(path),
        "first_user_text": "",
        "model_provider": "",
    }


def _text_from_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    return ""


def _iter_bounded_json_lines(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            consumed = 0
            for line_index, raw in enumerate(fh):
                if line_index >= _PREVIEW_MAX_LINES or consumed >= _PREVIEW_MAX_BYTES:
                    return
                consumed += len(raw.encode("utf-8", errors="replace"))
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _normalize_iso_timestamp(value: str) -> str:
    text = value.strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return text


def _mtime_label(path: Path) -> str:
    stat = _safe_stat(path)
    if stat is None:
        return ""
    local = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def _safe_stat(path: Path):
    try:
        return path.stat()
    except OSError:
        return None
