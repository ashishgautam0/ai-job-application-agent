from fastapi import APIRouter, Query
from typing import Optional

from ..models.schemas import LogFollowUpRequest, UpdateFollowUpOutcomeRequest
from tracker import (
    log_follow_up,
    get_follow_up_draft,
    get_follow_up_history,
    update_follow_up_outcome,
    update_status,
    get_follow_up_effectiveness,
)

router = APIRouter()


@router.post("/log")
def log_follow_up_sent(body: LogFollowUpRequest):
    follow_up_number = log_follow_up(
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        message_content=body.message_content,
        channel=body.channel,
        recipient_name=body.recipient_name,
        recipient_profile_url=body.recipient_profile_url,
    )
    if body.entity_type == "application":
        update_status(body.entity_id, "Follow-up Sent")
    return {"success": True, "follow_up_number": follow_up_number}


@router.get("/draft")
def follow_up_draft(entity_id: int = Query(...)):
    """The follow-up message pre-written for this application by the routine.

    status is "ready" (content present), "pending" (queued, written on the
    next hourly run) or null (nothing queued yet — the routine queues one
    once the application's follow_up_date arrives).
    """
    row = get_follow_up_draft(entity_id)
    if not row:
        return {"entity_id": entity_id, "status": None, "content": None}
    return {
        "entity_id": entity_id,
        "status": row.get("status"),
        "content": row.get("content"),
        "request_id": row.get("id"),
        "follow_up_number": (row.get("params") or {}).get("follow_up_number"),
    }


@router.get("/history")
def follow_up_history(
    entity_type: str = Query(...),
    entity_id: int = Query(...),
):
    records = get_follow_up_history(entity_type, entity_id)
    return records


@router.patch("/{history_id}/outcome")
def patch_outcome(history_id: int, body: UpdateFollowUpOutcomeRequest):
    update_follow_up_outcome(history_id, body.outcome)
    return {"success": True}


@router.get("/effectiveness")
def effectiveness():
    return get_follow_up_effectiveness()
