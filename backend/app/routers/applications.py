from fastapi import APIRouter, Query
from typing import Optional

from ..models.schemas import AddApplicationRequest, UpdateStatusRequest, UpdateNotesRequest, SnoozeRequest, HrEmailTodoRequest
from tracker import (
    add_application,
    delete_application,
    get_all_applications,
    find_application_by_url,
    update_status,
    update_notes,
    snooze_follow_up,
    set_hr_email_todo_completed,
)
from json_safe import json_records

router = APIRouter()


@router.get("/lookup")
def lookup_application(url: str = Query(...)):
    return find_application_by_url(url)


@router.get("")
def list_applications(
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    platform: Optional[str] = None,

):
    df = get_all_applications()
    if df.empty:
        return []
    if status_filter:
        df = df[df["status"] == status_filter]
    if type_filter:
        df = df[df["type"] == type_filter]
    if platform:
        df = df[df["platform"] == platform]
    return json_records(df)


@router.post("")
def create_application(
    body: AddApplicationRequest,

):
    add_application(
        company=body.company,
        role=body.role,
        job_type=body.job_type,
        platform=body.platform,
        url=body.url,
        noc_compatible=body.noc_compatible,
        conversion=body.conversion,
        salary=body.salary,
        notes=body.notes,
    )
    return {"success": True}


@router.patch("/{app_id}/status")
def patch_status(
    app_id: int,
    body: UpdateStatusRequest,

):
    update_status(app_id, body.status)
    return {"success": True}


@router.patch("/{app_id}/notes")
def patch_notes(
    app_id: int,
    body: UpdateNotesRequest,

):
    update_notes(app_id, body.notes)
    return {"success": True}


@router.patch("/{app_id}/snooze")
def snooze(app_id: int, body: SnoozeRequest):
    snooze_follow_up(app_id, body.new_date.isoformat())
    return {"success": True}


@router.patch("/{app_id}/hr-email-todo")
def update_hr_email_todo(app_id: int, body: HrEmailTodoRequest):
    completed_at = set_hr_email_todo_completed(app_id, body.completed)
    return {"success": True, "hr_email_sent_at": completed_at}


@router.delete("/{app_id}")
def remove_application(
    app_id: int,

):
    delete_application(app_id)
    return {"success": True}