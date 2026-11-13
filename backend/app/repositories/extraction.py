from __future__ import annotations

import json
import sqlite3
from typing import Any

SNAPSHOT_STATUSES = ("valid", "voided", "frozen", "failed", "discarded")
FAILURE_STATUSES = ("active", "cleared", "voided")


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _snapshot(r: sqlite3.Row | None, *, with_rows: bool = False) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    if with_rows:
        d["rows"] = json.loads(d.pop("rows_json"))
    else:
        d.pop("rows_json", None)
    return d


def _failure(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# --- snapshots -------------------------------------------------------------


def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    pair_id: int,
    side: str,
    status: str,
    rows: list[dict[str, Any]],
    invalid_reason: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO extraction_snapshots(
            batch_id, pair_id, side, status, rows_json, row_count,
            invalid_reason, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            pair_id,
            side,
            status,
            json.dumps(rows, ensure_ascii=False),
            len(rows),
            invalid_reason,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int, *, with_rows: bool = False) -> dict[str, Any] | None:
    return _snapshot(
        conn.execute("SELECT * FROM extraction_snapshots WHERE id=?", (snapshot_id,)).fetchone(),
        with_rows=with_rows,
    )


def latest_snapshot(conn: sqlite3.Connection, pair_id: int, side: str, *, with_rows: bool = False) -> dict[str, Any] | None:
    return _snapshot(
        conn.execute(
            "SELECT * FROM extraction_snapshots WHERE pair_id=? AND side=? ORDER BY id DESC LIMIT 1",
            (pair_id, side),
        ).fetchone(),
        with_rows=with_rows,
    )


def update_snapshot_status(
    conn: sqlite3.Connection,
    snapshot_id: int,
    *,
    status: str,
    invalid_reason: str | None = None,
    invalidated_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE extraction_snapshots
        SET status=?, invalid_reason=COALESCE(?, invalid_reason),
            invalidated_at=COALESCE(?, invalidated_at)
        WHERE id=?
        """,
        (status, invalid_reason, invalidated_at, snapshot_id),
    )


def list_snapshots(conn: sqlite3.Connection, *, pair_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM extraction_snapshots WHERE 1=1"
    args: list[Any] = []
    if pair_id is not None:
        sql += " AND pair_id=?"
        args.append(pair_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [_snapshot(r) for r in conn.execute(sql, args).fetchall()]  # type: ignore[misc]


# --- extraction runs -------------------------------------------------------


def insert_run(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    pair_id: int,
    side: str,
    status: str,
    expected_rows: int,
    extracted_rows: int,
    reason: str | None,
    injected: bool,
    snapshot_id: int | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO extraction_runs(
            batch_id, pair_id, side, status, expected_rows, extracted_rows,
            reason, injected, snapshot_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            pair_id,
            side,
            status,
            expected_rows,
            extracted_rows,
            reason,
            1 if injected else 0,
            snapshot_id,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_runs(conn: sqlite3.Connection, *, batch_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM extraction_runs WHERE 1=1"
    args: list[Any] = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        args.append(batch_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [_row(r) for r in conn.execute(sql, args).fetchall()]  # type: ignore[misc]


# --- failures --------------------------------------------------------------


def insert_failure(
    conn: sqlite3.Connection,
    *,
    pair_id: int,
    batch_id: int,
    failed_side: str,
    reason: str,
    policy: str,
    status: str,
    frozen_snapshot_id: int | None,
    failed_snapshot_id: int | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO extraction_failures(
            pair_id, batch_id, failed_side, reason, policy, status,
            frozen_snapshot_id, failed_snapshot_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            pair_id,
            batch_id,
            failed_side,
            reason,
            policy,
            status,
            frozen_snapshot_id,
            failed_snapshot_id,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def get_failure(conn: sqlite3.Connection, failure_id: int) -> dict[str, Any] | None:
    return _failure(
        conn.execute("SELECT * FROM extraction_failures WHERE id=?", (failure_id,)).fetchone()
    )


def get_active_failure(conn: sqlite3.Connection, pair_id: int) -> dict[str, Any] | None:
    return _failure(
        conn.execute(
            """
            SELECT * FROM extraction_failures
            WHERE pair_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (pair_id,),
        ).fetchone()
    )


def get_latest_failure(conn: sqlite3.Connection, pair_id: int) -> dict[str, Any] | None:
    return _failure(
        conn.execute(
            "SELECT * FROM extraction_failures WHERE pair_id=? ORDER BY id DESC LIMIT 1",
            (pair_id,),
        ).fetchone()
    )


def get_failure_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    """Failure that invalidated (or froze) a snapshot — by id or by its batch."""
    return _failure(
        conn.execute(
            """
            SELECT * FROM extraction_failures
            WHERE failed_snapshot_id=?
               OR frozen_snapshot_id=?
               OR batch_id=(
                   SELECT batch_id FROM extraction_snapshots WHERE id=?
               )
            ORDER BY id DESC LIMIT 1
            """,
            (snapshot_id, snapshot_id, snapshot_id),
        ).fetchone()
    )


def list_failures(conn: sqlite3.Connection, *, pair_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT * FROM extraction_failures WHERE 1=1"
    args: list[Any] = []
    if pair_id is not None:
        sql += " AND pair_id=?"
        args.append(pair_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [_failure(r) for r in conn.execute(sql, args).fetchall()]  # type: ignore[misc]


def update_failure_cleared(
    conn: sqlite3.Connection,
    failure_id: int,
    *,
    clear_method: str,
    cleared_at: str,
    retried_snapshot_id: int | None = None,
) -> None:
    conn.execute(
        """
        UPDATE extraction_failures
        SET status='cleared', clear_method=?, cleared_at=?,
            retried_snapshot_id=COALESCE(?, retried_snapshot_id)
        WHERE id=?
        """,
        (clear_method, cleared_at, retried_snapshot_id, failure_id),
    )


# --- alignment refusals ----------------------------------------------------


def insert_refusal(
    conn: sqlite3.Connection,
    *,
    pair_id: int,
    failure_id: int | None,
    reason: str,
    source: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO align_refusals(pair_id, failure_id, reason, source, created_at)
        VALUES (?,?,?,?,?)
        """,
        (pair_id, failure_id, reason, source, created_at),
    )
    return int(cur.lastrowid)


def list_refusals(conn: sqlite3.Connection, *, pair_id: int | None = None, failure_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT * FROM align_refusals WHERE 1=1"
    args: list[Any] = []
    if pair_id is not None:
        sql += " AND pair_id=?"
        args.append(pair_id)
    if failure_id is not None:
        sql += " AND failure_id=?"
        args.append(failure_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [_row(r) for r in conn.execute(sql, args).fetchall()]  # type: ignore[misc]


def count_refusals(conn: sqlite3.Connection, *, failure_id: int | None = None, pair_id: int | None = None) -> int:
    sql = "SELECT COUNT(*) c FROM align_refusals WHERE 1=1"
    args: list[Any] = []
    if failure_id is not None:
        sql += " AND failure_id=?"
        args.append(failure_id)
    if pair_id is not None:
        sql += " AND pair_id=?"
        args.append(pair_id)
    return int(conn.execute(sql, args).fetchone()["c"])


def count_rows(conn: sqlite3.Connection, table: str, *, pair_id: int | None = None) -> int:
    if table not in ("extraction_snapshots", "extraction_failures", "align_refusals"):
        raise ValueError(f"unsupported table: {table}")
    sql = f"SELECT COUNT(*) c FROM {table} WHERE 1=1"
    args: list[Any] = []
    if pair_id is not None:
        sql += " AND pair_id=?"
        args.append(pair_id)
    return int(conn.execute(sql, args).fetchone()["c"])
