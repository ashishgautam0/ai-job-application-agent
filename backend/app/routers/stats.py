from fastapi import APIRouter

from tracker import (
    get_cold_dm_todos,
    get_post_connection_follow_ups_due,
    get_platform_effectiveness,
    get_role_analysis,
    get_stats,
    get_status_funnel,
    get_weekly_trend,
)
from json_safe import json_records

router = APIRouter()


@router.get("/dashboard")
def dashboard_stats():
    return get_stats()


@router.get("/follow-ups")
def follow_ups():
    """Due follow-ups, each marked with whether it is actually sendable.

    A follow-up needs both halves: a written draft, and the person it goes to.
    It is a LinkedIn message now rather than an email, so the recipient is
    whoever accepted this job's connection request. Dashboard hides the ones
    missing either rather than offering a card that cannot be acted on.
    """
    from pending_messages import _connection_recipient
    from tracker import get_follow_up_draft

    df = get_post_connection_follow_ups_due()
    if df.empty:
        return []
    rows = json_records(df)

    for row in rows:
        try:
            draft = get_follow_up_draft(row["id"]) or {}
        except Exception:
            draft = {}
        row["recipient"] = _connection_recipient(row["id"]) or None
        row["draft_ready"] = draft.get("status") == "ready" and bool(
            (draft.get("content") or "").strip())
    return rows


@router.get("/cold-dm-todos")
def cold_dm_todos():
    # The dashboard only needs the latest persisted PDF profile version; avoid
    # a private Storage list call on every Dashboard refresh.
    from profile import get_latest_profile_snapshot
    resume = get_latest_profile_snapshot()
    return get_cold_dm_todos((resume or {}).get("version"))


@router.get("/weekly-trend")
def weekly_trend():
    df = get_weekly_trend()
    return json_records(df)


@router.get("/platform-effectiveness")
def platform_effectiveness():
    df = get_platform_effectiveness()
    return json_records(df)


@router.get("/status-funnel")
def status_funnel():
    return get_status_funnel()


@router.get("/role-analysis")
def role_analysis():
    df = get_role_analysis()
    return json_records(df)
