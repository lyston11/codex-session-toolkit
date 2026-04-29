"""Structured session JSONL parsing helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..errors import ToolkitError
from ..support import classify_session_kind


def normalize_session_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def looks_like_session_meta_text(text: str) -> bool:
    normalized = normalize_session_text(text)
    if not normalized:
        return True

    return normalized.lower().startswith(
        (
            "<environment_context>",
            "<permissions instructions>",
            "<app-context>",
            "<collaboration_mode>",
            "<skills_instructions>",
            "<turn_aborted>",
            "<image",
        )
    )


def first_text_fragment(value: object) -> str:
    if isinstance(value, str):
        return normalize_session_text(value)
    if isinstance(value, list):
        for item in value:
            text = first_text_fragment(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("text", "message", "content"):
            text = first_text_fragment(value.get(key))
            if text:
                return text
    return ""


def first_user_prompt_from_record(obj: dict) -> str:
    payload = obj.get("payload")
    candidate = ""
    if obj.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        candidate = first_text_fragment(payload.get("content"))
    elif obj.get("type") == "message" and isinstance(payload, dict) and payload.get("role") == "user":
        candidate = first_text_fragment(payload.get("text"))
    elif obj.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
        candidate = first_text_fragment(payload.get("message") or payload.get("text"))

    if candidate and not looks_like_session_meta_text(candidate):
        return candidate
    return ""


def parse_jsonl_records(path: Path) -> List[Tuple[str, Optional[dict]]]:
    records: List[Tuple[str, Optional[dict]]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, raw in enumerate(fh, 1):
                stripped = raw.strip()
                if not stripped:
                    records.append((raw, None))
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception as exc:
                    raise ToolkitError(f"{path} line {line_number}: {exc}") from exc
                if not isinstance(obj, dict):
                    raise ToolkitError(f"{path} line {line_number}: JSON value is not an object")
                records.append((raw, obj))
    except FileNotFoundError as exc:
        raise ToolkitError(f"Missing file: {path}") from exc
    return records


@dataclass(frozen=True)
class ParsedSessionFile:
    path: Path
    records: List[Tuple[str, Optional[dict]]]
    session_meta: dict
    turn_context: dict
    last_timestamp: str
    first_user_prompt: str

    @property
    def session_id(self) -> str:
        value = self.session_meta.get("id")
        return value if isinstance(value, str) else ""

    @property
    def source_name(self) -> str:
        value = self.session_meta.get("source")
        return value if isinstance(value, str) else ""

    @property
    def originator_name(self) -> str:
        value = self.session_meta.get("originator")
        return value if isinstance(value, str) else ""

    @property
    def cwd(self) -> str:
        value = self.session_meta.get("cwd")
        return value if isinstance(value, str) else ""

    @property
    def model_provider(self) -> str:
        value = self.session_meta.get("model_provider")
        return value if isinstance(value, str) else ""

    @property
    def session_kind(self) -> str:
        return classify_session_kind(self.source_name, self.originator_name)


@dataclass(frozen=True)
class ParsedSessionSummary:
    path: Path
    session_meta: dict
    first_user_prompt: str

    @property
    def session_id(self) -> str:
        value = self.session_meta.get("id")
        return value if isinstance(value, str) else ""

    @property
    def source_name(self) -> str:
        value = self.session_meta.get("source")
        return value if isinstance(value, str) else ""

    @property
    def originator_name(self) -> str:
        value = self.session_meta.get("originator")
        return value if isinstance(value, str) else ""

    @property
    def cwd(self) -> str:
        value = self.session_meta.get("cwd")
        return value if isinstance(value, str) else ""

    @property
    def model_provider(self) -> str:
        value = self.session_meta.get("model_provider")
        return value if isinstance(value, str) else ""

    @property
    def session_kind(self) -> str:
        return classify_session_kind(self.source_name, self.originator_name)


def parse_session_summary_file(path: Path, *, include_first_user_prompt: bool = True) -> ParsedSessionSummary:
    session_meta: dict = {}
    first_user_prompt = ""

    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, raw in enumerate(fh, 1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception as exc:
                    raise ToolkitError(f"{path} line {line_number}: {exc}") from exc
                if not isinstance(obj, dict):
                    raise ToolkitError(f"{path} line {line_number}: JSON value is not an object")

                payload = obj.get("payload")
                if obj.get("type") == "session_meta" and isinstance(payload, dict) and not session_meta:
                    session_meta = dict(payload)
                    if not include_first_user_prompt or first_user_prompt:
                        break
                    continue

                if include_first_user_prompt and not first_user_prompt:
                    candidate = first_user_prompt_from_record(obj)
                    if candidate:
                        first_user_prompt = candidate
                        if session_meta:
                            break
    except FileNotFoundError as exc:
        raise ToolkitError(f"Missing file: {path}") from exc

    if not session_meta:
        raise ToolkitError(f"{path}: session_meta not found")

    return ParsedSessionSummary(path=path, session_meta=session_meta, first_user_prompt=first_user_prompt)


def parse_session_file(path: Path) -> ParsedSessionFile:
    records = parse_jsonl_records(path)
    session_meta: dict = {}
    turn_context: dict = {}
    last_timestamp = ""
    first_user_prompt = ""

    for _, obj in records:
        if not obj:
            continue
        timestamp = obj.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            last_timestamp = timestamp

        payload = obj.get("payload")
        if obj.get("type") == "session_meta" and isinstance(payload, dict) and not session_meta:
            session_meta = dict(payload)
            continue

        if obj.get("type") == "turn_context" and isinstance(payload, dict) and not turn_context:
            turn_context = dict(payload)
            continue

        if first_user_prompt:
            continue

        candidate = first_user_prompt_from_record(obj)
        if candidate:
            first_user_prompt = candidate

    if not session_meta:
        raise ToolkitError(f"{path}: session_meta not found")

    return ParsedSessionFile(
        path=path,
        records=records,
        session_meta=session_meta,
        turn_context=turn_context,
        last_timestamp=last_timestamp,
        first_user_prompt=first_user_prompt,
    )
