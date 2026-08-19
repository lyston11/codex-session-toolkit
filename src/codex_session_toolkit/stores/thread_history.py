"""Per-thread export and merge support for Codex history projections."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


THREAD_HISTORY_FILENAME = "thread_history_1.sqlite"
PROJECTION_TABLES = (
    "thread_history_projection_state",
    "thread_turns",
    "thread_items",
)
MIGRATIONS_TABLE = "_sqlx_migrations"


@dataclass(frozen=True)
class ThreadHistoryExportResult:
    sidecar_path: Optional[Path]
    row_count: int = 0
    table_row_counts: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ThreadHistoryImportResult:
    target_db: Optional[Path]
    action: str
    row_count: int = 0
    table_row_counts: Dict[str, int] = field(default_factory=dict)
    skipped_tables: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _TableSnapshot:
    name: str
    create_sql: str
    columns: Tuple[str, ...]
    rows: Tuple[Tuple[object, ...], ...]


def export_thread_history(
    source_db: Optional[Path],
    bundle_dir: Path,
    thread_id: str,
    *,
    filename: str = THREAD_HISTORY_FILENAME,
) -> ThreadHistoryExportResult:
    """Write a self-contained SQLite sidecar containing only one thread."""
    if source_db is None or not source_db.is_file():
        return ThreadHistoryExportResult(sidecar_path=None)

    sidecar_path = bundle_dir / filename
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.unlink(missing_ok=True)
    source = _connect_read_only(source_db)
    try:
        source.execute("BEGIN")
        snapshots = tuple(
            snapshot
            for table in PROJECTION_TABLES
            if (snapshot := _read_thread_snapshot(source, table, thread_id)) is not None
        )
        row_counts = {snapshot.name: len(snapshot.rows) for snapshot in snapshots}
        total_rows = sum(row_counts.values())
        if not total_rows:
            return ThreadHistoryExportResult(sidecar_path=None, table_row_counts=row_counts)

        migration_snapshot = _read_table_snapshot(source, MIGRATIONS_TABLE)
        index_sql = _read_index_sql(source, {snapshot.name for snapshot in snapshots})
        destination = sqlite3.connect(str(sidecar_path))
        try:
            destination.execute("PRAGMA journal_mode=DELETE")
            if migration_snapshot is not None:
                _write_snapshot(destination, migration_snapshot)
            for snapshot in snapshots:
                _write_snapshot(destination, snapshot)
            for statement in index_sql:
                destination.execute(statement)
            destination.commit()
        finally:
            destination.close()
    except Exception:
        sidecar_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    try:
        _verify_database(sidecar_path)
    except Exception:
        sidecar_path.unlink(missing_ok=True)
        raise
    return ThreadHistoryExportResult(
        sidecar_path=sidecar_path,
        row_count=total_rows,
        table_row_counts=row_counts,
    )


def import_thread_history(
    sidecar_path: Path,
    target_db: Path,
    thread_id: str,
) -> ThreadHistoryImportResult:
    """Create or transactionally replace one thread's projection rows."""
    if not sidecar_path.is_file():
        return ThreadHistoryImportResult(target_db=None, action="missing")

    _verify_database(sidecar_path)
    source = _connect_read_only(sidecar_path)
    try:
        snapshots = tuple(
            snapshot
            for table in PROJECTION_TABLES
            if (snapshot := _read_thread_snapshot(source, table, thread_id)) is not None
        )
        _ensure_sidecar_contains_only_thread(source, thread_id)
        row_counts = {snapshot.name: len(snapshot.rows) for snapshot in snapshots}
        total_rows = sum(row_counts.values())
        if not total_rows:
            return ThreadHistoryImportResult(
                target_db=target_db if target_db.exists() else None,
                action="empty",
                table_row_counts=row_counts,
            )

        target_db.parent.mkdir(parents=True, exist_ok=True)
        if not target_db.exists():
            if not _table_exists(source, MIGRATIONS_TABLE):
                return ThreadHistoryImportResult(
                    target_db=None,
                    action="missing_migrations",
                    table_row_counts=row_counts,
                )
            _copy_database_atomically(sidecar_path, target_db)
            return ThreadHistoryImportResult(
                target_db=target_db,
                action="created",
                row_count=total_rows,
                table_row_counts=row_counts,
            )

        imported_counts, skipped_tables = _merge_snapshots(target_db, thread_id, snapshots)
        return ThreadHistoryImportResult(
            target_db=target_db,
            action="merged",
            row_count=sum(imported_counts.values()),
            table_row_counts=imported_counts,
            skipped_tables=tuple(skipped_tables),
        )
    finally:
        source.close()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _read_thread_snapshot(
    connection: sqlite3.Connection,
    table: str,
    thread_id: str,
) -> Optional[_TableSnapshot]:
    if not _table_exists(connection, table):
        return None
    columns = _table_columns(connection, table)
    if "thread_id" not in columns:
        return None
    create_sql = _table_create_sql(connection, table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    rows = tuple(
        connection.execute(
            f"SELECT {quoted_columns} FROM {_quote_identifier(table)} WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()
    )
    return _TableSnapshot(table, create_sql, columns, rows)


def _read_table_snapshot(
    connection: sqlite3.Connection,
    table: str,
) -> Optional[_TableSnapshot]:
    if not _table_exists(connection, table):
        return None
    columns = _table_columns(connection, table)
    create_sql = _table_create_sql(connection, table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    rows = tuple(
        connection.execute(
            f"SELECT {quoted_columns} FROM {_quote_identifier(table)}"
        ).fetchall()
    )
    return _TableSnapshot(table, create_sql, columns, rows)


def _write_snapshot(connection: sqlite3.Connection, snapshot: _TableSnapshot) -> None:
    connection.execute(snapshot.create_sql)
    if not snapshot.rows:
        return
    quoted_columns = ", ".join(_quote_identifier(column) for column in snapshot.columns)
    placeholders = ", ".join("?" for _ in snapshot.columns)
    connection.executemany(
        f"INSERT INTO {_quote_identifier(snapshot.name)} ({quoted_columns}) VALUES ({placeholders})",
        snapshot.rows,
    )


def _read_index_sql(connection: sqlite3.Connection, tables: set[str]) -> Tuple[str, ...]:
    if not tables:
        return ()
    placeholders = ",".join("?" for _ in tables)
    rows = connection.execute(
        f"SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name IN ({placeholders}) "
        "AND sql IS NOT NULL ORDER BY name",
        tuple(sorted(tables)),
    ).fetchall()
    return tuple(str(row[0]) for row in rows if row[0])


def _ensure_sidecar_contains_only_thread(connection: sqlite3.Connection, thread_id: str) -> None:
    for table in PROJECTION_TABLES:
        if not _table_exists(connection, table) or "thread_id" not in _table_columns(connection, table):
            continue
        row = connection.execute(
            f"SELECT thread_id FROM {_quote_identifier(table)} WHERE thread_id <> ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is not None:
            raise sqlite3.DatabaseError(
                f"Thread history sidecar contains another thread in {table}: {row[0]}"
            )


def _merge_snapshots(
    target_db: Path,
    thread_id: str,
    snapshots: Sequence[_TableSnapshot],
) -> Tuple[Dict[str, int], List[str]]:
    target = sqlite3.connect(str(target_db), timeout=5)
    imported_counts: Dict[str, int] = {}
    skipped_tables: List[str] = []
    prepared: List[Tuple[_TableSnapshot, Tuple[str, ...], Tuple[Tuple[object, ...], ...]]] = []
    try:
        for snapshot in snapshots:
            if not _table_exists(target, snapshot.name):
                skipped_tables.append(snapshot.name)
                continue
            target_info = _table_info(target, snapshot.name)
            target_columns = tuple(str(row[1]) for row in target_info)
            shared_columns = tuple(column for column in snapshot.columns if column in target_columns)
            if "thread_id" not in shared_columns:
                skipped_tables.append(snapshot.name)
                continue
            primary_key_columns = [row for row in target_info if int(row[5])]
            missing_required = set()
            for row in target_info:
                column_name = str(row[1])
                if column_name in shared_columns or row[4] is not None:
                    continue
                is_auto_integer_primary_key = (
                    len(primary_key_columns) == 1
                    and int(row[5]) == 1
                    and str(row[2]).upper() == "INTEGER"
                )
                if int(row[3]) or (int(row[5]) and not is_auto_integer_primary_key):
                    missing_required.add(column_name)
            if missing_required:
                skipped_tables.append(snapshot.name)
                continue
            source_indexes = [snapshot.columns.index(column) for column in shared_columns]
            rows = tuple(
                tuple(row[index] for index in source_indexes)
                for row in snapshot.rows
            )
            prepared.append((snapshot, shared_columns, rows))

        target.execute("BEGIN IMMEDIATE")
        for snapshot, _, _ in reversed(prepared):
            target.execute(
                f"DELETE FROM {_quote_identifier(snapshot.name)} WHERE thread_id = ?",
                (thread_id,),
            )
        for snapshot, shared_columns, rows in prepared:
            if rows:
                quoted_columns = ", ".join(_quote_identifier(column) for column in shared_columns)
                placeholders = ", ".join("?" for _ in shared_columns)
                target.executemany(
                    f"INSERT INTO {_quote_identifier(snapshot.name)} ({quoted_columns}) VALUES ({placeholders})",
                    rows,
                )
            imported_counts[snapshot.name] = len(rows)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
    return imported_counts, skipped_tables


def _copy_database_atomically(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copy2(source, temporary)
        _verify_database(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_database(path: Path) -> None:
    connection = _connect_read_only(path)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if not result or str(result[0]).lower() != "ok":
        raise sqlite3.DatabaseError(f"Invalid thread history database: {path}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _table_create_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if not row or not row[0]:
        raise sqlite3.DatabaseError(f"Missing CREATE TABLE statement for {table}")
    return str(row[0])


def _table_info(connection: sqlite3.Connection, table: str) -> List[Tuple[object, ...]]:
    return list(connection.execute(f"PRAGMA table_info({_quote_identifier(table)})"))


def _table_columns(connection: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    return tuple(str(row[1]) for row in _table_info(connection, table))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
