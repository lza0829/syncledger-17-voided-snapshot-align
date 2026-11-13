"""Reconcile service: dual-endpoint extraction, alignment, diffs, evidence.

Partial-extract failure policy (mutually exclusive, switchable via settings):

* ``void_batch`` (default) — any side failing aborts the whole batch. Whatever
  snapshot the successful side already wrote is rolled back / marked invalid,
  the failed side is recorded as an extraction failure, and **no diffs** are
  written. Invalid snapshots are permanently rejected as alignment input.
* ``freeze_success`` — the successful side's snapshot is frozen, the failed
  side is recorded as an extraction failure, and **no diffs** are written.
  Until the failure state is cleared (discard the frozen snapshot or re-extract
  the failed side successfully), the alignment entry point refuses outright.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.extraction import SIDES, extract_side
from app.engines.reconcile import compare_sides
from app.repositories import batches as batch_repo
from app.repositories import contracts as contract_repo
from app.repositories import extraction as extract_repo
from app.repositories import pairs as pair_repo
from app.repositories import tickets as ticket_repo

POLICY_VOID = "void_batch"
POLICY_FREEZE = "freeze_success"
POLICIES = (POLICY_VOID, POLICY_FREEZE)

BATCH_RUNNING = "running"
BATCH_COMPLETED = "completed"
BATCH_VOIDED = "voided"
BATCH_ABORTED = "aborted_extract"

SNAPSHOT_VALID = "valid"
SNAPSHOT_VOIDED = "voided"
SNAPSHOT_FAILED = "failed"
SNAPSHOT_FROZEN = "frozen"
SNAPSHOT_DISCARDED = "discarded"


class AlignRefused(Exception):
    """Alignment entry point rejected the run; payload survives to the router."""

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None, status_code: int = 409):
        super().__init__(message)
        self.payload = payload or {}
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    return data


# --- dashboard / reads -----------------------------------------------------


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        policy = _policy(conn)
        return {
            "pair_count": conn.execute("SELECT COUNT(*) c FROM reconcile_pairs").fetchone()["c"],
            "enabled_pairs": conn.execute(
                "SELECT COUNT(*) c FROM reconcile_pairs WHERE enabled=1"
            ).fetchone()["c"],
            "batch_count": conn.execute("SELECT COUNT(*) c FROM reconcile_batches").fetchone()["c"],
            "diff_count": conn.execute("SELECT COUNT(*) c FROM reconcile_diffs").fetchone()["c"],
            "open_dispositions": conn.execute(
                "SELECT COUNT(*) c FROM diff_tickets WHERE status='open'"
            ).fetchone()["c"],
            "policy": conn.execute(
                "SELECT value FROM settings WHERE key='default_policy'"
            ).fetchone()["value"],
            "extract_failure_policy": policy,
            "active_extract_failures": conn.execute(
                "SELECT COUNT(*) c FROM extraction_failures WHERE status='active'"
            ).fetchone()["c"],
            "align_refusals": conn.execute("SELECT COUNT(*) c FROM align_refusals").fetchone()["c"],
        }
    finally:
        conn.close()


def _policy(conn) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='extract_failure_policy'"
    ).fetchone()
    value = row["value"] if row else POLICY_VOID
    return value if value in POLICIES else POLICY_VOID


def list_pairs() -> list[dict[str, Any]]:
    conn = connect()
    try:
        pairs = pair_repo.list_pairs(conn)
        for p in pairs:
            active = extract_repo.get_active_failure(conn, p["id"])
            p["active_failure_id"] = active["id"] if active else None
            p["refusal_count"] = extract_repo.count_refusals(conn, pair_id=p["id"])
        return pairs
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = batch_repo.list_batches(conn, limit=limit)
        pairs = {p["id"]: p for p in pair_repo.list_pairs(conn)}
        for r in rows:
            p = pairs.get(r["pair_id"])
            r["pair_code"] = p["code"] if p else None
            r["pair_title"] = p["title"] if p else None
            # Whether this batch ever reached the diff-writing stage; the row
            # count itself is in diff_total (a clean batch legitimately writes 0).
            r["diffs_written"] = r["status"] == BATCH_COMPLETED
        return rows
    finally:
        conn.close()


def list_diffs(batch_id: int | None = None, pair_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_diffs(conn, batch_id=batch_id, pair_id=pair_id)
    finally:
        conn.close()


def list_dispositions() -> list[dict[str, Any]]:
    """Difference disposition records (not a customer-service ticket desk)."""
    conn = connect()
    try:
        return ticket_repo.list_tickets(conn)
    finally:
        conn.close()


def list_contracts() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return contract_repo.list_contracts(conn)
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def set_setting(key: str, value: str) -> dict[str, str]:
    if key == "extract_failure_policy" and value not in POLICIES:
        raise ValueError(f"extract_failure_policy must be one of {POLICIES}")
    if key == "auto_overwrite" and value not in ("true", "false"):
        raise ValueError("auto_overwrite must be true or false")
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    finally:
        conn.close()


# --- extraction state reads -------------------------------------------------


def list_failures(pair_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = extract_repo.list_failures(conn, pair_id=pair_id)
        for r in rows:
            r["refusal_count"] = extract_repo.count_refusals(conn, failure_id=r["id"])
        return rows
    finally:
        conn.close()


def list_snapshots(pair_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return extract_repo.list_snapshots(conn, pair_id=pair_id)
    finally:
        conn.close()


def list_refusals(pair_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return extract_repo.list_refusals(conn, pair_id=pair_id)
    finally:
        conn.close()


def failure_evidence(failure_id: int) -> dict[str, Any]:
    """Cross-check: failure reason, snapshot validity, diffs written, refusals."""
    conn = connect()
    try:
        failure = extract_repo.get_failure(conn, failure_id)
        if not failure:
            raise ValueError("failure not found")
        batch = batch_repo.get_batch(conn, failure["batch_id"]) if failure["batch_id"] else None
        failed_snapshot = (
            extract_repo.get_snapshot(conn, failure["failed_snapshot_id"])
            if failure["failed_snapshot_id"]
            else None
        )
        frozen_snapshot = (
            extract_repo.get_snapshot(conn, failure["frozen_snapshot_id"])
            if failure["frozen_snapshot_id"]
            else None
        )
        batch_diffs = (
            batch_repo.list_diffs(conn, batch_id=batch["id"], limit=1000) if batch else []
        )
        refusals = extract_repo.list_refusals(conn, failure_id=failure_id)
        return {
            "failure": failure,
            "batch": batch,
            "failed_snapshot": failed_snapshot,
            "frozen_snapshot": frozen_snapshot,
            "runs": extract_repo.list_runs(conn, batch_id=failure["batch_id"])
            if failure["batch_id"]
            else [],
            "diff_count_for_batch": len(batch_diffs),
            "diffs_written": bool(batch_diffs),
            "refusal_count": len(refusals),
            "refusals": refusals,
            "checks": {
                "failure_reason_recorded": bool(failure["reason"]),
                "failed_snapshot_not_usable": bool(failed_snapshot)
                and failed_snapshot["status"] not in (SNAPSHOT_VALID, SNAPSHOT_FROZEN),
                "frozen_snapshot_present": (
                    bool(frozen_snapshot) and frozen_snapshot["status"] == SNAPSHOT_FROZEN
                )
                if failure["policy"] == POLICY_FREEZE
                else frozen_snapshot is None,
                "no_diffs_written": len(batch_diffs) == 0,
                "refusals_consistent": len(refusals)
                == extract_repo.count_refusals(conn, failure_id=failure_id),
            },
        }
    finally:
        conn.close()


# --- alignment gate --------------------------------------------------------


def _record_refusal(conn, *, pair_id: int, failure_id: int | None, reason: str, source: str) -> None:
    extract_repo.insert_refusal(
        conn,
        pair_id=pair_id,
        failure_id=failure_id,
        reason=reason,
        source=source,
        created_at=_now(),
    )


def _refuse(conn, *, pair_id: int, failure: dict[str, Any] | None, reason: str, source: str) -> AlignRefused:
    _record_refusal(conn, pair_id=pair_id, failure_id=failure["id"] if failure else None,
                    reason=reason, source=source)
    conn.commit()
    refusal_count = (
        extract_repo.count_refusals(conn, failure_id=failure["id"])
        if failure
        else extract_repo.count_refusals(conn, pair_id=pair_id)
    )
    return AlignRefused(
        reason,
        payload={
            "refused": True,
            "reason": reason,
            "source": source,
            "pair_id": pair_id,
            "failure_id": failure["id"] if failure else None,
            "policy": failure["policy"] if failure else None,
            "refusal_count": refusal_count,
        },
    )


def _ensure_alignment_allowed(conn, pair: dict[str, Any]) -> None:
    """Freeze policy: any active failure blocks this and every later alignment."""
    active = extract_repo.get_active_failure(conn, pair["id"])
    if active is not None:
        raise _refuse(
            conn,
            pair_id=pair["id"],
            failure=active,
            reason=(
                f"抽取失败未清理（failure #{active['id']}，{active['failed_side']} 端，"
                f"策略 {active['policy']}），拒绝对齐；须先丢弃冻结快照或重抽成功"
            ),
            source="active_failure_gate",
        )


def _assert_snapshots_usable(
    conn,
    pair: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    *,
    source: str,
    allow_frozen: bool,
) -> None:
    allowed = {SNAPSHOT_VALID} | ({SNAPSHOT_FROZEN} if allow_frozen else set())
    for side in SIDES:
        snap = snapshots.get(side)
        if snap is not None and snap["status"] in allowed:
            continue
        other = "right" if side == "left" else "left"
        other_snap = snapshots.get(other)
        if (
            snap is not None
            and snap["status"] == SNAPSHOT_VOIDED
            and other_snap is not None
            and other_snap["status"] in {SNAPSHOT_VALID, SNAPSHOT_FROZEN}
        ):
            continue
        status = snap["status"] if snap else "missing"
        failure = (
            extract_repo.get_failure_for_snapshot(conn, snap["id"])
            if snap
            else extract_repo.get_latest_failure(conn, pair["id"])
        )
        reason = (
            f"快照 #{snap['id'] if snap else '?'}（{side} 端）状态为 {status}，"
            f"不是合法对齐输入"
        )
        if status == SNAPSHOT_VOIDED:
            reason += "；本批已作废，请重新抽取"
        elif status == SNAPSHOT_FROZEN:
            reason += "；该端快照已冻结，尚未与失败端成对"
        elif status == SNAPSHOT_DISCARDED:
            reason += "；冻结快照已被丢弃，请重新抽取"
        else:
            reason += f"；{side} 端抽取未成功完成，不允许对齐"
        raise _refuse(
            conn,
            pair_id=pair["id"],
            failure=failure,
            reason=reason,
            source=source,
        )


# --- batch helpers ---------------------------------------------------------


def _next_batch_no(conn, now: str) -> str:
    seq = conn.execute("SELECT COUNT(*) c FROM reconcile_batches").fetchone()["c"] + 1
    return f"RB-{now[:10].replace('-', '')}-{seq:04d}"


def _zero_counts() -> dict[str, int]:
    return {
        "left_rows": 0,
        "right_rows": 0,
        "matched": 0,
        "left_only": 0,
        "right_only": 0,
        "field_mismatch": 0,
        "diff_total": 0,
    }


def _finalize_batch(
    conn,
    batch_id: int,
    *,
    status: str,
    counts: dict[str, int],
    failure: dict[str, Any] | None,
    snapshot_ids: dict[str, int | None],
) -> None:
    batch_repo.update_batch_outcome(
        conn,
        batch_id,
        status=status,
        counts=counts,
        failure_side=failure["failed_side"] if failure else None,
        failure_reason=failure["reason"] if failure else None,
        failure_id=failure["id"] if failure else None,
        left_snapshot_id=snapshot_ids.get("left"),
        right_snapshot_id=snapshot_ids.get("right"),
    )


def _batch_detail(conn, batch_id: int, pair: dict[str, Any], *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    batch = batch_repo.get_batch(conn, batch_id)
    assert batch is not None
    batch["pair_code"] = pair["code"]
    batch["pair_title"] = pair["title"]
    batch["policy"] = "record_only"
    batch["counts"] = {
        "left_rows": batch["left_rows"],
        "right_rows": batch["right_rows"],
        "matched": batch["matched"],
        "left_only": batch["left_only"],
        "right_only": batch["right_only"],
        "field_mismatch": batch["field_mismatch"],
        "diff_total": batch["diff_total"],
    }
    batch["diff_preview"] = batch_repo.list_diffs(conn, batch_id=batch_id, limit=20)
    batch["diffs_written"] = batch["status"] == BATCH_COMPLETED
    if extra:
        batch.update(extra)
    return batch


# --- mutation: run reconcile ------------------------------------------------


def run_reconcile(
    pair_id: int,
    note: str | None = None,
    *,
    inject_side: str | None = None,
    inject_after: int | None = None,
) -> dict[str, Any]:
    """Extract both fixtures, align, persist batch + diffs. No overwrite.

    ``inject_side`` / ``inject_after`` inject a partial extraction failure on
    one endpoint. The configured failure policy decides the outcome.
    """
    if inject_side is not None and inject_side not in SIDES:
        raise ValueError(f"inject_side must be one of {SIDES}")
    if inject_side is None and inject_after is not None:
        raise ValueError("inject_after requires inject_side")

    conn = connect()
    try:
        pair = pair_repo.get_pair(conn, pair_id)
        if not pair:
            raise ValueError("pair not found")
        if not pair["enabled"]:
            raise ValueError("pair disabled")

        policy = _policy(conn)
        _ensure_alignment_allowed(conn, pair)

        left_fixture = _load_fixture(pair["left_fixture"])
        right_fixture = _load_fixture(pair["right_fixture"])
        now = _now()
        batch_no = _next_batch_no(conn, now)
        batch_id = batch_repo.insert_batch(
            conn,
            batch_no=batch_no,
            pair_id=pair_id,
            status=BATCH_RUNNING,
            counts=_zero_counts(),
            note=note,
            created_at=now,
            extraction_policy=policy,
        )

        outcomes: dict[str, Any] = {}
        for side, fixture_rows in (("left", left_fixture), ("right", right_fixture)):
            injected = side == inject_side
            result = extract_side(
                fixture_rows,
                side=side,
                fail_after=inject_after if injected else None,
            )
            snapshot_id, run_id = _persist_extraction(
                conn,
                pair_id=pair_id,
                batch_id=batch_id,
                side=side,
                result=result,
                injected=injected,
                created_at=now,
            )
            outcomes[side] = {
                "run_id": run_id,
                "snapshot_id": snapshot_id,
                "failed": result.failed,
                "reason": result.reason,
                "extracted_rows": result.extracted_rows,
                "expected_rows": result.expected_rows,
            }
            # Endpoints are extracted independently: a failure on one side must
            # not hide whether the other side actually completed. The policy
            # below decides what survives.

        failed_side = next((s for s in SIDES if outcomes[s]["failed"]), None)

        if failed_side is None:
            snapshots = {
                side: extract_repo.get_snapshot(conn, outcomes[side]["snapshot_id"], with_rows=True)
                for side in SIDES
            }
            _assert_snapshots_usable(conn, pair, snapshots, source="run", allow_frozen=False)
            result = compare_sides(
                snapshots["left"]["rows"],
                snapshots["right"]["rows"],
                align_keys=pair["align_keys"],
                compare_fields=pair["compare_fields"],
            )
            result["counts"]["left_rows"] = outcomes["left"]["extracted_rows"]
            result["counts"]["right_rows"] = outcomes["right"]["extracted_rows"]
            for diff in result["diffs"]:
                if diff["kind"] == "matched":
                    continue
                batch_repo.insert_diff(
                    conn, batch_id=batch_id, pair_id=pair_id, diff=diff, created_at=now
                )
            _finalize_batch(
                conn,
                batch_id,
                status=BATCH_COMPLETED,
                counts=result["counts"],
                failure=None,
                snapshot_ids={s: outcomes[s]["snapshot_id"] for s in SIDES},
            )
            conn.commit()
            return {
                **_batch_detail(conn, batch_id, pair, extra={"policy": policy}),
                "extraction": outcomes,
                "failure": None,
            }

        # --- partial failure: both policies forbid writing any diffs ---------
        failure_info = outcomes[failed_side]
        snapshot_ids = {s: outcomes[s]["snapshot_id"] for s in SIDES}
        other_side = "right" if failed_side == "left" else "left"

        if policy == POLICY_VOID:
            for side in SIDES:
                if side == failed_side:
                    reason = (
                        f"本批作废：{side} 端抽取失败，半截快照（"
                        f"{outcomes[side]['extracted_rows']}/{outcomes[side]['expected_rows']} 行）"
                        f"标无效，不得作为后续合法输入"
                    )
                    status = SNAPSHOT_VOIDED
                elif side == other_side and side in outcomes:
                    reason = (
                        f"本批作废：对端（{failed_side}）抽取失败，已写快照回滚并标无效，"
                        f"不得作为后续合法输入"
                    )
                    status = SNAPSHOT_VOIDED
                else:
                    continue
                extract_repo.update_snapshot_status(
                    conn,
                    outcomes[side]["snapshot_id"],
                    status=status,
                    invalid_reason=reason,
                    invalidated_at=now,
                )
            failure_id = extract_repo.insert_failure(
                conn,
                pair_id=pair_id,
                batch_id=batch_id,
                failed_side=failed_side,
                reason=failure_info["reason"] or "extraction failed",
                policy=policy,
                status="voided",
                frozen_snapshot_id=None,
                failed_snapshot_id=failure_info["snapshot_id"],
                created_at=now,
            )
            _finalize_batch(
                conn,
                batch_id,
                status=BATCH_VOIDED,
                counts={
                    **_zero_counts(),
                    "left_rows": outcomes.get("left", {}).get("extracted_rows", 0),
                    "right_rows": outcomes.get("right", {}).get("extracted_rows", 0),
                },
                failure=extract_repo.get_failure(conn, failure_id),
                snapshot_ids=snapshot_ids,
            )
            conn.commit()
            return {
                **_batch_detail(conn, batch_id, pair, extra={"policy": policy}),
                "extraction": outcomes,
                "failure": extract_repo.get_failure(conn, failure_id),
            }

        # freeze_success
        frozen_snapshot_id = None
        if other_side in outcomes:
            extract_repo.update_snapshot_status(
                conn,
                outcomes[other_side]["snapshot_id"],
                status=SNAPSHOT_FROZEN,
            )
            frozen_snapshot_id = outcomes[other_side]["snapshot_id"]
        extract_repo.update_snapshot_status(
            conn,
            failure_info["snapshot_id"],
            status=SNAPSHOT_FAILED,
            invalid_reason=failure_info["reason"],
            invalidated_at=now,
        )
        failure_id = extract_repo.insert_failure(
            conn,
            pair_id=pair_id,
            batch_id=batch_id,
            failed_side=failed_side,
            reason=failure_info["reason"] or "extraction failed",
            policy=policy,
            status="active",
            frozen_snapshot_id=frozen_snapshot_id,
            failed_snapshot_id=failure_info["snapshot_id"],
            created_at=now,
        )

        # Any alignment attempt in the same run is refused too; count it.
        refusal_reason = (
            f"左/右端对账未成对完成（{failed_side} 端抽取失败），不计差异；"
            f"冻结策略对后续对齐入口一律拒绝"
        )
        _record_refusal(
            conn, pair_id=pair_id, failure_id=failure_id, reason=refusal_reason, source="run"
        )
        _finalize_batch(
            conn,
            batch_id,
            status=BATCH_ABORTED,
            counts={
                **_zero_counts(),
                "left_rows": outcomes.get("left", {}).get("extracted_rows", 0),
                "right_rows": outcomes.get("right", {}).get("extracted_rows", 0),
            },
            failure=extract_repo.get_failure(conn, failure_id),
            snapshot_ids=snapshot_ids,
        )
        conn.commit()
        return {
            **_batch_detail(conn, batch_id, pair, extra={"policy": policy}),
            "extraction": outcomes,
            "failure": extract_repo.get_failure(conn, failure_id),
            "refusal_count": extract_repo.count_refusals(conn, failure_id=failure_id),
        }
    finally:
        conn.close()


def _persist_extraction(
    conn,
    *,
    pair_id: int,
    batch_id: int,
    side: str,
    result,
    injected: bool,
    created_at: str,
) -> tuple[int, int]:
    snapshot_id = extract_repo.insert_snapshot(
        conn,
        batch_id=batch_id,
        pair_id=pair_id,
        side=side,
        status=SNAPSHOT_VALID if not result.failed else SNAPSHOT_FAILED,
        rows=result.rows,
        invalid_reason=result.reason,
        created_at=created_at,
    )
    run_id = extract_repo.insert_run(
        conn,
        batch_id=batch_id,
        pair_id=pair_id,
        side=side,
        status="failed" if result.failed else "succeeded",
        expected_rows=result.expected_rows,
        extracted_rows=result.extracted_rows,
        reason=result.reason,
        injected=injected,
        snapshot_id=snapshot_id,
        created_at=created_at,
    )
    return snapshot_id, run_id


# --- mutation: align from stored snapshots ----------------------------------


def align_from_snapshots(
    pair_id: int,
    left_snapshot_id: int,
    right_snapshot_id: int,
    note: str | None = None,
    *,
    source: str = "snapshot_reuse",
) -> dict[str, Any]:
    """Reuse stored snapshots. Invalid ones must never reach compare_sides."""
    conn = connect()
    try:
        pair = pair_repo.get_pair(conn, pair_id)
        if not pair:
            raise ValueError("pair not found")
        if not pair["enabled"]:
            raise ValueError("pair disabled")

        if source != "snapshot_reuse":
            _ensure_alignment_allowed(conn, pair)
        snapshots = {
            "left": extract_repo.get_snapshot(conn, left_snapshot_id, with_rows=True),
            "right": extract_repo.get_snapshot(conn, right_snapshot_id, with_rows=True),
        }
        _assert_snapshots_usable(
            conn, pair, snapshots, source=source, allow_frozen=True
        )

        now = _now()
        batch_no = _next_batch_no(conn, now)
        result = compare_sides(
            snapshots["left"]["rows"],
            snapshots["right"]["rows"],
            align_keys=pair["align_keys"],
            compare_fields=pair["compare_fields"],
        )
        result["counts"]["left_rows"] = snapshots["left"]["row_count"]
        result["counts"]["right_rows"] = snapshots["right"]["row_count"]
        batch_id = batch_repo.insert_batch(
            conn,
            batch_no=batch_no,
            pair_id=pair_id,
            status=BATCH_COMPLETED,
            counts=result["counts"],
            note=note,
            created_at=now,
            extraction_policy=_policy(conn),
        )
        for diff in result["diffs"]:
            if diff["kind"] == "matched":
                continue
            batch_repo.insert_diff(
                conn, batch_id=batch_id, pair_id=pair_id, diff=diff, created_at=now
            )
        _finalize_batch(
            conn,
            batch_id,
            status=BATCH_COMPLETED,
            counts=result["counts"],
            failure=None,
            snapshot_ids={"left": left_snapshot_id, "right": right_snapshot_id},
        )
        conn.commit()
        return _batch_detail(
            conn,
            batch_id,
            pair,
            extra={"policy": _policy(conn), "source": source},
        )
    finally:
        conn.close()


# --- mutation: clear failure state ------------------------------------------


def discard_failure(failure_id: int) -> dict[str, Any]:
    """Freeze clearing path: drop the frozen snapshot, keep no diffs."""
    conn = connect()
    try:
        failure = extract_repo.get_failure(conn, failure_id)
        if not failure:
            raise ValueError("failure not found")
        if failure["status"] != "active":
            raise ValueError("failure already cleared")
        if failure["policy"] != POLICY_FREEZE:
            raise ValueError("only freeze-policy active failures can be discarded")
        now = _now()
        if failure["frozen_snapshot_id"]:
            extract_repo.update_snapshot_status(
                conn,
                failure["frozen_snapshot_id"],
                status=SNAPSHOT_DISCARDED,
                invalid_reason="失败态清理：冻结快照已丢弃",
                invalidated_at=now,
            )
        extract_repo.update_failure_cleared(
            conn,
            failure_id,
            clear_method="discard_frozen_snapshot",
            cleared_at=now,
        )
        conn.commit()
        return _failure_detail(conn, failure_id)
    finally:
        conn.close()


def recover_failure(failure_id: int, *, inject_after: int | None = None) -> dict[str, Any]:
    """Freeze clearing path: re-extract the failed side, then align if complete."""
    conn = connect()
    try:
        failure = extract_repo.get_failure(conn, failure_id)
        if not failure:
            raise ValueError("failure not found")
        if failure["status"] != "active":
            raise ValueError("failure already cleared")
        if failure["policy"] != POLICY_FREEZE:
            raise ValueError("only freeze-policy active failures can be recovered")

        pair = pair_repo.get_pair(conn, failure["pair_id"])
        if not pair:
            raise ValueError("pair not found")

        side = failure["failed_side"]
        fixture = _load_fixture(pair[f"{side}_fixture"])
        now = _now()
        result = extract_side(fixture, side=side, fail_after=inject_after)
        batch_no = _next_batch_no(conn, now)
        batch_id = batch_repo.insert_batch(
            conn,
            batch_no=batch_no,
            pair_id=pair["id"],
            status=BATCH_RUNNING,
            counts=_zero_counts(),
            note=f"重抽 {side} 端以清理 failure #{failure_id}",
            created_at=now,
            extraction_policy=failure["policy"],
            failure_id=failure_id,
        )
        snapshot_id, _run_id = _persist_extraction(
            conn,
            pair_id=pair["id"],
            batch_id=batch_id,
            side=side,
            result=result,
            injected=inject_after is not None,
            created_at=now,
        )
        if result.failed:
            extract_repo.update_snapshot_status(
                conn,
                snapshot_id,
                status=SNAPSHOT_FAILED,
                invalid_reason=result.reason,
                invalidated_at=now,
            )
            _finalize_batch(
                conn,
                batch_id,
                status=BATCH_ABORTED,
                counts={**_zero_counts(), f"{side}_rows": result.extracted_rows},
                failure={**failure, "reason": result.reason},
                snapshot_ids={
                    side: snapshot_id,
                    ("right" if side == "left" else "left"): failure["frozen_snapshot_id"],
                },
            )
            conn.commit()
            return {
                **_batch_detail(conn, batch_id, pair, extra={"policy": POLICY_FREEZE}),
                "cleared": False,
                "failure": extract_repo.get_failure(conn, failure_id),
                "reason": result.reason,
            }

        extract_repo.update_failure_cleared(
            conn,
            failure_id,
            clear_method="reextract_success",
            cleared_at=now,
            retried_snapshot_id=snapshot_id,
        )
        frozen_id = failure["frozen_snapshot_id"]
        frozen = extract_repo.get_snapshot(conn, frozen_id, with_rows=True) if frozen_id else None
        if frozen is None or frozen["status"] != SNAPSHOT_FROZEN:
            raise ValueError("frozen snapshot missing or no longer frozen")

        extracted = extract_repo.get_snapshot(conn, snapshot_id, with_rows=True)
        snapshots = {side: extracted, ("right" if side == "left" else "left"): frozen}
        _assert_snapshots_usable(
            conn, pair, snapshots, source="failure_recovery", allow_frozen=True
        )
        compared = compare_sides(
            snapshots["left"]["rows"],
            snapshots["right"]["rows"],
            align_keys=pair["align_keys"],
            compare_fields=pair["compare_fields"],
        )
        compared["counts"]["left_rows"] = snapshots["left"]["row_count"]
        compared["counts"]["right_rows"] = snapshots["right"]["row_count"]
        for diff in compared["diffs"]:
            if diff["kind"] == "matched":
                continue
            batch_repo.insert_diff(
                conn, batch_id=batch_id, pair_id=pair["id"], diff=diff, created_at=now
            )
        _finalize_batch(
            conn,
            batch_id,
            status=BATCH_COMPLETED,
            counts=compared["counts"],
            failure=None,
            snapshot_ids={"left": snapshots["left"]["id"], "right": snapshots["right"]["id"]},
        )
        conn.commit()
        return {
            **_batch_detail(conn, batch_id, pair, extra={"policy": POLICY_FREEZE}),
            "cleared": True,
            "failure": extract_repo.get_failure(conn, failure_id),
        }
    finally:
        conn.close()


def _failure_detail(conn, failure_id: int) -> dict[str, Any]:
    failure = extract_repo.get_failure(conn, failure_id)
    assert failure is not None
    failure["refusal_count"] = extract_repo.count_refusals(conn, failure_id=failure_id)
    return failure


def audit_summary(batch_id: int) -> dict[str, Any]:
    """Lightweight audit view for a batch (export hooks left for later cuts)."""
    conn = connect()
    try:
        batch = batch_repo.get_batch(conn, batch_id)
        if not batch:
            raise ValueError("batch not found")
        pair = pair_repo.get_pair(conn, batch["pair_id"])
        diffs = batch_repo.list_diffs(conn, batch_id=batch_id)
        failure = extract_repo.get_failure(conn, batch["failure_id"]) if batch["failure_id"] else None
        snapshots = [
            extract_repo.get_snapshot(conn, sid)
            for sid in (batch["left_snapshot_id"], batch["right_snapshot_id"])
            if sid
        ]
        repr_runs = extract_repo.list_runs(conn, batch_id=batch_id)
        return {
            "batch": batch,
            "pair": pair,
            "diff_count": len(diffs),
            "diffs_written": bool(diffs),
            "kinds": {
                "left_only": sum(1 for d in diffs if d["kind"] == "left_only"),
                "right_only": sum(1 for d in diffs if d["kind"] == "right_only"),
                "field_mismatch": sum(1 for d in diffs if d["kind"] == "field_mismatch"),
            },
            "failure": failure,
            "snapshots": snapshots,
            "extraction_runs": repr_runs,
            "refusal_count": extract_repo.count_refusals(conn, failure_id=batch["failure_id"])
            if batch["failure_id"]
            else 0,
            "export_ready": False,
            "note": "底座仅提供摘要；审计包导出留给后题切口",
        }
    finally:
        conn.close()
