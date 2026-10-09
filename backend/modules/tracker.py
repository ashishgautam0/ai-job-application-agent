"""
Database layer — Supabase (PostgreSQL) backend.
All other modules import from here. Function signatures and return types
are unchanged from the original SQLite version.
"""

import os
import hashlib
import pandas as pd
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from supabase import create_client
import json

# --- Outreach cadence (global defaults) ---
# Day offsets from the application date. Day 1 the job is tracked; day 8
# (offset 7) is the first outreach — HR email plus a LinkedIn connection note,
# each carrying that job's demo. Day 16 (offset 15) is the single follow-up
# round. Two rounds only: after the second, the record is marked Ghosted.
APPLICATION_CADENCE = [7, 15]
INTERVIEW_FOLLOW_UP_DAYS = 3
TERMINAL_STATUSES = ["Offer", "Rejected", "Ghosted", "Not Interested"]
USER_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _user_now():
    return datetime.now(USER_TIMEZONE)

# --- Supabase client (lazy singleton) ---
_supabase_client = None


def _get_client():
    """Return the Supabase client, creating it on first call."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")

    if not _url or not _key:
        raise RuntimeError(
            "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY "
            "as environment variables."
        )

    _supabase_client = create_client(_url, _key)
    return _supabase_client


def init_db():
    """No-op. Tables are created via Supabase dashboard SQL editor."""
    pass


# ===================== APPLICATION FUNCTIONS =====================

# Applications per day the dashboard tracks and the desktop agent stops at.
DAILY_APPLICATION_TARGET = 10


def count_applications_today():
    """Applications recorded today, in the user's timezone, from any source."""
    today = _user_now().strftime("%Y-%m-%d")
    resp = (_get_client().table("applications").select("id", count="exact")
            .eq("date_applied", today).execute())
    return resp.count if resp.count is not None else len(resp.data or [])


# LinkedIn connection invitations with a note — the Cold DM — sent per day,
# the second half of the desktop agent's run once the applications are done.
DAILY_DM_TARGET = 10
DM_CHANNEL = "LinkedIn connection"


def count_dms_today():
    """Cold DMs (LinkedIn connection notes) recorded since midnight, user's timezone."""
    midnight = _user_now().replace(hour=0, minute=0, second=0, microsecond=0)
    resp = (_get_client().table("follow_up_history").select("id", count="exact")
            .eq("channel", DM_CHANNEL).gte("sent_at", midnight.isoformat()).execute())
    return resp.count if resp.count is not None else len(resp.data or [])


def add_application(company, role, job_type, platform, url="",
                    noc_compatible="Unknown", conversion="N/A",
                    salary="", notes=""):
    db = _get_client()
    today = _user_now().strftime("%Y-%m-%d")
    follow_up = (_user_now() + timedelta(days=APPLICATION_CADENCE[0])).strftime("%Y-%m-%d")
    db.table("applications").insert({
        "company": company,
        "role": role,
        "type": job_type,
        "platform": platform,
        "url": url,
        "date_applied": today,
        "follow_up_date": follow_up,
        "noc_compatible": noc_compatible,
        "conversion_potential": conversion,
        "salary_range": salary,
        "notes": notes,
    }).execute()


def update_status(app_id, new_status):
    db = _get_client()
    update_data = {"status": new_status}

    if new_status in TERMINAL_STATUSES:
        update_data["follow_up_date"] = None
    elif new_status == "Follow-up Sent":
        resp = (db.table("applications")
                .select("follow_up_count")
                .eq("id", app_id).single().execute())
        app = resp.data
        count = (app.get("follow_up_count") or 0) + 1
        update_data["follow_up_count"] = count
        if count < len(APPLICATION_CADENCE):
            # Start the next window when outreach is recorded, not from the
            # original application date. Late sends must not make the next
            # round immediately overdue, so the cadence supplies the gap
            # between rounds rather than an absolute date.
            gap = APPLICATION_CADENCE[count] - APPLICATION_CADENCE[count - 1]
            update_data["follow_up_date"] = (_user_now().date() + timedelta(days=gap)).isoformat()
        else:
            # Cadence exhausted — auto-mark as Ghosted
            update_data["follow_up_date"] = None
            update_data["status"] = "Ghosted"
    elif new_status == "Interview":
        update_data["follow_up_date"] = (
            _user_now() + timedelta(days=INTERVIEW_FOLLOW_UP_DAYS)
        ).strftime("%Y-%m-%d")

    db.table("applications").update(update_data).eq("id", app_id).execute()


def update_notes(app_id, notes):
    db = _get_client()
    db.table("applications").update({"notes": notes}).eq("id", app_id).execute()


def get_all_applications():
    db = _get_client()
    resp = db.table("applications").select("*").order("date_applied", desc=True).execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return df
    from analytics import attach_tracker_job_ids
    urls = [url for url in df.get("url", pd.Series(dtype=str)).dropna().tolist() if url]
    jobs = []
    for start in range(0, len(urls), 200):
        jobs.extend((db.table("scraped_jobs").select("id,url")
                     .in_("url", urls[start:start + 200]).execute()).data or [])
    mapped = attach_tracker_job_ids(df.to_dict("records"), jobs)
    result = pd.DataFrame(mapped)
    # Keep missing detail mappings as JSON null rather than pandas NaN.
    result["scraped_job_id"] = pd.Series(
        [row["scraped_job_id"] for row in mapped], dtype="object"
    )
    return result


def find_application_by_url(url):
    if not url:
        return None
    rows = (_get_client().table("applications").select("*").eq("url", url)
            .order("created_at", desc=True).limit(1).execute()).data or []
    return rows[0] if rows else None


def is_scraped_job_tracked(job_id):
    """True only when this scraped job belongs to an active tracker record."""
    try:
        db = _get_client()
        job = (db.table("scraped_jobs").select("url").eq("id", int(job_id))
               .single().execute()).data or {}
        url = (job.get("url") or "").strip()
        if not url:
            return False
        rows = (db.table("applications").select("status").eq("url", url)
                .execute()).data or []
        return any(row.get("status") not in TERMINAL_STATUSES for row in rows)
    except Exception:
        return False


def get_follow_ups_due():
    db = _get_client()
    today = _user_now().strftime("%Y-%m-%d")
    # The Data API caps a select response (often at 1,000 rows); include every
    # due application so both Dashboard queues use the same complete snapshot.
    rows = []
    page_size = 1000
    for start in range(0, 1000000, page_size):
        batch = (db.table("applications").select("*")
                 .lte("follow_up_date", today).order("id")
                 .range(start, start + page_size - 1).execute()).data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    else:
        raise RuntimeError("Due follow-up list exceeded pagination limit; retry after archiving records")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[~df["status"].isin(TERMINAL_STATUSES)]
    df = df[df["follow_up_date"].notna() & (df["follow_up_date"] != "")]
    # Sort by urgency: most overdue first
    df = df.sort_values("follow_up_date", ascending=True)
    from analytics import attach_tracker_job_ids
    urls = [url for url in df.get("url", pd.Series(dtype=str)).dropna().tolist() if url]
    if urls:
        jobs = []
        for start in range(0, len(urls), 200):
            jobs.extend((db.table("scraped_jobs").select("id,url")
                         .in_("url", urls[start:start + 200]).execute()).data or [])
        df = pd.DataFrame(attach_tracker_job_ids(df.to_dict("records"), jobs))
    else:
        df["scraped_job_id"] = None
    return df


def _linkedin_connection_dates(application_ids):
    """Latest recorded connection date per Tracker ID in the user's timezone.

    Include IDs with malformed sent_at as None: a recorded connection still
    prevents another invitation, but cannot authorize a timed follow-up.
    """
    ids = sorted({int(value) for value in application_ids if value is not None})
    dates = {}
    if not ids:
        return dates
    db = _get_client()
    for offset in range(0, len(ids), 200):
        chunk = ids[offset:offset + 200]
        for start in range(0, 1000000, 1000):
            rows = (db.table("follow_up_history")
                    .select("id,entity_id,sent_at")
                    .eq("entity_type", "application")
                    .eq("channel", "LinkedIn connection")
                    .in_("entity_id", chunk).order("id")
                    .range(start, start + 999).execute()).data or []
            for row in rows:
                app_id = int(row["entity_id"])
                dates.setdefault(app_id, None)
                try:
                    sent_at = datetime.fromisoformat(str(row["sent_at"]).replace("Z", "+00:00"))
                    if sent_at.tzinfo is None:
                        continue
                    sent_day = sent_at.astimezone(USER_TIMEZONE).date()
                except (TypeError, ValueError):
                    continue
                if dates[app_id] is None or dates[app_id] < sent_day:
                    dates[app_id] = sent_day
            if len(rows) < 1000:
                break
        else:
            raise RuntimeError("LinkedIn connection history exceeds pagination limit")
    return dates


def get_post_connection_follow_ups_due():
    """Only follow-ups due at least seven Kolkata calendar days after a sent connection."""
    due = get_follow_ups_due()
    if due.empty:
        return due
    dates = _linkedin_connection_dates(due["id"].tolist())
    today = _user_now().date()
    return due[due["id"].map(lambda app_id: dates.get(int(app_id)) is not None
               and today >= dates[int(app_id)] + timedelta(days=7))]


def get_cold_dm_todos(resume_version=None):
    """Use the same eligibility check for Dashboard badges and the copied batch."""
    rows = get_cold_dm_prompt_jobs(resume_version, limit=None)
    return [{"id": row["tracker_id"], "company": row["company"], "role": row["title"],
             "follow_up_date": row["follow_up_date"], "scraped_job_id": row["job_id"],
             "cold_dm_ready": not bool(row["blocked_reason"]),
             "readiness_issue": row["blocked_reason"] or None}
            for row in rows]


def get_cold_dm_prompt_jobs(resume_version=None, limit=100):
    """Build a bounded, fixed batch of due Tracker jobs and their stored notes.

    Keep blocked records in the output so missing or stale drafts are visible.
    Reuse the same URL-to-job mapping and Asia/Kolkata due filter as Dashboard.
    """
    due = get_follow_ups_due()
    if due.empty:
        return []
    rows = due.astype(object).where(due.notna(), None).to_dict("records")
    # Once a connection has been logged, this job moves to the later follow-up
    # stage; never present it as a fresh invitation again.
    connected = _linkedin_connection_dates(row["id"] for row in rows)
    rows = [row for row in rows if int(row["id"]) not in connected]
    if limit is not None and len(rows) > limit:
        raise ValueError(f"{len(rows)} Cold DMs are due; the prompt limit is {limit}. Resolve due jobs before generating the batch.")

    job_ids = sorted({int(row["scraped_job_id"]) for row in rows if row.get("scraped_job_id")})
    jobs, messages = {}, {}
    if job_ids:
        db = _get_client()
        for start in range(0, len(job_ids), 200):
            chunk = job_ids[start:start + 200]
            for job in (db.table("scraped_jobs")
                        .select("id,title,company,location,source,url")
                        .in_("id", chunk).execute()).data or []:
                jobs[job["id"]] = job
            for message in (db.table("job_messages")
                            .select("scraped_job_id,content,is_stale,profile_version,generated_at")
                            .eq("message_type", "cold_dm").in_("scraped_job_id", chunk)
                            .execute()).data or []:
                messages[message["scraped_job_id"]] = message

    batch = []
    for row in rows:
        job_id = int(row["scraped_job_id"]) if row.get("scraped_job_id") is not None else None
        job = jobs.get(job_id) or {}
        cold_dm = messages.get(job_id) or {}
        current = (bool(cold_dm.get("content", "").strip()) and not cold_dm.get("is_stale")
                   and resume_version is not None and cold_dm.get("profile_version") == resume_version)
        match = bool(job and job.get("url") == row.get("url")
                     and str(job.get("company") or "").strip().casefold() ==
                     str(row.get("company") or "").strip().casefold())
        # These are already tracked, due follow-ups. A saved screen is required
        # for new application discovery, not for a current draft on an existing
        # Tracker record. Retain the PDF-version and exact-job safeguards.
        blocked = ("No matching scraped job" if not match else
                   "No current Cold DM for the latest Settings PDF" if not current else "")
        batch.append({
            "job_id": job_id if match else None,
            "tracker_id": int(row["id"]),
            "title": job.get("title") or row.get("role") or "",
            "company": row.get("company") or "",
            "location": job.get("location") or "",
            "source": job.get("source") or "",
            "url": row.get("url") or "",
            "follow_up_date": row["follow_up_date"],
            "cold_dm": cold_dm["content"] if match and current else None,
            "cold_dm_generated_at": cold_dm.get("generated_at") if match and current else None,
            "resume_version": resume_version if match and current else None,
            "blocked_reason": blocked,
        })
    return batch


def _add_dm_progress(stats):
    """Today's Cold DMs for the Dashboard; a failed count must not hide the rest."""
    try:
        stats['dms_today'] = count_dms_today()
    except Exception as exc:
        print(f"[tracker] could not count today's Cold DMs: {exc}")
        stats['dms_today'] = 0
    stats['dm_target'] = DAILY_DM_TARGET


def get_stats():
    db = _get_client()
    resp = db.table("applications").select("status, type, platform, date_applied").execute()
    df = pd.DataFrame(resp.data)

    stats = {}
    if df.empty:
        stats['total'] = 0
        stats['applied'] = 0
        stats['interview'] = 0
        stats['offer'] = 0
        stats['rejected'] = 0
        stats['this_week'] = 0
        stats['today'] = 0
        stats['daily_target'] = DAILY_APPLICATION_TARGET
        _add_dm_progress(stats)
        stats['jobs'] = 0
        stats['internships'] = 0
        stats['by_platform'] = []
        stats['response_rates'] = []
        return stats

    stats['total'] = len(df)
    stats['applied'] = len(df[df['status'] == 'Applied'])
    stats['interview'] = len(df[df['status'].isin(['Interview', 'Interview Scheduled', 'Interviewed'])])
    stats['offer'] = len(df[df['status'] == 'Offer'])
    stats['rejected'] = len(df[df['status'] == 'Rejected'])

    # Calculate this_week: all applications (jobs + internships) from the current week
    today = _user_now().replace(tzinfo=None)
    start_of_week = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    df_dates = pd.to_datetime(df['date_applied'], errors='coerce')
    stats['this_week'] = int((df_dates >= start_of_week).sum())
    stats['today'] = int((df['date_applied'].astype(str).str[:10]
                          == today.strftime("%Y-%m-%d")).sum())
    stats['daily_target'] = DAILY_APPLICATION_TARGET
    _add_dm_progress(stats)

    stats['jobs'] = len(df[df['type'] == 'Job'])
    stats['internships'] = len(df[df['type'] == 'Internship'])

    # by_platform: list of (platform, count) tuples
    stats['by_platform'] = list(
        df.groupby('platform').size().sort_values(ascending=False).items()
    )

    # response_rates: list of (platform, total, responses) tuples
    response_rates = []
    for platform, group in df.groupby('platform'):
        total = len(group)
        responses = len(group[group['status'].isin(
            ['Interview', 'Interview Scheduled', 'Interviewed', 'Offer']
        )])
        response_rates.append((platform, total, responses))
    stats['response_rates'] = response_rates

    return stats


def delete_application(app_id):
    db = _get_client()
    db.table("applications").delete().eq("id", app_id).execute()


def snooze_follow_up(app_id, new_date):
    """Reschedule a follow-up to a custom date without changing status or count."""
    parsed = date.fromisoformat(new_date)
    db = _get_client()
    db.table("applications").update(
        {"follow_up_date": parsed.isoformat()}
    ).eq("id", app_id).execute()


# ===================== SCRAPED JOBS FUNCTIONS =====================

def dedup_window_days():
    """Days of scrape history that count as "already seen". Tunable via env."""
    try:
        return int(os.environ.get("DEDUP_WINDOW_DAYS", "14"))
    except ValueError:
        return 14


def _paginate(query_fn):
    """Collect every url a query matches; Supabase caps one select at 1000 rows."""
    urls = set()
    page_size = 1000
    offset = 0
    while True:
        resp = query_fn(offset, offset + page_size - 1).execute()
        batch = resp.data or []
        urls.update(row["url"] for row in batch if row.get("url"))
        if len(batch) < page_size:
            break
        offset += page_size
    return urls


def get_handled_job_urls():
    """URLs already acted on: applied to, or dismissed as not a fit.

    This is a different question from get_existing_job_urls. That one asks "has
    the scraper saved this yet", so it covers every discovered posting. This one
    asks "have we finished with this", so a posting the scraper found and left
    pending in Today Todo is absent — the desktop agent is meant to apply to
    exactly those. All-time on purpose: a job applied to a year ago must never
    come back around.

    Errors propagate on purpose. An empty skip list is indistinguishable from a
    working one with nothing in it, and it is the worst possible default here:
    it tells the agent to apply to everything again.
    """
    db = _get_client()

    def scraped(flag):
        # applied/dismissed are integer columns — a boolean here makes
        # PostgREST send eq.true, which Postgres cannot cast to integer.
        return lambda lo, hi: (db.table("scraped_jobs").select("url")
                               .eq(flag, 1).range(lo, hi))

    def applications(lo, hi):
        return db.table("applications").select("url").range(lo, hi)

    return (_paginate(scraped("applied")) | _paginate(scraped("dismissed"))
            | _paginate(applications))


def get_existing_job_urls(since_days=None):
    """URLs already in scraped_jobs, for deduplication.

    Paginates because Supabase caps a single select at 1000 rows; without this
    the dedup set is incomplete and already-seen jobs get re-added every run.

    `since_days` bounds the window: only URLs scraped within the last N days are
    treated as "already seen". This stops the all-time table (thousands of old,
    non-dismissed rows) from blocking genuinely re-listed roles.

    Dismissed URLs are always excluded regardless of age — a dismissal is
    permanent and a re-scrape of a dismissed posting must never recount it as
    "new" or reset its dismissed state.

    Errors propagate on purpose: an empty set silently makes every job look new
    and the whole run re-processes what it already handled.
    """
    db = _get_client()
    cutoff = None
    if since_days:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

    # dismissed is an integer column, so these filters must be 0/1 — a boolean
    # makes PostgREST send eq.true, which Postgres cannot cast to integer.

    # Windowed set: recent non-dismissed rows (prevents re-listing noise)
    def recent_query(lo, hi):
        q = db.table("scraped_jobs").select("url").eq("dismissed", 0)
        if cutoff:
            q = q.gte("scraped_at", cutoff)
        return q.range(lo, hi)

    # All-time set: every dismissed URL regardless of age
    def dismissed_query(lo, hi):
        return db.table("scraped_jobs").select("url").eq("dismissed", 1).range(lo, hi)

    return _paginate(recent_query) | _paginate(dismissed_query)


def save_scraped_job(title, company, location, source, url, description="",
                     score=0, noc_verdict="", skill_match=None,
                     verdict="", ats_score=None, analysis_details=None,
                     analysis_version=None, profile_version=None,
                     company_exclusions=None):
    # Guard direct/manual intake too; never delete existing application history.
    from intake_policy import exclusion_reason
    job = {"company": company, "description": description}
    reason = exclusion_reason(job)
    if not reason and company_exclusions is None:
        from profile import get_company_exclusions
        company_exclusions = get_company_exclusions()
    if not reason:
        reason = exclusion_reason(job, company_exclusions or ())
    if reason:
        print(f"Skipped scraped job at {company}: {reason}")
        return
    db = _get_client()
    try:
        jd_hash = hashlib.sha256((description or "").strip().encode("utf-8")).hexdigest()
        existing = (db.table("scraped_jobs").select("id,jd_hash,jd_version")
                    .eq("url", url).limit(1).execute()).data or []
        jd_version = int(existing[0].get("jd_version") or 1) if existing else 1
        changed = bool(existing and existing[0].get("jd_hash") != jd_hash)
        if changed:
            jd_version += 1
            db.table("cover_letter_drafts").update({"is_outdated": True}).eq(
                "scraped_job_id", existing[0]["id"]
            ).eq("is_outdated", False).execute()
        payload = {
            "title": title,
            "company": company,
            "location": location,
            "source": source,
            "url": url,
            "description": description,
            "score": score,
            "noc_verdict": noc_verdict or "",
            "skill_match": skill_match,
            "verdict": verdict or "",
            "ats_score": ats_score,
            "profile_version": profile_version,
            "analysis_version": analysis_version,
            "analysis_stale": profile_version is None,
            "analysis_details": analysis_details or {},
            "analyzed_at": datetime.now().isoformat() if analysis_version else None,
            "jd_hash": jd_hash,
            "jd_version": jd_version,
        }
        if existing:
            db.table("scraped_jobs").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            db.table("scraped_jobs").insert(payload).execute()
    except Exception:
        pass


def update_scraped_job_analysis(job_id, score, noc_verdict, skill_match,
                                verdict="", ats_score=None,
                                analysis_details=None, analysis_version=None,
                                profile_version=None):
    """Update a scraped job with analysis results."""
    db = _get_client()
    update_data = {
        "score": score,
        "noc_verdict": noc_verdict,
        "skill_match": skill_match,
        "ats_score": ats_score,
        "analysis_details": analysis_details or {},
        "analysis_version": analysis_version,
        "profile_version": profile_version,
        "analysis_stale": profile_version is None,
        "analyzed_at": datetime.now().isoformat() if analysis_version else None,
    }
    if verdict:
        update_data["verdict"] = verdict
    db.table("scraped_jobs").update(update_data).eq("id", job_id).execute()


def get_scraped_job(job_id):
    """Fetch one scraped job by id regardless of dismissed/applied flags."""
    try:
        db = _get_client()
        resp = db.table("scraped_jobs").select("*").eq("id", job_id).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def find_scraped_job_by_url(url):
    """Find a scraped job by its exact posting URL (any dismissed/applied state)."""
    if not url:
        return None
    try:
        db = _get_client()
        resp = db.table("scraped_jobs").select("id").eq("url", url).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def delete_scraped_job(job_id):
    """Permanently delete a scraped job (its job_messages cascade)."""
    try:
        db = _get_client()
        db.table("scraped_jobs").delete().eq("id", job_id).execute()
        return True
    except Exception as e:
        print(f"Failed to delete scraped job {job_id}: {e}")
        return False


def get_scraped_jobs(source=None):
    db = _get_client()
    query = db.table("scraped_jobs").select("*").eq("dismissed", 0).eq("applied", 0)
    if source:
        query = query.eq("source", source)
    resp = query.order("scraped_at", desc=True).execute()
    jobs = resp.data or []
    for job in jobs:
        job["screening_status"] = "pending"
        job["screening_reason"] = ""
    # Fetch current screening evidence in batches, not one request per card.
    by_id = {job["id"]: job for job in jobs}
    ids = list(by_id)
    for offset in range(0, len(ids), 200):
        messages = (db.table("job_messages")
                    .select("scraped_job_id,content,is_stale")
                    .eq("message_type", "screen")
                    .in_("scraped_job_id", ids[offset:offset + 200]).execute()).data or []
        for message in messages:
            job = by_id.get(message["scraped_job_id"])
            if job is None or message.get("is_stale"):
                continue
            tag, separator, reason = (message.get("content") or "").partition(":")
            status = tag.strip().lower()
            if separator and status in {"pass", "fail", "review"}:
                job["screening_status"] = status
                job["screening_reason"] = reason.strip()
    return pd.DataFrame(jobs)


# ===================== JOB MESSAGE FUNCTIONS =====================
# Outreach messages are written by the scheduled Claude routine, not by a
# hosted LLM call — see backend/modules/pending_messages.py.

DEFAULT_MESSAGE_TYPE = "cold_dm"
JOB_MESSAGE_TYPES = ("screen", "cold_dm", "resume_points", "demo_html")
REMOVED_MESSAGE_TYPES = {"evaluation"}


def get_job_message(scraped_job_id, message_type=DEFAULT_MESSAGE_TYPE):
    """Return the stored message row for a job, or None."""
    if message_type in REMOVED_MESSAGE_TYPES:
        return None
    try:
        db = _get_client()
        resp = (db.table("job_messages")
                .select("*")
                .eq("scraped_job_id", scraped_job_id)
                .eq("message_type", message_type)
                .execute())
        row = resp.data[0] if resp.data else None
        if row and row.get("is_stale"):
            return None
        return row
    except Exception:
        return None


def save_job_message(scraped_job_id, content, message_type=DEFAULT_MESSAGE_TYPE,
                     generated_by="claude-routine", profile_version=None):
    """Store (or replace) the message for a job. Returns True on success."""
    if message_type not in JOB_MESSAGE_TYPES:
        print(f"Unsupported job message type: {message_type}")
        return False
    # Last line of defence against a draft reused across jobs: it carries the
    # other job's demo link, which would send this employer someone else's demo.
    # Only this check here — the rest of the outreach rules stay in cmd_save,
    # so this cannot start rejecting drafts that save fine today.
    from outreach_quality import wrong_demo_links
    foreign = wrong_demo_links(content, scraped_job_id)
    if foreign:
        print(f"Rejected {message_type} for job {scraped_job_id}: "
              f"it links demo(s) {', '.join(foreign)} belonging to another job.")
        return False
    db = _get_client()
    try:
        profile_dependent = message_type in {
            "screen", "cold_dm", "resume_points"
        }
        if profile_version is None and profile_dependent:
            try:
                from profile import get_active_profile_snapshot
                profile_version = (get_active_profile_snapshot() or {}).get("version")
            except Exception:
                profile_version = None
        db.table("job_messages").upsert({
            "scraped_job_id": int(scraped_job_id),
            "message_type": message_type,
            "content": content,
            "generated_by": generated_by,
            "generated_at": datetime.now().isoformat(),
            "profile_version": profile_version,
            "is_stale": profile_dependent and profile_version is None,
        }, on_conflict="scraped_job_id,message_type").execute()
        return True
    except Exception as e:
        print(f"Failed to save job message for {scraped_job_id}: {e}")
        return False


def get_jobs_needing_messages(limit=20, message_type=DEFAULT_MESSAGE_TYPE):
    """Active jobs that have no stored message of this type yet, newest first.

    PostgREST has no NOT EXISTS, so the anti-join is done here: pull the
    candidate jobs, pull the ids that already have a message, subtract.
    """
    if message_type not in JOB_MESSAGE_TYPES:
        return []
    try:
        db = _get_client()
        jobs = (db.table("scraped_jobs")
                .select("id, title, company, location, url, description")
                .eq("dismissed", 0)
                .eq("applied", 0)
                .order("scraped_at", desc=True)
                .limit(max(limit * 5, limit))
                .execute()).data or []
        if not jobs:
            return []

        done = (db.table("job_messages")
                .select("scraped_job_id,profile_version,is_stale")
                .eq("message_type", message_type)
                .in_("scraped_job_id", [j["id"] for j in jobs])
                .execute()).data or []
        try:
            from profile import get_active_profile_snapshot
            active_version = (get_active_profile_snapshot() or {}).get("version")
        except Exception:
            active_version = None
        have = {r["scraped_job_id"] for r in done
                if not r.get("is_stale") and r.get("profile_version") == active_version}

        return [j for j in jobs if j["id"] not in have][:limit]
    except Exception as e:
        print(f"Failed to list jobs needing messages: {e}")
        return []


def get_current_cover_letter(scraped_job_id):
    """Return the newest non-outdated audited cover-letter draft."""
    try:
        rows = (_get_client().table("cover_letter_drafts").select("*")
                .eq("scraped_job_id", scraped_job_id).eq("is_outdated", False)
                .order("generated_at", desc=True).limit(1).execute()).data or []
        return rows[0] if rows else None
    except Exception:
        return None


def save_cover_letter_draft(job_id, resume_profile_id, resume_version, jd_version,
                             jd_hash, match_score, analysis_version, run_id, content,
                             rules_version="grounded-cover-letter-v1", generated_by="claude"):
    """Store an audited cover-letter draft. Any existing non-outdated draft for
    this job that doesn't match the new resume version/JD hash/rules version is
    marked outdated first. Idempotent on (job, resume_version, jd_hash, rules_version)."""
    db = _get_client()
    try:
        current = (db.table("cover_letter_drafts")
                   .select("id,resume_version,jd_hash,generation_rules_version")
                   .eq("scraped_job_id", job_id).eq("is_outdated", False).execute()).data or []
        stale_ids = [row["id"] for row in current
                     if row.get("resume_version") != resume_version
                     or row.get("jd_hash") != jd_hash
                     or row.get("generation_rules_version") != rules_version]
        if stale_ids:
            db.table("cover_letter_drafts").update({"is_outdated": True}).in_("id", stale_ids).execute()
        db.table("cover_letter_drafts").upsert({
            "scraped_job_id": job_id,
            "resume_profile_id": resume_profile_id,
            "resume_version": resume_version,
            "jd_version": jd_version,
            "jd_hash": jd_hash,
            "match_score": match_score,
            "analysis_version": analysis_version,
            "generation_rules_version": rules_version,
            "run_id": run_id,
            "content": content,
            "generated_by": generated_by,
            "is_outdated": False,
        }, on_conflict="scraped_job_id,resume_version,jd_hash,generation_rules_version",
           ignore_duplicates=True).execute()
        return True
    except Exception as e:
        print(f"Failed to save cover-letter draft for job {job_id}: {e}")
        return False


# ===================== MESSAGE REQUEST QUEUE =====================
# The UI queues freeform message requests here; the scheduled Claude routine
# writes them. No hosted LLM call is involved.

MESSAGE_REQUEST_TYPES = (
    "cold-dm", "follow-up", "cover-letter",
    "thank-you", "demo-outreach",
)


def create_message_request(message_type, params):
    """Queue a request. Returns the new row, or None on failure."""
    db = _get_client()
    try:
        resp = db.table("message_requests").insert({
            "message_type": message_type,
            "params": params or {},
            "status": "pending",
        }).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        print(f"Failed to queue message request: {e}")
        return None


def get_message_request(request_id):
    """Fetch a single queued request, or None."""
    try:
        db = _get_client()
        resp = (db.table("message_requests")
                .select("*")
                .eq("id", request_id)
                .execute())
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def get_message_requests(status=None, limit=50):
    """Recent requests, newest first, optionally filtered by status."""
    try:
        db = _get_client()
        query = db.table("message_requests").select("*")
        if status:
            query = query.eq("status", status)
        resp = query.order("created_at", desc=True).limit(limit).execute()
        return resp.data or []
    except Exception:
        return []


def complete_message_request(request_id, content):
    """Attach the written message and mark the request ready."""
    db = _get_client()
    try:
        db.table("message_requests").update({
            "status": "ready",
            "content": content,
            "error": None,
            "completed_at": datetime.now().isoformat(),
        }).eq("id", request_id).execute()
        return True
    except Exception as e:
        print(f"Failed to complete request {request_id}: {e}")
        return False


def get_follow_up_draft(application_id):
    """Latest auto-queued follow-up request for one tracked application.

    Returns the newest pending/ready 'follow-up' message_request whose params
    carry _application_id == application_id, or None.
    """
    try:
        db = _get_client()
        resp = (db.table("message_requests")
                .select("*")
                .eq("message_type", "follow-up")
                .order("created_at", desc=True)
                .limit(100)
                .execute())
        for r in resp.data or []:
            params = r.get("params") or {}
            if (params.get("_application_id") == application_id
                    and r.get("status") in ("pending", "ready")):
                return r
    except Exception:
        pass
    return None


def fail_message_request(request_id, error):
    """Mark a request failed so the UI stops showing it as pending forever."""
    db = _get_client()
    try:
        db.table("message_requests").update({
            "status": "failed",
            "error": str(error)[:500],
            "completed_at": datetime.now().isoformat(),
        }).eq("id", request_id).execute()
        return True
    except Exception:
        return False


def mark_scraped_job(job_id, action):
    db = _get_client()
    if action == 'applied':
        db.table("scraped_jobs").update({"applied": 1}).eq("id", job_id).execute()
    elif action == 'dismissed':
        db.table("scraped_jobs").update({"dismissed": 1}).eq("id", job_id).execute()
    elif action == 'keep':
        # Un-dismiss: make the job visible again. Used when the screener PASSes a
        # job that a prior FAIL had hidden, so a PASS always restores visibility.
        db.table("scraped_jobs").update({"dismissed": 0}).eq("id", job_id).execute()


# ===================== ANALYTICS FUNCTIONS =====================

def get_weekly_trend():
    """Get Monday-based application counts for the latest 12 active weeks."""
    from analytics import weekly_trend
    db = _get_client()
    resp = db.table("applications").select("date_applied, type").order("date_applied").execute()
    return pd.DataFrame(weekly_trend(resp.data))


def get_platform_effectiveness():
    """Positive responses / all persisted applications, grouped by platform."""
    from analytics import platform_effectiveness
    db = _get_client()
    resp = db.table("applications").select("platform, status").execute()
    return pd.DataFrame(platform_effectiveness(resp.data))


def get_status_funnel():
    """Get a lossless breakdown using current and legacy persisted statuses."""
    from analytics import status_breakdown
    db = _get_client()
    resp = db.table("applications").select("status").execute()
    return status_breakdown(resp.data)


def get_role_analysis():
    """Assign each persisted role once to an evidence-based role family."""
    from analytics import role_analysis
    db = _get_client()
    resp = db.table("applications").select("role, status").execute()
    return pd.DataFrame(role_analysis(resp.data))


# ===================== FOLLOW-UP HISTORY =====================


def log_follow_up(entity_type, entity_id, message_content="", channel=""):
    db = _get_client()
    # Determine follow_up_number from existing history
    resp = (db.table("follow_up_history")
            .select("id")
            .eq("entity_type", entity_type)
            .eq("entity_id", entity_id)
            .execute())
    follow_up_number = len(resp.data) + 1

    # Auto-mark all prior pending follow-ups as "no_response"
    (db.table("follow_up_history")
     .update({"follow_up_outcome": "no_response"})
     .eq("entity_type", entity_type)
     .eq("entity_id", entity_id)
     .eq("follow_up_outcome", "pending")
     .execute())

    db.table("follow_up_history").insert({
        "entity_type": entity_type,
        "entity_id": int(entity_id),
        "message_content": message_content,
        "channel": channel,
        "follow_up_number": follow_up_number,
        "follow_up_outcome": "pending",
    }).execute()
    return follow_up_number


def get_follow_up_history(entity_type, entity_id):
    db = _get_client()
    resp = (db.table("follow_up_history")
            .select("*")
            .eq("entity_type", entity_type)
            .eq("entity_id", entity_id)
            .order("sent_at", desc=True)
            .execute())
    return resp.data


def update_follow_up_outcome(history_id, outcome):
    db = _get_client()
    db.table("follow_up_history").update(
        {"follow_up_outcome": outcome}
    ).eq("id", history_id).execute()


def get_follow_up_effectiveness():
    db = _get_client()
    resp = db.table("follow_up_history").select("*").execute()
    rows = resp.data
    if not rows:
        return {
            "by_channel": [],
            "by_number": [],
            "overall": {"total": 0, "responded": 0, "rate": 0},
        }

    df = pd.DataFrame(rows)
    total = len(df)
    responded = len(df[df["follow_up_outcome"] == "responded"])
    overall_rate = round(responded / total * 100, 1) if total else 0

    by_channel = []
    for channel, group in df.groupby("channel"):
        ch_total = len(group)
        ch_responded = len(group[group["follow_up_outcome"] == "responded"])
        by_channel.append({
            "channel": channel,
            "total": ch_total,
            "responded": ch_responded,
            "rate": round(ch_responded / ch_total * 100, 1) if ch_total else 0,
        })

    by_number = []
    for num, group in df.groupby("follow_up_number"):
        n_total = len(group)
        n_responded = len(group[group["follow_up_outcome"] == "responded"])
        by_number.append({
            "follow_up_number": int(num),
            "total": n_total,
            "responded": n_responded,
            "rate": round(n_responded / n_total * 100, 1) if n_total else 0,
        })

    return {
        "by_channel": by_channel,
        "by_number": sorted(by_number, key=lambda x: x["follow_up_number"]),
        "overall": {"total": total, "responded": responded, "rate": overall_rate},
    }


# ===================== COMPANY RESEARCH CACHE =====================

def get_cached_research(company_name):
    db = _get_client()
    resp = (db.table("company_research_cache")
            .select("*")
            .ilike("company_name", company_name)
            .execute())
    if not resp.data:
        return None
    row = resp.data[0]
    # Check staleness (14 days)
    researched = row.get("researched_at", "")
    if researched:
        try:
            from dateutil.parser import parse as parse_dt
            dt = parse_dt(researched)
            if (datetime.now(dt.tzinfo) - dt).days > 14:
                return None  # stale
        except Exception:
            pass
    return row


def save_research_cache(company_name, research_data):
    db = _get_client()
    db.table("company_research_cache").upsert({
        "company_name": company_name,
        "hiring_contact_name": research_data.get("hiring_contact", {}).get("name", ""),
        "hiring_contact_title": research_data.get("hiring_contact", {}).get("title", ""),
        "hiring_contact_linkedin": research_data.get("hiring_contact", {}).get("linkedin_url", ""),
        "product_url": research_data.get("product_url", ""),
        # Stamp every save, not just the first. The column default only fires on
        # insert, so without this an upsert left the old date: a row went stale
        # at 14 days and stayed stale however often it was researched again,
        # and "how long since we last looked" could not be asked at all.
        "researched_at": _user_now().isoformat(),
    }, on_conflict="company_name").execute()


# ===================== MINI DEMO FUNCTIONS =====================

def add_mini_demo(company, role, demo_idea):
    db = _get_client()
    try:
        db.table("mini_demos").insert({
            "company": company,
            "role": role,
            "demo_idea": demo_idea,
            "status": "Idea",
            "hours_spent": 0,
        }).execute()
    except Exception:
        raise RuntimeError("mini_demos table not found. Create it in Supabase first.")


def update_mini_demo(demo_id, **kwargs):
    """Update mini demo fields. Pass any of: status, github_url, demo_url, hours_spent, result."""
    db = _get_client()
    allowed = {"status", "github_url", "demo_url", "hours_spent", "result"}
    update_data = {k: v for k, v in kwargs.items() if k in allowed}
    if update_data:
        try:
            db.table("mini_demos").update(update_data).eq("id", demo_id).execute()
        except Exception:
            raise RuntimeError("mini_demos table not found. Create it in Supabase first.")


def get_active_demos():
    try:
        db = _get_client()
        resp = (db.table("mini_demos")
                .select("*")
                .in_("status", ["Idea", "Building", "Deployed"])
                .order("created_at", desc=True)
                .execute())
        return pd.DataFrame(resp.data)
    except Exception:
        return pd.DataFrame()


def get_demo_results():
    try:
        db = _get_client()
        resp = db.table("mini_demos").select("*").order("created_at", desc=True).execute()
        return pd.DataFrame(resp.data)
    except Exception:
        return pd.DataFrame()


# ===================== NOTIFICATION FUNCTIONS =====================

def init_notifications_table():
    """Create the notifications table if it doesn't exist."""
    try:
        db = _get_client()
        db.rpc("exec_sql", {
            "query": """
                CREATE TABLE IF NOT EXISTS notifications (
                    id BIGSERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'job_alert',
                    metadata JSONB DEFAULT '{}',
                    is_read BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_is_read
                    ON notifications(is_read);
                CREATE INDEX IF NOT EXISTS idx_notifications_created_at
                    ON notifications(created_at DESC);
            """
        }).execute()
    except Exception as e:
        # Table may already exist or RPC not available — that's fine
        print(f"  Note: init_notifications_table: {e}")


def save_notification(title, body, notification_type="job_alert", metadata=None):
    """Save an in-app notification to Supabase."""
    db = _get_client()
    import json
    try:
        db.table("notifications").insert({
            "title": title,
            "body": body,
            "type": notification_type,
            "metadata": json.dumps(metadata or {}),
        }).execute()
    except Exception as e:
        print(f"Failed to save notification: {e}")


def get_notifications(unread_only=False, limit=50):
    """Get notifications, newest first."""
    try:
        db = _get_client()
        query = db.table("notifications").select("*")
        if unread_only:
            query = query.eq("is_read", False)
        resp = query.order("created_at", desc=True).limit(limit).execute()
        return resp.data or []
    except Exception:
        return []


def get_unread_count():
    """Get count of unread notifications."""
    try:
        db = _get_client()
        resp = (db.table("notifications")
                .select("id", count="exact")
                .eq("is_read", False)
                .execute())
        return resp.count or 0
    except Exception:
        return 0


def mark_notification_read(notification_id):
    """Mark a single notification as read."""
    db = _get_client()
    db.table("notifications").update({"is_read": True}).eq("id", notification_id).execute()


def mark_all_notifications_read():
    """Mark all notifications as read."""
    db = _get_client()
    db.table("notifications").update({"is_read": True}).eq("is_read", False).execute()


# ===================== PUSH SUBSCRIPTION FUNCTIONS =====================

def save_push_subscription(endpoint, keys_p256dh, keys_auth):
    """Save or update a push subscription."""
    db = _get_client()
    try:
        db.table("push_subscriptions").upsert({
            "endpoint": endpoint,
            "keys_p256dh": keys_p256dh,
            "keys_auth": keys_auth,
        }, on_conflict="endpoint").execute()
    except Exception as e:
        print(f"Failed to save push subscription: {e}")


def delete_push_subscription(endpoint):
    """Remove a push subscription."""
    db = _get_client()
    try:
        db.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()
    except Exception as e:
        print(f"Failed to delete push subscription: {e}")


def get_all_push_subscriptions():
    """Get all stored push subscriptions."""
    try:
        db = _get_client()
        resp = db.table("push_subscriptions").select("*").execute()
        return resp.data or []
    except Exception:
        return []


def send_push_notifications(title, body, url="/dashboard"):
    """Send a web push notification to all subscribed devices."""
    import json
    import os
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("pywebpush not installed -- skipping push notifications.")
        return

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    vapid_claims_email = os.environ.get("VAPID_CLAIM_EMAIL", "")

    if not vapid_private_key or not vapid_claims_email:
        print("VAPID keys not configured -- skipping push notifications.")
        return

    subscriptions = get_all_push_subscriptions()
    if not subscriptions:
        print("No push subscriptions found -- skipping push.")
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
    })

    sent = 0
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {
                "p256dh": sub["keys_p256dh"],
                "auth": sub["keys_auth"],
            },
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_claims_email},
            )
            sent += 1
        except WebPushException as e:
            print(f"Push failed for {sub['endpoint'][:50]}...: {e}")
            status = getattr(e.response, 'status_code', None) if e.response else None
            err_str = str(e)
            should_remove = (
                status in (400, 404, 410)
                or "400" in err_str or "404" in err_str or "410" in err_str
            )
            if should_remove:
                delete_push_subscription(sub["endpoint"])
                print("  Removed stale subscription.")
        except Exception as e:
            print(f"Push error: {e}")

    print(f"Push notifications sent to {sent} device(s).")
