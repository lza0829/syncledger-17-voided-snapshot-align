from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import reconcile_service as svc

router = APIRouter(tags=["batches"])


class RunBody(BaseModel):
    pair_id: int
    note: str | None = None
    inject_side: str | None = None
    inject_after: int | None = None


class SnapshotAlignBody(BaseModel):
    pair_id: int
    left_snapshot_id: int
    right_snapshot_id: int
    note: str | None = None


@router.get("/batches")
def list_batches(limit: int = 50):
    return svc.list_batches(limit=limit)


@router.get("/diffs")
def list_diffs(batch_id: int | None = None, pair_id: int | None = None):
    return svc.list_diffs(batch_id=batch_id, pair_id=pair_id)


@router.post("/reconcile")
def run_reconcile(body: RunBody):
    try:
        return svc.run_reconcile(
            body.pair_id,
            body.note,
            inject_side=body.inject_side,
            inject_after=body.inject_after,
        )
    except svc.AlignRefused as e:
        raise HTTPException(e.status_code, detail={"detail": str(e), **e.payload}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/reconcile/from-snapshots")
def align_from_snapshots(body: SnapshotAlignBody):
    try:
        return svc.align_from_snapshots(
            body.pair_id, body.left_snapshot_id, body.right_snapshot_id, body.note
        )
    except svc.AlignRefused as e:
        raise HTTPException(e.status_code, detail={"detail": str(e), **e.payload}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
