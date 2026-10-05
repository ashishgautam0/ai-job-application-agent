from fastapi import APIRouter, Query

from ..models.schemas import AddDemoRequest, UpdateDemoRequest
from tracker import add_mini_demo, get_active_demos, get_demo_results, update_mini_demo
from json_safe import json_records

router = APIRouter()


@router.get("")
def list_demos(
    active_only: bool = Query(True),
):
    df = get_active_demos() if active_only else get_demo_results()
    return json_records(df)


@router.post("")
def create_demo(
    body: AddDemoRequest,
):
    add_mini_demo(body.company, body.role, body.demo_idea)
    return {"success": True}


@router.patch("/{demo_id}")
def patch_demo(
    demo_id: int,
    body: UpdateDemoRequest,
):
    updates = body.model_dump(exclude_none=True)
    if updates:
        update_mini_demo(demo_id, **updates)
    return {"success": True}
