from fastapi import APIRouter, HTTPException

from app.services import reconcile_service as svc

router = APIRouter(tags=["extraction"])


@router.get("/extraction/failures")
def list_failures(pair_id: int | None = None):
    return svc.list_failures(pair_id=pair_id)


@router.get("/extraction/failures/{failure_id}/evidence")
def failure_evidence(failure_id: int):
    try:
        return svc.failure_evidence(failure_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/extraction/snapshots")
def list_snapshots(pair_id: int | None = None):
    return svc.list_snapshots(pair_id=pair_id)


@router.get("/extraction/refusals")
def list_refusals(pair_id: int | None = None):
    return svc.list_refusals(pair_id=pair_id)


@router.post("/extraction/failures/{failure_id}/discard")
def discard_failure(failure_id: int):
    try:
        return svc.discard_failure(failure_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/extraction/failures/{failure_id}/recover")
def recover_failure(failure_id: int, inject_after: int | None = None):
    try:
        return svc.recover_failure(failure_id, inject_after=inject_after)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
