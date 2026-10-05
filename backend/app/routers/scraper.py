from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import MarkScrapedJobRequest
from tracker import (
    delete_scraped_job,
    find_scraped_job_by_url,
    get_job_message,
    get_current_cover_letter,
    get_scraped_job,
    get_scraped_jobs,
    mark_scraped_job,
)
from json_safe import json_records

router = APIRouter()


def _records(df):
    """Return JSON-safe records while preserving database NULL values."""
    return json_records(df)


def _active_profile():
    try:
        from profile import get_active_profile_snapshot
        return get_active_profile_snapshot()
    except Exception:
        return None


@router.get("")
def list_scraped_jobs(
    source: str | None = None,
):
    df = get_scraped_jobs(source=source)
    return _records(df)


@router.get("/ranked")
def ranked_scraped_jobs(limit: int = 200):
    """Scraped jobs ordered by BestScore — the queue to apply to, best first.

    Each row gains `bestscore` (0–100) and a `bestscore_breakdown`, computed
    live from the active resume snapshot and current job description.
    """
    from ranking import rank_jobs

    df = get_scraped_jobs()
    jobs = _records(df)
    if not jobs:
        return []
    return rank_jobs(jobs, profile_snapshot=_active_profile())[:limit]


@router.get("/lookup")
def lookup_scraped_job(url: str = Query(...)):
    """Resolve a posting URL to its scraped job id (e.g. from a tracker row)."""
    row = find_scraped_job_by_url(url)
    return {"id": row["id"] if row else None}


@router.get("/{job_id}")
def get_one_scraped_job(job_id: int):
    """One scraped job by id, regardless of applied/dismissed state."""
    row = get_scraped_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@router.delete("/{job_id}")
def delete_one_scraped_job(job_id: int):
    """Permanently delete a scraped job and its stored messages."""
    if not delete_scraped_job(job_id):
        raise HTTPException(status_code=500, detail="Delete failed")
    return {"success": True}


@router.patch("/{job_id}")
def mark_job(
    job_id: int,
    body: MarkScrapedJobRequest,
):
    mark_scraped_job(job_id, body.action)
    return {"success": True}


@router.get("/{job_id}/message")
def job_message(
    job_id: int,
    type: str = "cold_dm",
):
    """Draft content written for this job by the configured cloud routine.

    Returns {"content": null} when the routine has not written one yet, rather
    than 404 — the UI treats "not generated yet" as a normal state.
    """
    row = (get_current_cover_letter(job_id) if type == "cover_letter"
           else get_job_message(job_id, message_type=type))
    if not row:
        return {"job_id": job_id, "message_type": type, "content": None}
    return {
        "job_id": job_id,
        "message_type": "cover_letter" if type == "cover_letter" else row.get("message_type"),
        "content": row.get("content"),
        "generated_by": row.get("generated_by"),
        "generated_at": row.get("generated_at"),
        "is_outdated": row.get("is_outdated", False),
        "resume_version": row.get("resume_version") or row.get("profile_version"),
        "jd_version": row.get("jd_version"),
        "jd_hash": row.get("jd_hash"),
        "match_score": row.get("match_score"),
        "analysis_version": row.get("analysis_version"),
        "generation_rules_version": row.get("generation_rules_version"),
        "run_id": row.get("run_id"),
    }
