"""
Outreach messages for scraped jobs, written by the scheduled Claude routine.

There is no hosted LLM call here. The routine session *is* Claude: it lists the
jobs that still need a message, writes each one itself, and saves it back. This
script is the interface it drives.

    # what still needs a message (JSON: profile + jobs)
    python pending_messages.py list --limit 10

    # save one message (body on stdin avoids shell-quoting multi-line text)
    python pending_messages.py save --job-id 4821 < message.txt

    # one-time Company HR email drafts for newly tracked jobs
    python pending_messages.py list --type hr_email --limit 10
    python pending_messages.py save --type hr_email --job-id 4821 < email.txt

    # freeform requests queued from the UI, each with its ready-made prompt
    python pending_messages.py requests
    python pending_messages.py fulfil --request-id 12 < message.txt

    # audited cover-letter drafts for explicitly eligible, high-match jobs
    python pending_messages.py cover-letter-list --limit 10
    python pending_messages.py save-cover-letter --job-id 4821 < letter.txt

    # end-of-run summary: in-app notification + web push to the installed PWA
    python pending_messages.py notify --title "Hourly run" --body "3 jobs, 3 DMs"

Requires SUPABASE_URL and SUPABASE_KEY. No LLM key of any kind.
"""

import argparse
import json
import os
import sys

from tracker import (
    DEFAULT_MESSAGE_TYPE,
    JOB_MESSAGE_TYPES,
    complete_message_request,
    fail_message_request,
    get_job_message,
    get_message_request,
    get_message_requests,
    save_job_message,
)
from tracker import save_notification, send_push_notifications


def _profile_text():
    """Profile context from the reviewed active PDF; empty if unavailable."""
    try:
        from message_generator import _get_profile_text
        return _get_profile_text()
    except Exception:
        return ""


def _tracked_jobs_missing(message_type, limit):
    """Tracker-logged scraped jobs lacking a stored message of this type.

    Content is generated ONLY for jobs the user logged to the tracker (an
    application row whose URL matches a scraped job, terminal statuses
    excluded). Returns (jobs, tracked_total).

    Soonest-due first: every run is capped, so the order decides what actually
    gets written. Sorting by job id wrote the newest jobs first and left the
    oldest — the ones whose outreach date has already arrived — until last, so
    a job could come due with no draft to send. The follow-up date is the
    cadence's own clock, which is what the cap should spend itself on.
    """
    from tracker import TERMINAL_STATUSES, _get_client, get_job_message

    db = _get_client()
    apps = (db.table("applications").select("url,status,follow_up_date")
            .execute()).data or []
    urls = [a["url"] for a in apps
            if (a.get("url") or "").strip()
            and a.get("status") not in TERMINAL_STATUSES]

    # follow_up_date is a "YYYY-MM-DD" string, so it sorts lexicographically.
    # A row without one goes last rather than jumping the queue.
    due_by_url = {(a.get("url") or "").strip(): str(a.get("follow_up_date") or "")
                  for a in apps if (a.get("url") or "").strip()}

    tracked = []
    for i in range(0, len(urls), 100):
        resp = (db.table("scraped_jobs")
                .select("id, title, company, location, url, description")
                .in_("url", urls[i:i + 100])
                .execute())
        tracked.extend(resp.data or [])

    def _soonest_due_first(row):
        return (due_by_url.get(row.get("url") or "") or "9999-12-31",
                -int(row["id"]))

    need = []
    for r in sorted(tracked, key=_soonest_due_first):
        if len(need) >= limit:
            break
        if get_job_message(r["id"], message_type=message_type):
            continue
        if message_type in {"cold_dm", "hr_email"}:
            # Both outreach assets carry the job's own live mini demo, so
            # neither is written before that demo exists. A note drafted early
            # keeps its missing link for good: this list only offers jobs with
            # no draft at all, so nothing ever comes back to add the link.
            if not get_job_message(r["id"], message_type="demo_html"):
                continue
        if message_type == "hr_email":
            api_base = os.environ.get(
                "PUBLIC_API_URL", "https://uav-6qe7.vercel.app"
            ).rstrip("/")
            r["demo_url"] = f"{api_base}/api/demo/{r['id']}"
            r["resume_attachment"] = (
                "Attach the latest PDF from Settings; do not put a resume URL "
                "inside the email body."
            )
        r["description"] = (r.get("description") or "").strip()
        need.append(r)
    return need, len(tracked)


def _cached_hiring_email(company_name):
    """The hiring address company research cached for this employer, if any."""
    try:
        from tracker import get_cached_research
        row = get_cached_research(company_name) or {}
        return (row.get("hiring_email") or "").strip().lower()
    except Exception:
        return ""


def _company_intel_text(company_name):
    """One-line company context from the research cache, or empty."""
    try:
        from tracker import get_cached_research
        row = get_cached_research(company_name)
        if not row:
            return ""
        parts = []
        if row.get("product_url"):
            parts.append(f"Website: {row['product_url']}")
        if row.get("hiring_contact_name"):
            title = row.get("hiring_contact_title") or ""
            parts.append(f"Hiring contact: {row['hiring_contact_name']}"
                         + (f" ({title})" if title else ""))
        if row.get("hiring_email"):
            source = row.get("hiring_email_source") or "source not recorded"
            parts.append(f"Published hiring email: {row['hiring_email']} (from {source})")
        return "; ".join(parts)
    except Exception:
        return ""


def _demo_url_for_job(job_id):
    """Return the live demo URL if a demo exists for this job, else empty."""
    try:
        from tracker import get_job_message
        if get_job_message(job_id, message_type="demo_html"):
            api_base = os.environ.get(
                "PUBLIC_API_URL", "https://uav-6qe7.vercel.app"
            ).rstrip("/")
            return f"{api_base}/api/demo/{job_id}"
    except Exception:
        pass
    return ""


def _demo_url_for_scraped_job_by_app_url(app_url):
    """Look up the scraped job by application URL and return its demo URL."""
    if not app_url:
        return ""
    try:
        from tracker import _get_client, get_job_message
        db = _get_client()
        resp = (db.table("scraped_jobs")
                .select("id")
                .eq("url", app_url.strip())
                .limit(1)
                .execute())
        if resp.data:
            return _demo_url_for_job(resp.data[0]["id"])
    except Exception:
        pass
    return ""


def cmd_list(args):
    jobs, tracked_total = _tracked_jobs_missing(args.type, args.limit)
    profile = _profile_text()
    from message_generator import build_cold_dm_prompt, build_hr_email_prompt
    for job in jobs:
        if args.type in {"cold_dm", "hr_email"} and not profile.strip():
            job["draft_spec"] = {"error": "Active verified PDF profile unavailable; do not draft."}
            continue
        if args.type == "cold_dm":
            job["draft_spec"] = build_cold_dm_prompt(
                job["company"], job["title"], job["description"],
                profile_text=profile,
                demo_url=_demo_url_for_job(job["id"]),
                company_intel=_company_intel_text(job["company"]))
        elif args.type == "hr_email":
            from email_finder import extract_published_emails
            # Company research already looked for a published hiring address and
            # cached it, so that comes first; the posting's own text backs it up.
            cached = _cached_hiring_email(job["company"])
            published = ([cached] if cached else []) + [
                e for e in extract_published_emails(job["description"]) if e != cached
            ]
            job["published_emails"] = published
            job["draft_spec"] = build_hr_email_prompt(
                job["company"], job["title"], job["description"], job["demo_url"], profile,
                published_emails=published)
    json.dump(
        {
            "message_type": args.type,
            "tracked_jobs_total": tracked_total,
            "profile": profile,
            "jobs": jobs,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


def cmd_save(args):
    content = (args.content if args.content is not None else sys.stdin.read()).strip()
    if args.type in {"cold_dm", "hr_email"}:
        from outreach_quality import validate_outreach_draft
        problem = validate_outreach_draft(args.type, content, args.job_id)
        if problem:
            print(problem, file=sys.stderr)
            return 1
    if not content:
        print("Refusing to save an empty message.", file=sys.stderr)
        return 1

    if args.type == "cold_dm":
        # The note is the follow-up on an application: it says so, and links
        # this job's demo whenever one exists.
        if "applied" not in content.lower():
            print('Cold DM must open "Hi, I recently applied for the <Role> role at '
                  '<Company>." — follow up on the application.', file=sys.stderr)
            return 1
        demo_url = _demo_url_for_job(args.job_id)
        if demo_url and demo_url not in content:
            print(f"Cold DM must include this job's demo link: {demo_url}", file=sys.stderr)
            return 1

    if args.type == "hr_email":
        from tracker import is_scraped_job_tracked

        if not is_scraped_job_tracked(args.job_id):
            print("HR email drafts are allowed only for active tracker jobs.", file=sys.stderr)
            return 1
        if not get_job_message(args.job_id, message_type="demo_html"):
            print("Build the mini demo before saving the HR email draft.", file=sys.stderr)
            return 1
        api_base = os.environ.get(
            "PUBLIC_API_URL", "https://uav-6qe7.vercel.app"
        ).rstrip("/")
        expected_demo_url = f"{api_base}/api/demo/{args.job_id}"
        if expected_demo_url not in content:
            print("HR email draft must include this job's mini demo link.", file=sys.stderr)
            return 1
        lower = content.lower()
        if "resume" not in lower or "attach" not in lower:
            print("HR email draft must state that the resume is attached.", file=sys.stderr)
            return 1
        if len(content.split()) > 150:
            print("HR email draft is too long (maximum 150 words including headers).", file=sys.stderr)
            return 1

    ok = save_job_message(args.job_id, content, message_type=args.type)
    if not ok:
        return 1

    saved = get_job_message(args.job_id, message_type=args.type)
    if not saved:
        print(f"Save reported success but job {args.job_id} has no row.", file=sys.stderr)
        return 1

    if args.type == "screen":
        from tracker import mark_scraped_job
        tag = content.split(":", 1)[0].strip().upper()
        if tag in {"PASS", "REVIEW"}:
            mark_scraped_job(args.job_id, "keep")
        elif tag == "FAIL":
            mark_scraped_job(args.job_id, "dismissed")

    print(f"Saved {args.type} for job {args.job_id} ({len(content)} chars).")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="jobs with no stored message yet")
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--type", default=DEFAULT_MESSAGE_TYPE, choices=JOB_MESSAGE_TYPES)
    p_list.set_defaults(func=cmd_list)

    p_save = sub.add_parser("save", help="store a message for one job")
    p_save.add_argument("--job-id", type=int, required=True)
    p_save.add_argument("--type", default=DEFAULT_MESSAGE_TYPE, choices=JOB_MESSAGE_TYPES)
    p_save.add_argument("--content", default=None,
                        help="message text; omit to read from stdin")
    p_save.set_defaults(func=cmd_save)

    p_fup = sub.add_parser(
        "followups",
        help="queue follow-up requests for tracked applications now due",
    )
    p_fup.set_defaults(func=cmd_followups)

    p_req = sub.add_parser("requests", help="pending UI requests, with prompts")
    p_req.add_argument("--limit", type=int, default=20)
    p_req.set_defaults(func=cmd_requests)

    p_ful = sub.add_parser("fulfil", help="answer one queued request")
    p_ful.add_argument("--request-id", type=int, required=True)
    p_ful.add_argument("--content", default=None,
                       help="message text; omit to read from stdin")
    p_ful.set_defaults(func=cmd_fulfil)

    p_fail = sub.add_parser("fail", help="mark a request failed")
    p_fail.add_argument("--request-id", type=int, required=True)
    p_fail.add_argument("--error", required=True)
    p_fail.set_defaults(func=cmd_fail)

    p_dem = sub.add_parser(
        "demos",
        help="tracker-logged jobs still lacking a live demo",
    )
    p_dem.add_argument("--limit", type=int, default=4)
    p_dem.set_defaults(func=cmd_demos)

    p_cos = sub.add_parser(
        "companies",
        help="recent-job companies with no fresh intel cached",
    )
    p_cos.add_argument("--limit", type=int, default=10)
    p_cos.set_defaults(func=cmd_companies)

    p_sco = sub.add_parser("save-company", help="store company intel (JSON on stdin)")
    p_sco.add_argument("--name", required=True)
    p_sco.set_defaults(func=cmd_save_company)

    p_int = sub.add_parser("intel", help="print cached company intel as JSON")
    p_int.add_argument("--name", required=True)
    p_int.set_defaults(func=cmd_intel)

    p_scrl = sub.add_parser("screen-list", help="scraped jobs not yet resume-screened")
    p_scrl.add_argument("--limit", type=int, default=100)
    p_scrl.set_defaults(func=cmd_screen_list)

    p_scr = sub.add_parser("screen", help="record a resume-screening decision")
    p_scr.add_argument("--job-id", type=int, required=True)
    p_scr.add_argument("--decision", choices=["pass", "fail", "review"], required=True)
    p_scr.add_argument("--reason", default="")
    p_scr.set_defaults(func=cmd_screen)

    p_cll = sub.add_parser(
        "cover-letter-list",
        help="explicitly-eligible high-match jobs needing an audited cover-letter draft",
    )
    p_cll.add_argument("--limit", type=int, default=10)
    p_cll.add_argument("--threshold", type=int, default=90)
    p_cll.set_defaults(func=cmd_cover_letter_list)

    p_cls = sub.add_parser("save-cover-letter", help="store an audited cover-letter draft for one job")
    p_cls.add_argument("--job-id", type=int, required=True)
    p_cls.add_argument("--threshold", type=int, default=90)
    p_cls.add_argument("--run-id", default=None)
    p_cls.add_argument("--content", default=None,
                       help="cover-letter text; omit to read from stdin")
    p_cls.set_defaults(func=cmd_save_cover_letter)

    p_not = sub.add_parser("notify", help="in-app notification + web push")
    p_not.add_argument("--title", required=True)
    p_not.add_argument("--body", required=True)
    p_not.add_argument("--url", default="/tonight")
    p_not.set_defaults(func=cmd_notify)

    args = parser.parse_args()
    sys.exit(args.func(args))


def _build_prompt(message_type, params):
    """Render the stored request into the prompt the app would have sent."""
    from message_generator import PROMPT_BUILDERS

    builder = PROMPT_BUILDERS.get(message_type)
    if builder is None:
        return {"error": f"unknown message_type {message_type!r}"}
    try:
        # Keys starting with "_" are request metadata (e.g. _application_id
        # linking an auto-queued follow-up to its tracker row), not builder args.
        clean = {k: v for k, v in (params or {}).items() if not k.startswith("_")}
        return builder(**clean)
    except TypeError as e:
        return {"error": f"params do not match {message_type} builder: {e}"}


def cmd_requests(args):
    rows = get_message_requests(status="pending", limit=args.limit)
    out = []
    for r in rows:
        spec = _build_prompt(r["message_type"], r.get("params"))
        out.append({
            "request_id": r["id"],
            "message_type": r["message_type"],
            "created_at": r.get("created_at"),
            **spec,
        })
    json.dump({"pending": out}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_fulfil(args):
    row = get_message_request(args.request_id)
    if not row:
        print(f"No request {args.request_id}.", file=sys.stderr)
        return 1

    content = (args.content if args.content is not None else sys.stdin.read()).strip()
    if not content:
        print("Refusing to save an empty message.", file=sys.stderr)
        return 1

    # Apply the same sentence-boundary trim the old inline generator used.
    from message_generator import enforce_char_limit
    spec = _build_prompt(row["message_type"], row.get("params"))
    if row["message_type"] == "cold-dm":
        from outreach_quality import validate_outreach_draft
        problem = validate_outreach_draft(row["message_type"], content)
        if problem:
            print(problem, file=sys.stderr)
            return 1
    content = enforce_char_limit(content, spec.get("char_limit"))

    if not complete_message_request(args.request_id, content):
        return 1
    print(f"Request {args.request_id} ({row['message_type']}) ready — {len(content)} chars.")
    return 0


def cmd_followups(args):
    """Queue a follow-up request for each tracked application whose
    follow_up_date has arrived.

    One request per application per follow-up round: an application already
    holding a pending/ready request for its current round is skipped, and
    rounds stop after the third follow-up (the builder's final-tone cap).
    The queued requests are then written by the normal `requests`/`fulfil`
    flow in the same routine run.
    """
    from datetime import datetime

    from tracker import (
        create_message_request,
        get_follow_up_history,
        get_post_connection_follow_ups_due,
        _user_now,
    )

    df = get_post_connection_follow_ups_due()
    apps = [] if df is None or df.empty else df.to_dict("records")

    existing = get_message_requests(limit=200)
    already = set()
    for r in existing:
        p = r.get("params") or {}
        if (
            r.get("message_type") == "follow-up"
            and r.get("status") in ("pending", "ready")
            and p.get("_application_id")
        ):
            already.add((p["_application_id"], p.get("follow_up_number", 1)))

    queued, skipped = [], 0
    for app in apps:
        history = get_follow_up_history("application", app["id"]) or []
        number = len(history) + 1
        if number > 3 or (app["id"], number) in already:
            skipped += 1
            continue

        days = 7
        try:
            applied = datetime.strptime(
                str(app.get("date_applied", ""))[:10], "%Y-%m-%d"
            ).date()
            days = max((_user_now().date() - applied).days, 1)
        except (ValueError, TypeError):
            pass

        previous = [
            h.get("message_content", "")
            for h in sorted(history, key=lambda h: h.get("sent_at") or "")
        ][-3:]

        company = app.get("company", "")
        demo = _demo_url_for_scraped_job_by_app_url(app.get("url", ""))
        intel = _company_intel_text(company)
        # The follow-up goes out by Gmail, so it needs an address the research
        # cache already evidenced; without one the draft keeps the unknown marker.
        recipient = _cached_hiring_email(company)

        row = create_message_request("follow-up", {
            "company_name": company,
            "role_title": app.get("role", ""),
            "days_since_applied": days,
            "original_platform": app.get("platform") or "LinkedIn",
            "follow_up_number": number,
            "previous_messages": [m for m in previous if m],
            "demo_url": demo,
            "company_intel": intel,
            "recipient_email": recipient,
            "_application_id": app["id"],
        })
        if row:
            queued.append({
                "request_id": row["id"],
                "application_id": app["id"],
                "company": app.get("company", ""),
                "role": app.get("role", ""),
                "follow_up_number": number,
            })

    json.dump({"queued": queued, "skipped": skipped}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_demos(args):
    """List tracker-logged jobs that still lack a live demo.

    Every tracked job eventually gets a demo; --limit just bounds one
    firing's batch.
    """
    jobs, tracked_total = _tracked_jobs_missing("demo_html", args.limit)
    need = [{
        "job_id": r["id"],
        "title": r.get("title", ""),
        "company": r.get("company", ""),
        "description": r.get("description", ""),
    } for r in jobs]

    json.dump(
        {
            "profile": _profile_text(),
            "tracked_jobs_total": tracked_total,
            "jobs_needing_demos": need,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


# A company with a website but no published hiring email is worth another look,
# but not on every run: some employers publish no address at all, and the agent
# is right not to invent one.
EMAIL_RETRY_DAYS = 7


def _research_age_days(researched_at, now):
    """Days since this company was last researched, or None if unknown."""
    from datetime import datetime

    if not researched_at:
        return None
    try:
        moment = datetime.fromisoformat(str(researched_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return (now - moment).days


def cmd_companies(args):
    """List companies still needing research — above all, a hiring email.

    Two pools, because they fail differently:

    * Newly scraped jobs, which usually have no cache row at all.
    * Companies whose HR email is due, which is when a missing address actually
      costs a send. These were unreachable: candidates came only from jobs
      scraped in the last two days, and the email is not due until day 8, so a
      company aged out of the pool roughly six days before anyone needed its
      address.

    A row holding a website but no hiring_email used to count as finished,
    because eligibility asked whether product_url was missing. The address is
    the point of the pass, so it now counts as unfinished. A company that
    publishes nothing would otherwise be retried every run, so a re-check waits
    EMAIL_RETRY_DAYS, and companies never researched at all are offered first.
    """
    from datetime import timedelta

    from tracker import _get_client, _user_now, get_hr_email_todos

    db = _get_client()
    now = _user_now()
    since = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    resp = (db.table("scraped_jobs").select("company")
            .gte("scraped_at", since)
            .eq("dismissed", 0)
            .execute())
    companies = {(r.get("company") or "").strip()
                 for r in (resp.data or []) if (r.get("company") or "").strip()}

    # The companies whose email is actually due. A research failure only shows
    # up here, so this is the pool that has to be covered.
    try:
        due = get_hr_email_todos()
        if not getattr(due, "empty", True):
            companies.update(
                name for name in (str(row.get("company") or "").strip()
                                  for row in due.to_dict("records")) if name)
    except Exception as exc:  # a tracker read must not lose the scraped pool
        print(f"[pending-messages] could not read HR email todos: {exc}", file=sys.stderr)

    # One read for every company, rather than a query each.
    cache = {}
    for row in (db.table("company_research_cache")
                .select("company_name,product_url,hiring_email,researched_at")
                .execute()).data or []:
        cache[(row.get("company_name") or "").strip().casefold()] = row

    never, retry = [], []
    for name in sorted(companies):
        row = cache.get(name.casefold())
        if row is None or not (row.get("product_url") or "").strip():
            never.append(name)
            continue
        if (row.get("hiring_email") or "").strip():
            continue
        age = _research_age_days(row.get("researched_at"), now)
        if age is None or age >= EMAIL_RETRY_DAYS:
            retry.append((age if age is not None else 10 ** 6, name))

    # Never-researched first; then the longest-untouched missing address.
    ordered = never + [name for _, name in sorted(retry, reverse=True)]
    json.dump({"companies_needing_intel": ordered[:args.limit]}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_intel(args):
    """Print the cached website and hiring contact for downstream outreach.

    Prints {"found": false} when no fresh cache entry exists.
    """
    from tracker import get_cached_research

    row = get_cached_research(args.name)
    if not row:
        print(json.dumps({"found": False}))
        return 0
    print(json.dumps({"found": True, **row}, default=str))
    return 0


def cmd_screen_list(args):
    """Visible scraped jobs not yet screened against the resume, newest first,
    with the candidate's profile. Claude reads each and decides fit + level."""
    from tracker import get_scraped_jobs, get_job_message

    df = get_scraped_jobs()
    rows = df.to_dict("records") if not df.empty else []
    need = []
    for r in rows:
        if len(need) >= args.limit:
            break
        if get_job_message(r["id"], message_type="screen"):
            continue
        need.append({
            "id": r["id"],
            "title": r.get("title", ""),
            "company": r.get("company", ""),
            "location": r.get("location", ""),
            "url": r.get("url", ""),
            "description": (r.get("description") or "").strip(),
        })
    json.dump({"profile": _profile_text(), "jobs": need}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_cover_letter_list(args):
    """High-match, explicitly-eligible jobs that passed resume screening and
    still need (or need a refreshed) audited cover-letter draft, each with a
    ready-to-use prompt. Claude drafts it; `save-cover-letter` records it."""
    from tracker import get_scraped_jobs, get_job_message, get_current_cover_letter
    from profile import get_active_profile_snapshot
    from message_generator import build_cover_letter_prompt

    profile_snapshot = get_active_profile_snapshot()
    if not profile_snapshot:
        json.dump({"profile": "", "resume_version": None, "cover_letters": []}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    profile_version = profile_snapshot.get("version")
    profile_text = profile_snapshot.get("raw_text", "")

    df = get_scraped_jobs()
    rows = df.to_dict("records") if not df.empty else []
    out = []
    for r in rows:
        if len(out) >= args.limit:
            break
        score = r.get("ats_score")
        if type(score) is not int or score < args.threshold:
            continue
        if r.get("analysis_stale") or r.get("profile_version") != profile_version:
            continue
        details = r.get("analysis_details") or {}
        if (details.get("mandatory_eligibility") or {}).get("overall") != "passed":
            continue
        screen = get_job_message(r["id"], message_type="screen")
        if not screen or not (screen.get("content") or "").startswith("PASS:"):
            continue
        current = get_current_cover_letter(r["id"])
        if (current and current.get("resume_version") == profile_version
                and current.get("jd_hash") == r.get("jd_hash")):
            continue
        jd = (r.get("description") or "").strip()
        spec = build_cover_letter_prompt(r.get("company") or "Unknown", r.get("title") or "Role",
                                          jd, profile_text=profile_text)
        out.append({
            "job_id": r["id"], "company": r.get("company", ""), "title": r.get("title", ""),
            "match_score": score, **spec,
        })
    json.dump({"profile": profile_text, "resume_version": profile_version,
               "threshold": args.threshold, "cover_letters": out}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_save_cover_letter(args):
    """Store an audited cover-letter draft for one eligible job. Re-derives
    the resume/JD provenance from the live row rather than trusting the
    caller, so a draft can never be recorded against a stale snapshot."""
    from datetime import datetime, timezone
    from tracker import get_scraped_job, get_job_message, save_cover_letter_draft
    from profile import get_active_profile_snapshot

    content = (args.content if args.content is not None else sys.stdin.read()).strip()
    if not content:
        print("Refusing to save an empty cover letter.", file=sys.stderr)
        return 1

    job = get_scraped_job(args.job_id)
    if not job:
        print(f"No scraped job {args.job_id}.", file=sys.stderr)
        return 1

    profile_snapshot = get_active_profile_snapshot()
    if not profile_snapshot:
        print("No active resume profile.", file=sys.stderr)
        return 1

    score = job.get("ats_score")
    if type(score) is not int or score < args.threshold:
        print(f"Job {args.job_id} does not meet the cover-letter threshold "
              f"({score} < {args.threshold}).", file=sys.stderr)
        return 1
    if job.get("analysis_stale") or job.get("profile_version") != profile_snapshot.get("version"):
        print(f"Job {args.job_id}'s analysis is stale against the active profile; "
              "re-screen before drafting.", file=sys.stderr)
        return 1
    details = job.get("analysis_details") or {}
    if (details.get("mandatory_eligibility") or {}).get("overall") != "passed":
        print(f"Job {args.job_id} is not explicitly eligible.", file=sys.stderr)
        return 1
    screen = get_job_message(args.job_id, message_type="screen")
    if not screen or not (screen.get("content") or "").startswith("PASS:"):
        print(f"Job {args.job_id} has not passed resume screening.", file=sys.stderr)
        return 1

    run_id = args.run_id or datetime.now(timezone.utc).strftime("claude-%Y%m%dT%H%M%SZ")
    ok = save_cover_letter_draft(
        job_id=args.job_id,
        resume_profile_id=profile_snapshot["id"],
        resume_version=profile_snapshot["version"],
        jd_version=job.get("jd_version") or 1,
        jd_hash=job.get("jd_hash"),
        match_score=score,
        analysis_version=job.get("analysis_version"),
        run_id=run_id,
        content=content,
    )
    if not ok:
        return 1
    print(f"Saved cover-letter draft for job {args.job_id} ({len(content)} chars).")
    return 0


def cmd_screen(args):
    """Record a resume-screening decision for one scraped job. A 'fail'
    dismisses the job (hidden everywhere, kept for URL dedup); either way the
    decision is stored as a 'screen' message so it is not re-screened."""
    from tracker import save_job_message, mark_scraped_job

    decision = args.decision
    if decision not in {"pass", "fail", "review"}:
        print("Invalid screening decision.", file=sys.stderr)
        return 1
    tag = decision.upper() + ": " + (args.reason or "")
    if not save_job_message(args.job_id, tag.strip(), message_type="screen"):
        return 1
    if decision == "fail":
        mark_scraped_job(args.job_id, "dismissed")
    else:
        # PASS and REVIEW restore visibility even if a prior FAIL hid the job
        # (re-screens across rule changes), so explicitly un-dismiss.
        mark_scraped_job(args.job_id, "keep")
    print(f"Screened job {args.job_id}: {decision}. {args.reason or ''}".strip())
    return 0


def cmd_save_company(args):
    """Save a company website/contact from stdin JSON into the cache.

    Expected JSON: {"product_url": "https://company.example",
                    "hiring_contact": {"name": str, "title": str,
                                       "linkedin_url": str}}
    """
    from tracker import save_research_cache

    raw = sys.stdin.read().strip()
    if not raw:
        print("Refusing to save empty intel.", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"stdin is not valid JSON: {e}", file=sys.stderr)
        return 1
    product_url = (data.get("product_url") or "").strip()
    contact = data.get("hiring_contact") or {}
    if not isinstance(contact, dict):
        print("hiring_contact must be a JSON object.", file=sys.stderr)
        return 1
    if product_url and not product_url.startswith(("https://", "http://")):
        print("Company website must be an HTTP(S) URL.", file=sys.stderr)
        return 1
    if not product_url and not (contact.get("name") or "").strip():
        print("A company website or verified hiring contact is required.", file=sys.stderr)
        return 1

    save_research_cache(args.name, {
        "product_url": product_url,
        "hiring_contact": {
            "name": (contact.get("name") or "").strip(),
            "title": (contact.get("title") or "").strip(),
            "linkedin_url": (contact.get("linkedin_url") or "").strip(),
        },
    })
    print(f"Saved company website/contact for {args.name}.")
    return 0


def cmd_fail(args):
    if not fail_message_request(args.request_id, args.error):
        return 1
    print(f"Request {args.request_id} marked failed.")
    return 0


def cmd_notify(args):
    """Save an in-app notification and push it to every subscribed device.

    The routine calls this once at the very end of each run — after the DMs are
    written — so the push can carry the whole summary and fires even when the
    scrape found nothing. Push delivery needs VAPID_PRIVATE_KEY and
    VAPID_CLAIM_EMAIL; without them send_push_notifications() prints a notice
    and skips, and the in-app notification is still saved.
    """
    try:
        save_notification(
            title=args.title,
            body=args.body,
            notification_type="run_summary",
        )
        print("In-app notification saved.")
    except Exception as e:
        print(f"Could not save in-app notification: {e}", file=sys.stderr)

    try:
        send_push_notifications(title=args.title, body=args.body, url=args.url)
    except Exception as e:
        print(f"Could not send push: {e}", file=sys.stderr)
        return 1

    # Also email the summary (no-ops if no email backend is configured).
    try:
        from emailer import send_email
        send_email(subject=args.title, body_text=args.body)
    except Exception as e:
        print(f"Could not send email: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    main()
