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


def _sendable_follow_ups(df):
    """Due follow-ups, each marked with whether it is actually sendable.

    A follow-up needs both halves: a written draft, and the person it goes to.
    The follow-up is a LinkedIn message, so the recipient is whoever accepted
    this job's connection request. Dashboard hides the ones missing either
    rather than offering a card that cannot be acted on.
    """
    from pending_messages import _connection_records
    from tracker import get_follow_up_drafts

    if df.empty:
        return []
    rows = json_records(df)

    # Two batched reads for the whole queue, not two per row: the Dashboard
    # waits on this, and the database is a continent away, so a per-row lookup
    # cost a round-trip each and held the page at "Loading...".
    ids = [row["id"] for row in rows]
    drafts = get_follow_up_drafts(ids)
    recipients = _connection_records(ids)

    for row in rows:
        app_id = int(row["id"])
        draft = drafts.get(app_id) or {}
        row["recipient"] = recipients.get(app_id, ("", ""))[0] or None
        row["draft_ready"] = draft.get("status") == "ready" and bool(
            (draft.get("content") or "").strip())
        # pandas widens an int column to float once any row is missing a match,
        # which reached the UI as "/jobs/53773.0".
        job_id = row.get("scraped_job_id")
        row["scraped_job_id"] = int(job_id) if job_id is not None else None
    return rows


def _resume_version():
    # The dashboard only needs the latest persisted PDF profile version; avoid
    # a private Storage list call on every Dashboard refresh.
    from profile import get_latest_profile_snapshot
    return (get_latest_profile_snapshot() or {}).get("version")


@router.get("/follow-ups")
def follow_ups():
    return _sendable_follow_ups(get_post_connection_follow_ups_due())


@router.get("/cold-dm-todos")
def cold_dm_todos():
    return get_cold_dm_todos(_resume_version())


@router.get("/due-queues")
def due_queues():
    """Both Dashboard queues from one snapshot of the due list.

    Each queue alone re-read the due applications and the recorded connection
    dates, so loading the Dashboard paid for them twice. They are derived from
    the same snapshot here instead. The two single-queue routes above stay as
    they are for callers that want only one.
    """
    from tracker import _linkedin_connection_dates, get_follow_ups_due

    due = get_follow_ups_due()
    if due.empty:
        return {"follow_ups": [], "cold_dms": []}
    connection_dates = _linkedin_connection_dates(due["id"].tolist())
    return {
        "follow_ups": _sendable_follow_ups(
            get_post_connection_follow_ups_due(due, connection_dates)),
        "cold_dms": get_cold_dm_todos(_resume_version(), due, connection_dates),
    }


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
