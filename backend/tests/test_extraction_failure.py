from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.seed import init_db  # noqa: E402
from app.services import reconcile_service as svc  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr("app.config.DATA_DIR", tmp_path)
    init_db()
    return tmp_path


def test_void_policy_no_diffs_and_snapshot_invalidated(fresh_db):
    assert svc.list_settings()["extract_failure_policy"] == "void_batch"
    result = svc.run_reconcile(1, inject_side="right", inject_after=2)
    assert result["status"] == "voided"
    assert result["failure_side"] == "right"
    assert result["diff_total"] == 0
    assert result["diffs_written"] is False

    snapshots = svc.list_snapshots(pair_id=1)
    assert {s["status"] for s in snapshots} == {"voided"}
    failed = next(s for s in snapshots if s["side"] == "right")
    assert failed["row_count"] == 2

    evidence = svc.failure_evidence(result["failure"]["id"])
    assert evidence["checks"]["failure_reason_recorded"] is True
    assert evidence["checks"]["failed_snapshot_not_usable"] is True
    assert evidence["checks"]["no_diffs_written"] is True
    assert evidence["diffs_written"] is False


def test_void_policy_invalid_snapshot_rejected_as_input(fresh_db):
    svc.run_reconcile(1, inject_side="right", inject_after=1)
    snapshots = svc.list_snapshots(pair_id=1)
    left = next(s for s in snapshots if s["side"] == "left")
    right = next(s for s in snapshots if s["side"] == "right")
    with pytest.raises(svc.AlignRefused) as exc:
        svc.align_from_snapshots(1, left["id"], right["id"])
    assert exc.value.payload["refused"] is True
    assert exc.value.payload["refusal_count"] == 1


def test_void_policy_later_batch_completes_after_abort(fresh_db):
    svc.run_reconcile(1, inject_side="left", inject_after=1)
    later = svc.run_reconcile(1, note="after abort")
    assert later["status"] == "completed"
    assert later["diffs_written"] is True


def test_freeze_policy_keeps_good_side_and_blocks_alignment(fresh_db):
    svc.set_setting("extract_failure_policy", "freeze_success")
    result = svc.run_reconcile(2, inject_side="left", inject_after=1)
    assert result["status"] == "aborted_extract"
    assert result["diffs_written"] is False

    snapshots = {s["side"]: s for s in svc.list_snapshots(pair_id=2)}
    assert snapshots["right"]["status"] == "frozen"
    assert snapshots["left"]["status"] == "failed"
    assert result["failure"]["frozen_snapshot_id"] == snapshots["right"]["id"]

    with pytest.raises(svc.AlignRefused):
        svc.run_reconcile(2)
    snapshots_list = svc.list_snapshots(pair_id=2)
    with pytest.raises(svc.AlignRefused):
        svc.align_from_snapshots(2, snapshots_list[0]["id"], snapshots_list[1]["id"])

    evidence = svc.failure_evidence(result["failure"]["id"])
    assert evidence["refusal_count"] >= 1
    assert evidence["checks"]["no_diffs_written"] is True


def test_freeze_policy_clear_by_reextract_then_align(fresh_db):
    svc.set_setting("extract_failure_policy", "freeze_success")
    first = svc.run_reconcile(2, inject_side="right", inject_after=1)
    recovered = svc.recover_failure(first["failure"]["id"])
    assert recovered["cleared"] is True
    assert recovered["status"] == "completed"
    assert recovered["diffs_written"] is True
    assert svc.run_reconcile(2)["status"] == "completed"


def test_freeze_policy_clear_by_discard_then_align(fresh_db):
    svc.set_setting("extract_failure_policy", "freeze_success")
    first = svc.run_reconcile(3, inject_side="left", inject_after=2)
    cleared = svc.discard_failure(first["failure"]["id"])
    assert cleared["status"] == "cleared"
    assert cleared["clear_method"] == "discard_frozen_snapshot"
    frozen = svc.list_snapshots(pair_id=3)
    assert any(s["side"] == "right" and s["status"] == "discarded" for s in frozen)
    assert svc.run_reconcile(3)["status"] == "completed"


def test_frozen_policy_failed_retry_keeps_gate_active(fresh_db):
    svc.set_setting("extract_failure_policy", "freeze_success")
    first = svc.run_reconcile(2, inject_side="right", inject_after=1)
    retry = svc.recover_failure(first["failure"]["id"], inject_after=0)
    assert retry["cleared"] is False
    assert retry["failure"]["status"] == "active"
    with pytest.raises(svc.AlignRefused):
        svc.run_reconcile(2)


def test_policy_switch_is_exclusive(fresh_db):
    with pytest.raises(ValueError):
        svc.set_setting("extract_failure_policy", "both")
    svc.set_setting("extract_failure_policy", "void_batch")
    r = svc.run_reconcile(1, inject_side="left", inject_after=0)
    assert r["status"] == "voided"
    assert r["failure"]["status"] == "voided"


def test_reuse_refusal_attributed_to_originating_failure(fresh_db):
    svc.set_setting("extract_failure_policy", "void_batch")
    first = svc.run_reconcile(1, inject_side="right", inject_after=2)
    snapshots = svc.list_snapshots(pair_id=1)
    with pytest.raises(svc.AlignRefused) as exc:
        svc.align_from_snapshots(1, snapshots[1]["id"], snapshots[0]["id"])
    assert exc.value.payload["failure_id"] == first["failure"]["id"]
    evidence = svc.failure_evidence(first["failure"]["id"])
    assert evidence["refusal_count"] == 1


def test_void_policy_voided_snapshot_rejected_even_paired_with_valid(fresh_db):
    """A voided snapshot must not become legal by splicing with a later valid one."""
    assert svc.list_settings()["extract_failure_policy"] == "void_batch"
    bad = svc.run_reconcile(1, inject_side="right", inject_after=2)
    good = svc.run_reconcile(1, note="later clean run")
    assert good["status"] == "completed"

    voided_left = bad["left_snapshot_id"]
    valid_right = good["right_snapshot_id"]
    statuses = {s["id"]: s["status"] for s in svc.list_snapshots(pair_id=1)}
    assert statuses[voided_left] == "voided"
    assert statuses[valid_right] == "valid"

    diffs_before = len(svc.list_diffs(pair_id=1))
    with pytest.raises(svc.AlignRefused) as exc:
        svc.align_from_snapshots(1, voided_left, valid_right)
    payload = exc.value.payload
    assert payload["refused"] is True
    assert payload["source"] == "snapshot_reuse"
    assert payload["failure_id"] == bad["failure"]["id"]
    assert payload["refusal_count"] == 1
    # No new batch/diffs may be written by the refused alignment.
    assert len(svc.list_diffs(pair_id=1)) == diffs_before
    assert svc.failure_evidence(bad["failure"]["id"])["refusal_count"] == 1


def test_freeze_policy_reuse_entry_refused_and_counted_while_failure_active(fresh_db):
    """Snapshot-reuse entry must hit the same active-failure gate as a new run."""
    svc.set_setting("extract_failure_policy", "freeze_success")
    baseline = svc.run_reconcile(2, note="clean baseline")
    valid_left, valid_right = baseline["left_snapshot_id"], baseline["right_snapshot_id"]
    bad = svc.run_reconcile(2, inject_side="left", inject_after=1)
    fid = bad["failure"]["id"]
    assert bad["failure"]["status"] == "active"

    diffs_before = len(svc.list_diffs(pair_id=2))
    with pytest.raises(svc.AlignRefused) as exc:
        svc.align_from_snapshots(2, valid_left, valid_right, note="sneaky reuse")
    payload = exc.value.payload
    assert payload["refused"] is True
    assert payload["source"] == "active_failure_gate"
    assert payload["failure_id"] == fid

    # Every refused attempt — run entry and reuse entry — accumulates.
    refusals = svc.list_refusals(pair_id=2)
    assert [r["source"] for r in refusals if r["failure_id"] == fid]
    assert all(r["failure_id"] == fid for r in refusals)
    with pytest.raises(svc.AlignRefused):
        svc.run_reconcile(2)
    evidence = svc.failure_evidence(fid)
    assert evidence["refusal_count"] == 3  # run-time refusal + reuse + second run
    assert len(svc.list_diffs(pair_id=2)) == diffs_before


def test_freeze_policy_reuse_allowed_only_after_clearing(fresh_db):
    """No alignment (including snapshot reuse) before clearing; both clear paths reopen it."""
    svc.set_setting("extract_failure_policy", "freeze_success")
    baseline = svc.run_reconcile(2, note="clean baseline")
    valid_left, valid_right = baseline["left_snapshot_id"], baseline["right_snapshot_id"]

    bad = svc.run_reconcile(2, inject_side="right", inject_after=1)
    fid = bad["failure"]["id"]
    with pytest.raises(svc.AlignRefused):
        svc.align_from_snapshots(2, valid_left, valid_right)

    # Clear by discarding the frozen snapshot.
    assert svc.discard_failure(fid)["status"] == "cleared"
    reused = svc.align_from_snapshots(2, valid_left, valid_right, note="reuse after discard")
    assert reused["status"] == "completed"
    assert reused["diffs_written"] is True
    assert svc.run_reconcile(2)["status"] == "completed"


def test_freeze_policy_reuse_allowed_after_reextract_success(fresh_db):
    """Successful re-extraction clears the failure and reopens every entry point."""
    svc.set_setting("extract_failure_policy", "freeze_success")
    baseline = svc.run_reconcile(2, note="clean baseline")
    valid_left, valid_right = baseline["left_snapshot_id"], baseline["right_snapshot_id"]
    bad = svc.run_reconcile(2, inject_side="left", inject_after=1)
    fid = bad["failure"]["id"]

    with pytest.raises(svc.AlignRefused):
        svc.align_from_snapshots(2, valid_left, valid_right)
    recovered = svc.recover_failure(fid)
    assert recovered["cleared"] is True

    # Historical valid snapshots are reusable again once the failure is cleared.
    reused = svc.align_from_snapshots(2, valid_left, valid_right, note="reuse after recover")
    assert reused["status"] == "completed"
    assert reused["diffs_written"] is True


def test_active_failure_gate_survives_policy_switch(fresh_db):
    svc.set_setting("extract_failure_policy", "freeze_success")
    first = svc.run_reconcile(2, inject_side="left", inject_after=1)
    svc.set_setting("extract_failure_policy", "void_batch")
    with pytest.raises(svc.AlignRefused):
        svc.run_reconcile(2)
    assert svc.discard_failure(first["failure"]["id"])["status"] == "cleared"
    assert svc.run_reconcile(2)["status"] == "completed"
