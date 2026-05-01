"""Desktop state and SQLite helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from ..support import classify_session_kind, iso_to_epoch
from .session_parser import ParsedSessionFile, parse_session_file


@dataclass(frozen=True)
class DesktopThreadRow:
    session_id: str
    rollout_path: str
    created_at: int
    updated_at: int
    source: str
    model_provider: str
    cwd: str
    title: str
    sandbox_policy: str
    approval_mode: str
    tokens_used: int
    has_user_event: int
    archived: int
    archived_at: Optional[int]
    cli_version: str
    first_user_message: str
    memory_mode: str
    model: Optional[str]
    reasoning_effort: Optional[str]


def load_desktop_state_data(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    with state_file.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_desktop_state_data(state_file: Path, data: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))


def merge_workspace_root(data: dict, workspace_dir: str) -> bool:
    saved = list(data.setdefault("electron-saved-workspace-roots", []))
    active_saved = list(data.setdefault("active-workspace-roots", saved))
    project_order = list(data.setdefault("project-order", []))

    covered = False
    for root in saved:
        if workspace_dir == root or workspace_dir.startswith(root.rstrip("/") + "/"):
            covered = True
            break

    if not covered:
        saved.append(workspace_dir)
        active_saved.append(workspace_dir)
        if workspace_dir not in project_order:
            project_order.append(workspace_dir)

    data["electron-saved-workspace-roots"] = saved
    data["active-workspace-roots"] = list(saved)
    data["project-order"] = project_order
    return True


def ensure_desktop_workspace_root(workspace_dir: str, state_file: Path) -> bool:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return False

    data = load_desktop_state_data(state_file)
    merge_workspace_root(data, workspace_dir)
    write_desktop_state_data(state_file, data)
    return True


def load_thread_metadata(
    state_db: Optional[Path],
    *,
    session_ids: Optional[set[str]] = None,
) -> dict[str, dict[str, str]]:
    if not state_db or not state_db.is_file():
        return {}

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    try:
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return {}

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        if "id" not in columns:
            return {}

        select_columns = ["id"]
        if "title" in columns:
            select_columns.append("title")
        if "first_user_message" in columns:
            select_columns.append("first_user_message")

        rows = cur.execute(f"select {', '.join(select_columns)} from threads").fetchall()
        metadata: dict[str, dict[str, str]] = {}
        for values in rows:
            session_id = values[0]
            if not isinstance(session_id, str) or not session_id:
                continue
            if session_ids is not None and session_id not in session_ids:
                continue
            item = dict(zip(select_columns, values))
            metadata[session_id] = {
                "title": str(item.get("title") or ""),
                "first_user_message": str(item.get("first_user_message") or ""),
            }
        return metadata
    finally:
        conn.close()


def prepare_session_for_import(
    source_session: Path,
    prepared_session: Path,
    *,
    auto_desktop_compat: bool,
    session_kind: str,
    target_desktop_model_provider: str,
    session_cwd_override: str = "",
) -> None:
    with source_session.open("r", encoding="utf-8") as in_fh, prepared_session.open("w", encoding="utf-8") as out_fh:
        for raw in in_fh:
            line = raw.rstrip("\n")
            if not line:
                out_fh.write(raw)
                continue

            try:
                obj = json.loads(line)
            except Exception:
                out_fh.write(raw)
                continue

            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                payload = dict(obj["payload"])
                if auto_desktop_compat and session_kind == "cli":
                    payload["source"] = "vscode"
                    payload["originator"] = "Codex Desktop"
                if target_desktop_model_provider:
                    payload["model_provider"] = target_desktop_model_provider
                if session_cwd_override:
                    payload["cwd"] = session_cwd_override

                obj = dict(obj)
                obj["payload"] = payload
                out_fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
                continue

            out_fh.write(raw)


def build_threads_row(
    session_file: Path,
    target_rollout: Path,
    *,
    parsed_session: Optional[ParsedSessionFile] = None,
    thread_name: str,
    updated_at: str,
    first_user_message: str = "",
    session_cwd: str = "",
    session_source: str = "",
    session_originator: str = "",
    session_kind: str = "",
    model_provider_override: str = "",
) -> DesktopThreadRow:
    parsed = parsed_session or parse_session_file(session_file)
    source_name = session_source or parsed.source_name
    originator_name = session_originator or parsed.originator_name
    effective_kind = session_kind or classify_session_kind(source_name, originator_name)
    cwd = session_cwd or parsed.cwd
    created_iso = str(parsed.session_meta.get("timestamp") or parsed.last_timestamp or updated_at)
    updated_iso = updated_at or parsed.last_timestamp or created_iso
    user_message = first_user_message or thread_name or parsed.first_user_prompt or parsed.session_id
    title = thread_name or user_message or parsed.session_id
    sandbox_policy = json.dumps(parsed.turn_context.get("sandbox_policy", {}), ensure_ascii=False, separators=(",", ":"))
    approval_mode = parsed.turn_context.get("approval_policy", "on-request")
    model_provider = model_provider_override or parsed.model_provider
    cli_version = parsed.session_meta.get("cli_version", "")
    model = parsed.turn_context.get("model")
    reasoning_effort = parsed.turn_context.get("effort")
    archived = 1 if "archived_sessions" in Path(target_rollout).parts else 0
    archived_at = iso_to_epoch(updated_iso) if archived else None

    return DesktopThreadRow(
        session_id=parsed.session_id,
        rollout_path=str(target_rollout),
        created_at=iso_to_epoch(created_iso),
        updated_at=iso_to_epoch(updated_iso),
        source=source_name or ("vscode" if effective_kind == "desktop" else "cli" if effective_kind == "cli" else "unknown"),
        model_provider=model_provider,
        cwd=cwd,
        title=title,
        sandbox_policy=sandbox_policy,
        approval_mode=approval_mode,
        tokens_used=0,
        has_user_event=1,
        archived=archived,
        archived_at=archived_at,
        cli_version=cli_version if isinstance(cli_version, str) else "",
        first_user_message=user_message or title,
        memory_mode="enabled",
        model=model,
        reasoning_effort=reasoning_effort,
    )


def upsert_threads_rows(
    state_db: Path,
    rows: Sequence[DesktopThreadRow],
    *,
    dry_run: bool = False,
) -> int:
    if not state_db or not state_db.is_file() or not rows:
        return 0

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    try:
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return 0

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        upserted = 0
        for thread_row in rows:
            data = {
                "id": thread_row.session_id,
                "rollout_path": thread_row.rollout_path,
                "created_at": thread_row.created_at,
                "updated_at": thread_row.updated_at,
                "source": thread_row.source,
                "model_provider": thread_row.model_provider,
                "cwd": thread_row.cwd,
                "title": thread_row.title,
                "sandbox_policy": thread_row.sandbox_policy,
                "approval_mode": thread_row.approval_mode,
                "tokens_used": thread_row.tokens_used,
                "has_user_event": thread_row.has_user_event,
                "archived": thread_row.archived,
                "archived_at": thread_row.archived_at,
                "cli_version": thread_row.cli_version,
                "first_user_message": thread_row.first_user_message,
                "memory_mode": thread_row.memory_mode,
                "model": thread_row.model,
                "reasoning_effort": thread_row.reasoning_effort,
            }
            insert_cols = [name for name in data if name in columns]
            placeholders = ", ".join("?" for _ in insert_cols)
            col_list = ", ".join(insert_cols)
            update_cols = [name for name in insert_cols if name != "id"]
            update_sql = ", ".join(f"{name}=excluded.{name}" for name in update_cols)
            values = [data[name] for name in insert_cols]
            sql = f"insert into threads ({col_list}) values ({placeholders}) on conflict(id) do update set {update_sql}"
            if not dry_run:
                cur.execute(sql, values)
            upserted += 1
        if not dry_run:
            conn.commit()
        return upserted
    finally:
        conn.close()


def load_thread_session_ids(state_db: Optional[Path], *, managed_roots: Sequence[Path] = ()) -> set[str]:
    if not state_db or not state_db.is_file():
        return set()

    try:
        conn = sqlite3.connect(state_db)
        cur = conn.cursor()
        try:
            row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
            if not row:
                return set()

            columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
            if "id" not in columns:
                return set()

            if "rollout_path" in columns and managed_roots:
                return {
                    session_id
                    for session_id, rollout_path in cur.execute("select id, rollout_path from threads").fetchall()
                    if isinstance(session_id, str)
                    and session_id
                    and isinstance(rollout_path, str)
                    and _path_is_under_any_root(rollout_path, managed_roots)
                }

            return {
                session_id
                for (session_id,) in cur.execute("select id from threads").fetchall()
                if isinstance(session_id, str) and session_id
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def prune_threads_rows(
    state_db: Path,
    *,
    desired_session_ids: set[str],
    managed_roots: Sequence[Path],
    dry_run: bool = False,
) -> int:
    if not state_db or not state_db.is_file():
        return 0

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    try:
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return 0

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        if "id" not in columns or "rollout_path" not in columns:
            return 0

        stale_ids = []
        for session_id, rollout_path in cur.execute("select id, rollout_path from threads").fetchall():
            if not isinstance(session_id, str) or session_id in desired_session_ids:
                continue
            if isinstance(rollout_path, str) and _path_is_under_any_root(rollout_path, managed_roots):
                stale_ids.append(session_id)

        if not dry_run:
            for session_id in stale_ids:
                cur.execute("delete from threads where id = ?", (session_id,))
            conn.commit()
        return len(stale_ids)
    finally:
        conn.close()


def _path_is_under_any_root(path_value: str, roots: Sequence[Path]) -> bool:
    normalized_path = _normalized_thread_path(path_value)
    if not normalized_path:
        return False
    for root in roots:
        normalized_root = _normalized_thread_path(str(root))
        if normalized_path == normalized_root or normalized_path.startswith(normalized_root + "/"):
            return True
    return False


def _normalized_thread_path(path_value: str) -> str:
    return str(path_value or "").replace("\\", "/").rstrip("/")


def upsert_threads_table(
    state_db: Path,
    session_file: Path,
    history_file: Path,
    target_rollout: Path,
    *,
    session_id: str,
    thread_name: str,
    updated_at: str,
    session_cwd: str,
    session_source: str,
    session_originator: str,
    session_kind: str,
    classify_session_kind,
    first_user_message: str = "",
) -> bool:
    effective_first_user_message = first_user_message or thread_name
    if not first_user_message and history_file.exists():
        with history_file.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
            if first_line:
                try:
                    effective_first_user_message = json.loads(first_line).get("text") or effective_first_user_message
                except Exception:
                    pass
    parsed_session = parse_session_file(session_file)
    row = build_threads_row(
        session_file,
        target_rollout,
        parsed_session=parsed_session,
        thread_name=thread_name,
        updated_at=updated_at,
        first_user_message=effective_first_user_message,
        session_cwd=session_cwd,
        session_source=session_source,
        session_originator=session_originator,
        session_kind=session_kind or classify_session_kind(session_source, session_originator),
    )
    return upsert_threads_rows(state_db, [row]) > 0
