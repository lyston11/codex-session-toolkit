"""Desktop state and SQLite helpers."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from ..support import classify_session_kind, iso_to_epoch, iso_to_epoch_ms
from .session_parser import ParsedSessionFile, parse_session_file


@dataclass(frozen=True)
class DesktopThreadRow:
    session_id: str
    rollout_path: str
    created_at: int
    updated_at: int
    created_at_ms: int
    updated_at_ms: int
    source: str
    thread_source: Optional[str]
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

    if workspace_dir not in saved:
        saved.append(workspace_dir)
    if workspace_dir not in active_saved:
        active_saved.append(workspace_dir)
    if workspace_dir not in project_order:
        project_order.append(workspace_dir)

    data["electron-saved-workspace-roots"] = saved
    data["active-workspace-roots"] = active_saved
    data["project-order"] = project_order
    return True


def ensure_sidebar_workspace_visibility(
    data: dict,
    workspace_dirs: Sequence[str] = (),
    *,
    reset_workspace_filter: bool = False,
) -> bool:
    persisted = data.setdefault("electron-persisted-atom-state", {})
    if not isinstance(persisted, dict):
        persisted = {}
        data["electron-persisted-atom-state"] = persisted

    changed = False
    raw_collapsed_sections = persisted.get("sidebar-collapsed-sections-v1")
    collapsed_sections = dict(raw_collapsed_sections) if isinstance(raw_collapsed_sections, dict) else {}
    for section in ("chats", "pinned", "threads"):
        if collapsed_sections.get(section) is not False:
            collapsed_sections[section] = False
            changed = True
    if changed or not isinstance(raw_collapsed_sections, dict):
        persisted["sidebar-collapsed-sections-v1"] = collapsed_sections

    if reset_workspace_filter and persisted.get("sidebar-workspace-filter-v2") != "all":
        persisted["sidebar-workspace-filter-v2"] = "all"
        changed = True

    collapsed_groups = persisted.get("sidebar-collapsed-groups")
    if isinstance(collapsed_groups, dict) and workspace_dirs:
        visible_roots = {_normalized_thread_path(root) for root in workspace_dirs if root}
        next_groups = {
            group: value
            for group, value in collapsed_groups.items()
            if _normalized_thread_path(str(group)) not in visible_roots
        }
        if len(next_groups) != len(collapsed_groups):
            persisted["sidebar-collapsed-groups"] = next_groups
            changed = True

    return changed


def ensure_sidebar_thread_state(
    data: dict,
    thread_workspaces: Sequence[tuple[str, str]],
    *,
    reset_workspace_filter: bool = False,
    pin_threads: bool = False,
) -> tuple[int, int]:
    pairs = _normalized_thread_workspace_pairs(thread_workspaces)
    if not pairs:
        return 0, 0

    workspace_dirs = [workspace for _, workspace in pairs if workspace]
    for workspace_dir in workspace_dirs:
        merge_workspace_root(data, workspace_dir)

    _prepend_unique_state_list(data, "active-workspace-roots", workspace_dirs)
    _prepend_unique_state_list(data, "project-order", workspace_dirs)
    ensure_sidebar_workspace_visibility(
        data,
        workspace_dirs,
        reset_workspace_filter=reset_workspace_filter,
    )

    hints = data.get("thread-workspace-root-hints")
    if not isinstance(hints, dict):
        hints = {}
    for thread_id, workspace_dir in pairs:
        if workspace_dir:
            hints[thread_id] = workspace_dir
    data["thread-workspace-root-hints"] = hints

    project_orders = data.get("sidebar-project-thread-orders")
    if not isinstance(project_orders, dict):
        project_orders = {}
    for workspace_dir, thread_ids in _group_thread_ids_by_workspace(pairs).items():
        if not workspace_dir:
            continue
        raw_order = project_orders.get(workspace_dir)
        order = dict(raw_order) if isinstance(raw_order, dict) else {}
        raw_existing = order.get("threadIds", [])
        existing_ids = [str(item).strip() for item in raw_existing] if isinstance(raw_existing, list) else []
        existing_ids = [item for item in existing_ids if item]
        desired = _dedupe_strings(thread_ids)
        desired_set = set(desired)
        order["threadIds"] = desired + [thread_id for thread_id in existing_ids if thread_id not in desired_set]
        project_orders[workspace_dir] = order
    data["sidebar-project-thread-orders"] = project_orders

    pinned_count = pin_desktop_thread_ids(data, [thread_id for thread_id, _ in pairs]) if pin_threads else 0
    return len(pairs), pinned_count


def ensure_desktop_sidebar_thread_state(
    state_file: Path,
    thread_workspaces: Sequence[tuple[str, str]],
    *,
    reset_workspace_filter: bool = False,
    pin_threads: bool = False,
) -> tuple[int, int]:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return 0, 0

    data = load_desktop_state_data(state_file)
    visible_count, pinned_count = ensure_sidebar_thread_state(
        data,
        thread_workspaces,
        reset_workspace_filter=reset_workspace_filter,
        pin_threads=pin_threads,
    )
    if visible_count:
        write_desktop_state_data(state_file, data)
    return visible_count, pinned_count


def pin_desktop_thread_ids(data: dict, thread_ids: Sequence[str]) -> int:
    desired = []
    seen = set()
    for thread_id in thread_ids:
        value = str(thread_id or "").strip()
        if not value or value in seen:
            continue
        desired.append(value)
        seen.add(value)

    if not desired:
        return 0

    raw_existing = data.get("pinned-thread-ids", [])
    existing = [str(item).strip() for item in raw_existing] if isinstance(raw_existing, list) else []
    existing = [item for item in existing if item]

    next_ids = desired + [thread_id for thread_id in existing if thread_id not in seen]
    if next_ids != raw_existing:
        data["pinned-thread-ids"] = next_ids
    return len(desired)


def ensure_desktop_thread_pins(state_file: Path, thread_ids: Sequence[str]) -> int:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return 0

    data = load_desktop_state_data(state_file)
    pinned = pin_desktop_thread_ids(data, thread_ids)
    if pinned:
        write_desktop_state_data(state_file, data)
    return pinned


def ensure_desktop_workspace_root(
    workspace_dir: str,
    state_file: Path,
    *,
    reset_workspace_filter: bool = False,
) -> bool:
    if not state_file.exists():
        print(f"Warning: Codex Desktop state file not found: {state_file}", file=sys.stderr)
        return False

    data = load_desktop_state_data(state_file)
    merge_workspace_root(data, workspace_dir)
    ensure_sidebar_workspace_visibility(
        data,
        [workspace_dir],
        reset_workspace_filter=reset_workspace_filter,
    )
    write_desktop_state_data(state_file, data)
    return True


def repair_blank_thread_sources(
    state_db: Path,
    *,
    managed_roots: Sequence[Path],
    thread_source: str = "user",
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
        if "id" not in columns or "rollout_path" not in columns or "thread_source" not in columns:
            return 0

        select_columns = ["id", "rollout_path"]
        if "source" in columns:
            select_columns.append("source")
        rows = cur.execute(
            f"select {', '.join(select_columns)} from threads where thread_source is null or thread_source = ''"
        ).fetchall()
        repairable_ids = []
        for values in rows:
            item = dict(zip(select_columns, values))
            session_id = item.get("id")
            rollout_path = item.get("rollout_path")
            source = str(item.get("source") or "").strip()
            if source.startswith("{"):
                continue
            if not isinstance(session_id, str) or not isinstance(rollout_path, str):
                continue
            if _path_is_under_any_root(rollout_path, managed_roots):
                repairable_ids.append(session_id)

        if not dry_run:
            for session_id in repairable_ids:
                cur.execute("update threads set thread_source = ? where id = ?", (thread_source, session_id))
            conn.commit()
        return len(repairable_ids)
    finally:
        conn.close()


def promote_workspace_threads_for_sidebar(
    state_db: Path,
    workspace_dirs: Sequence[str],
    *,
    managed_roots: Sequence[Path],
    dry_run: bool = False,
    base_updated_at: Optional[int] = None,
) -> list[str]:
    if not state_db or not state_db.is_file() or not workspace_dirs:
        return []

    target_roots = []
    seen_roots = set()
    for workspace_dir in workspace_dirs:
        normalized = _normalized_thread_path(workspace_dir)
        if not normalized or normalized in seen_roots:
            continue
        target_roots.append(normalized)
        seen_roots.add(normalized)
    if not target_roots:
        return []

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    try:
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return []

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        required_columns = {"id", "cwd", "rollout_path", "updated_at"}
        if not required_columns.issubset(columns):
            return []

        select_columns = ["id", "cwd", "rollout_path", "updated_at"]
        if "updated_at_ms" in columns:
            select_columns.append("updated_at_ms")
        if "archived" in columns:
            select_columns.append("archived")
        if "source" in columns:
            select_columns.append("source")

        rows = cur.execute(f"select {', '.join(select_columns)} from threads").fetchall()
        best_by_workspace: dict[str, tuple[int, str]] = {}
        for values in rows:
            item = dict(zip(select_columns, values))
            session_id = item.get("id")
            cwd = item.get("cwd")
            rollout_path = item.get("rollout_path")
            if not isinstance(session_id, str) or not isinstance(cwd, str) or not isinstance(rollout_path, str):
                continue
            normalized_cwd = _normalized_thread_path(cwd)
            if normalized_cwd not in seen_roots:
                continue
            if int(item.get("archived") or 0):
                continue
            if str(item.get("source") or "").strip().startswith("{"):
                continue
            if not _path_is_under_any_root(rollout_path, managed_roots):
                continue

            updated_at = int(item.get("updated_at") or 0)
            updated_at_ms = int(item.get("updated_at_ms") or 0) if "updated_at_ms" in item else 0
            sort_value = max(updated_at_ms, updated_at * 1000)
            current = best_by_workspace.get(normalized_cwd)
            if current is None or (sort_value, session_id) > current:
                best_by_workspace[normalized_cwd] = (sort_value, session_id)

        representative_ids = [
            best_by_workspace[root][1]
            for root in target_roots
            if root in best_by_workspace
        ]
        if not representative_ids:
            return []

        if not dry_run:
            max_updated_at = cur.execute("select max(updated_at) from threads").fetchone()[0]
            base_epoch = base_updated_at or max(int(time.time()), int(max_updated_at or 0)) + len(representative_ids)
            for index, session_id in enumerate(representative_ids):
                promoted_updated_at = base_epoch - index
                update_parts = ["updated_at = ?"]
                values: list[object] = [promoted_updated_at]
                if "updated_at_ms" in columns:
                    update_parts.append("updated_at_ms = ?")
                    values.append(promoted_updated_at * 1000)
                values.append(session_id)
                cur.execute(f"update threads set {', '.join(update_parts)} where id = ?", values)
            conn.commit()
        return representative_ids
    finally:
        conn.close()


def promote_desktop_thread_ids_for_sidebar(
    state_db: Path,
    thread_ids: Sequence[str],
    *,
    managed_roots: Sequence[Path],
    dry_run: bool = False,
    base_updated_at: Optional[int] = None,
) -> list[str]:
    if not state_db or not state_db.is_file() or not thread_ids:
        return []

    desired_ids = []
    seen_ids = set()
    for thread_id in thread_ids:
        value = str(thread_id or "").strip()
        if not value or value in seen_ids:
            continue
        desired_ids.append(value)
        seen_ids.add(value)
    if not desired_ids:
        return []

    conn = sqlite3.connect(state_db)
    cur = conn.cursor()
    try:
        row = cur.execute("select name from sqlite_master where type='table' and name='threads'").fetchone()
        if not row:
            return []

        columns = [r[1] for r in cur.execute("pragma table_info(threads)").fetchall()]
        required_columns = {"id", "rollout_path", "updated_at"}
        if not required_columns.issubset(columns):
            return []

        select_columns = ["id", "rollout_path"]
        if "archived" in columns:
            select_columns.append("archived")
        if "source" in columns:
            select_columns.append("source")

        eligible_ids = set()
        for values in cur.execute(f"select {', '.join(select_columns)} from threads").fetchall():
            item = dict(zip(select_columns, values))
            session_id = item.get("id")
            rollout_path = item.get("rollout_path")
            if not isinstance(session_id, str) or session_id not in seen_ids:
                continue
            if not isinstance(rollout_path, str) or not _path_is_under_any_root(rollout_path, managed_roots):
                continue
            if int(item.get("archived") or 0):
                continue
            if str(item.get("source") or "").strip().startswith("{"):
                continue
            eligible_ids.add(session_id)

        promoted_ids = [session_id for session_id in desired_ids if session_id in eligible_ids]
        if not promoted_ids:
            return []

        if not dry_run:
            max_updated_at = cur.execute("select max(updated_at) from threads").fetchone()[0]
            base_epoch = base_updated_at or max(int(time.time()), int(max_updated_at or 0)) + len(promoted_ids)
            for index, session_id in enumerate(promoted_ids):
                promoted_updated_at = base_epoch - index
                update_parts = ["updated_at = ?"]
                values: list[object] = [promoted_updated_at]
                if "updated_at_ms" in columns:
                    update_parts.append("updated_at_ms = ?")
                    values.append(promoted_updated_at * 1000)
                values.append(session_id)
                cur.execute(f"update threads set {', '.join(update_parts)} where id = ?", values)
            conn.commit()
        return promoted_ids
    finally:
        conn.close()


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
        created_at_ms=iso_to_epoch_ms(created_iso),
        updated_at_ms=iso_to_epoch_ms(updated_iso),
        source=source_name or ("vscode" if effective_kind == "desktop" else "cli" if effective_kind == "cli" else "unknown"),
        thread_source="user",
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
                "created_at_ms": thread_row.created_at_ms,
                "updated_at_ms": thread_row.updated_at_ms,
                "source": thread_row.source,
                "thread_source": thread_row.thread_source,
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


def delete_thread_rows_by_session_ids(
    state_db: Path,
    session_ids: set[str],
    *,
    managed_roots: Sequence[Path],
    dry_run: bool = False,
) -> int:
    if not state_db or not state_db.is_file() or not session_ids:
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

        removable_ids = []
        for session_id, rollout_path in cur.execute("select id, rollout_path from threads").fetchall():
            if not isinstance(session_id, str) or session_id not in session_ids:
                continue
            if isinstance(rollout_path, str) and _path_is_under_any_root(rollout_path, managed_roots):
                removable_ids.append(session_id)

        if not dry_run:
            for session_id in removable_ids:
                cur.execute("delete from threads where id = ?", (session_id,))
            conn.commit()
        return len(removable_ids)
    finally:
        conn.close()


def redirect_thread_rows_by_session_paths(
    state_db: Path,
    session_paths: dict[str, Path],
    *,
    managed_roots: Sequence[Path],
    dry_run: bool = False,
) -> int:
    if not state_db or not state_db.is_file() or not session_paths:
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

        updated = 0
        for session_id, active_path in session_paths.items():
            current = cur.execute("select rollout_path from threads where id = ?", (session_id,)).fetchone()
            if not current:
                continue
            current_path = current[0]
            if not isinstance(current_path, str) or not _path_is_under_any_root(current_path, managed_roots):
                continue
            if str(current_path or "") == str(active_path):
                continue
            if not dry_run:
                update_parts = ["rollout_path = ?"]
                values: list[object] = [str(active_path)]
                if "archived" in columns:
                    update_parts.append("archived = ?")
                    values.append(0)
                if "archived_at" in columns:
                    update_parts.append("archived_at = null")
                values.append(session_id)
                cur.execute(f"update threads set {', '.join(update_parts)} where id = ?", values)
            updated += 1
        if not dry_run:
            conn.commit()
        return updated
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


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _prepend_unique_state_list(data: dict, key: str, values: Sequence[str]) -> None:
    desired = _dedupe_strings(values)
    if not desired:
        return
    raw_existing = data.get(key, [])
    existing = [str(item).strip() for item in raw_existing] if isinstance(raw_existing, list) else []
    existing = [item for item in existing if item]
    desired_set = set(desired)
    data[key] = desired + [item for item in existing if item not in desired_set]


def _normalized_thread_workspace_pairs(thread_workspaces: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    pairs = []
    seen = set()
    for thread_id, workspace_dir in thread_workspaces:
        normalized_thread_id = str(thread_id or "").strip()
        normalized_workspace = _normalized_thread_path(workspace_dir)
        if not normalized_thread_id or normalized_thread_id in seen:
            continue
        pairs.append((normalized_thread_id, normalized_workspace))
        seen.add(normalized_thread_id)
    return pairs


def _group_thread_ids_by_workspace(pairs: Sequence[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for thread_id, workspace_dir in pairs:
        grouped.setdefault(workspace_dir, []).append(thread_id)
    return grouped


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
