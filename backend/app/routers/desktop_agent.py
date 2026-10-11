"""Endpoints the Claude Desktop Computer Use agent calls while it applies.

The agent runs in the user's own browser, so it reaches these over HTTPS rather
than importing the modules directly. What it needs from the backend: the set of
postings already dealt with (so repeat runs skip them), a way to record a job it
just handled into both the job list and the Tracker, and — once the day's
applications are done — the Cold DMs that are due and a way to record each one
it sends.
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from ..models.schemas import DesktopAgentColdDmRequest, DesktopAgentJobRequest
from profile import get_latest_profile_snapshot
from tracker import (
    DAILY_APPLICATION_TARGET,
    DAILY_DM_TARGET,
    DM_CHANNEL,
    _linkedin_connection_dates,
    add_application,
    count_applications_today,
    count_dms_today,
    find_application_by_url,
    find_scraped_job_by_url,
    get_cold_dm_prompt_jobs,
    get_handled_job_urls,
    log_follow_up,
    mark_scraped_job,
    save_scraped_job,
    update_status,
)

router = APIRouter()


def _daily_progress():
    """Today's applications against the target, so the agent knows when to stop.

    Counted from the tracker rather than by the agent, so earlier runs the same
    day are included. A counting failure must not fail the call — the
    application is already recorded — so the count is reported as unknown.
    """
    try:
        applied_today = count_applications_today()
    except Exception as exc:
        print(f"[desktop-agent] could not count today's applications: {exc}")
        applied_today = None
    try:
        dms_today = count_dms_today()
    except Exception as exc:
        print(f"[desktop-agent] could not count today's Cold DMs: {exc}")
        dms_today = None
    return {"applied_today": applied_today, "daily_target": DAILY_APPLICATION_TARGET,
            "dms_today": dms_today, "dm_target": DAILY_DM_TARGET}


@router.get("/seen-urls", name="desktop_agent_seen_urls")
def seen_urls():
    """Posting URLs already applied to or dismissed, for the agent's skip list.

    Deliberately excludes postings that are merely present and unhandled, such
    as the rows the removed job scraper left behind: those were never applied
    to, so applying to them is still the agent's job.
    """
    urls = get_handled_job_urls()
    return {"urls": sorted(urls), "count": len(urls), **_daily_progress()}


@router.post("/jobs", name="desktop_agent_record_job")
def record_job(body: DesktopAgentJobRequest):
    """Record a handled posting, whichever portal it came from.

    Applied postings reach the Tracker. Skipped ones are dismissed, which both
    hides them and puts them on the next run's skip list.
    """
    # Only insert a posting no row already holds. save_scraped_job overwrites
    # the whole row, which on a known job would replace the full JD with the
    # agent's summary, wipe any score and analysis already on it, and mark its
    # cover letter outdated.
    scraped = find_scraped_job_by_url(body.url)
    if not scraped:
        save_scraped_job(
            title=body.title,
            company=body.company,
            location=body.location,
            source=body.source,
            url=body.url,
            description=body.description,
        )
        scraped = find_scraped_job_by_url(body.url)

    if body.status == "skipped":
        if scraped:
            mark_scraped_job(scraped["id"], "dismissed")
        return {"saved": True, "applied": False, "dismissed": bool(scraped), "duplicate": False}

    # A posting the user already applied to must not produce a second Tracker
    # row; the agent can re-send the same job after an interrupted run.
    if find_application_by_url(body.url):
        return {"saved": True, "applied": False, "dismissed": False, "duplicate": True}

    add_application(
        company=body.company,
        role=body.title,
        job_type=body.job_type,
        platform=body.source,
        url=body.url,
        notes=body.notes,
    )
    if scraped:
        mark_scraped_job(scraped["id"], "applied")
    return {"saved": True, "applied": True, "dismissed": False, "duplicate": False,
            **_daily_progress()}


def _ready_cold_dms():
    """Due Tracker jobs with a current stored note, as the agent should see them.

    The same eligibility check the Settings Cold DM prompt and the Dashboard
    use: due today, no connection note recorded yet, a draft written for the
    latest resume and a matching job. Anything else is left out, not guessed at.
    """
    version = (get_latest_profile_snapshot() or {}).get("version")
    due = get_cold_dm_prompt_jobs(version, limit=None)
    return [job for job in due if not job["blocked_reason"] and job.get("cold_dm")], len(due)


def _people_search(company, who):
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote(f"{company} {who}")


@router.get("/cold-dms", name="desktop_agent_cold_dms")
def cold_dms():
    """The Cold DMs due now, fetched live so drafts written during the day count."""
    ready, due = _ready_cold_dms()
    jobs = [{
        "tracker_id": job["tracker_id"],
        "company": job["company"],
        "title": job["title"],
        "location": job["location"],
        "url": job["url"],
        "cold_dm": job["cold_dm"],
        "recruiters_search_url": _people_search(job["company"], "recruiter"),
        "hiring_managers_search_url": _people_search(job["company"], "hiring manager"),
    } for job in ready]
    return {"jobs": jobs, "count": len(jobs), "not_ready": due - len(jobs), **_daily_progress()}


@router.post("/cold-dms", name="desktop_agent_record_cold_dm")
def record_cold_dm(body: DesktopAgentColdDmRequest):
    """Record a sent connection note, exactly as the Tracker's outreach form does.

    One note per Tracker job: a job that already has one is reported as a
    duplicate rather than logged twice, since a re-sent record would advance its
    follow-up schedule a second time.
    """
    if _linkedin_connection_dates([body.tracker_id]):
        return {"recorded": False, "duplicate": True, **_daily_progress()}
    ready, _ = _ready_cold_dms()
    if body.tracker_id not in {job["tracker_id"] for job in ready}:
        raise HTTPException(status_code=404, detail="That Tracker job is not in today's Cold DM list.")
    log_follow_up(
        "application", body.tracker_id,
        # The trailer stays: it is the human-readable record in the history
        # table, and the columns below are the queryable copy of the same facts.
        message_content=f"{body.note}\n\nSent to {body.recipient_name}: {body.recipient_profile_url}",
        channel=DM_CHANNEL,
        recipient_name=body.recipient_name,
        recipient_profile_url=body.recipient_profile_url,
    )
    update_status(body.tracker_id, "Follow-up Sent")
    return {"recorded": True, "duplicate": False, **_daily_progress()}
