import hashlib
from io import BytesIO
from typing import Literal
import json
import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pypdf import PdfReader

from ..models.schemas import (
    ApplicationPromptSettings,
    CompanyExclusionsResponse,
    CompanyExclusionsSettings,
    RenderedApplicationPrompt,
    ResumeProfileResponse,
    ResumeProfileReviewRequest,
    ResumeProfileStatusResponse,
    UserProfileRequest,
    UserProfileResponse,
)
from profile import (
    DEFAULT_AUTOMATION_RULES,
    activate_resume_profile,
    create_resume_profile,
    get_active_profile_snapshot,
    get_application_prompt_settings,
    get_company_exclusions,
    get_latest_profile_snapshot,
    get_profile,
    get_resume_profile,
    prune_obsolete_resume_profiles,
    save_application_prompt_settings,
    save_company_exclusions,
    upsert_profile,
)
from resume_profile import extract_profile_facts, reviewed_experience_months, profile_text
from json_safe import json_records
from pdf_storage import (
    _encode_url_path,
    _ensure_pdf_bucket,
    _get_client as _storage_client,
    _PDF_BUCKET,
)

router = APIRouter()

_DEFAULT_USERNAME = "subidh"
_MAX_RESUME_BYTES = 10 * 1024 * 1024
_MAX_RESUME_PAGES = 30
_APPLICATION_PREFIX = "application-resumes"
_PROMPT_PLACEHOLDER = re.compile(r"{{([a-z_][a-z0-9_]*)}}")


def _application_path(source_sha256):
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha256 or ""):
        raise HTTPException(status_code=404, detail="Resume not found.")
    return f"{_APPLICATION_PREFIX}/{_DEFAULT_USERNAME}/{source_sha256}.pdf"


def _stored_application_paths(bucket):
    """List current and legacy resume objects, bounded to two folder levels."""
    paths = []
    for item in bucket.list(_APPLICATION_PREFIX) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        direct = f"{_APPLICATION_PREFIX}/{name}"
        if name.lower().endswith(".pdf"):
            paths.append(direct)
            continue
        for child in bucket.list(direct) or []:
            if isinstance(child, dict) and str(child.get("name", "")).lower().endswith(".pdf"):
                paths.append(f"{direct}/{child['name']}")
    return paths


def _store_application_pdf(raw, source_sha256):
    """Store the candidate PDF without disturbing the current valid object."""
    db = _storage_client()
    _ensure_pdf_bucket(db)
    bucket = db.storage.from_(_PDF_BUCKET)
    current = _application_path(source_sha256)
    bucket.upload(current, raw, {
        "content-type": "application/pdf", "upsert": "true", "cache-control": "0",
    })
    return current


def _remove_obsolete_application_pdfs(source_sha256):
    bucket = _storage_client().storage.from_(_PDF_BUCKET)
    current = _application_path(source_sha256)
    obsolete = [path for path in _stored_application_paths(bucket) if path != current]
    if obsolete:
        bucket.remove(obsolete)
    return len(obsolete)


def _application_pdf_metadata():
    latest = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    if not latest:
        return None
    path = _application_path(latest.get("source_sha256"))
    try:
        bucket = _storage_client().storage.from_(_PDF_BUCKET)
        filename = path.rsplit("/", 1)[-1]
        entries = bucket.list(path.rsplit("/", 1)[0]) or []
        entry = next(item for item in entries
                     if isinstance(item, dict) and item.get("name") == filename)
    except Exception:
        return None
    metadata = entry.get("metadata") or {}
    size = metadata.get("size") or metadata.get("contentLength") or entry.get("size")
    return {
        "filename": latest.get("source_filename") or "Resume.pdf",
        "sha256": latest.get("source_sha256"),
        "size": size,
        "version": latest.get("version"),
        "profile_status": latest.get("status"),
    }


@router.get("/", response_model=UserProfileResponse)
def read_profile():
    data = get_profile(_DEFAULT_USERNAME)
    if data is None:
        return UserProfileResponse(username=_DEFAULT_USERNAME)
    return UserProfileResponse(**data)


@router.put("/", response_model=UserProfileResponse)
def update_profile(body: UserProfileRequest):
    payload = body.model_dump(exclude_none=True)
    saved = upsert_profile(_DEFAULT_USERNAME, payload)
    if saved:
        return UserProfileResponse(**saved)
    return UserProfileResponse(username=_DEFAULT_USERNAME)


def _settings_field_limit(key):
    if key == "desktop_prompt_template":
        # The desktop prompt carries the portal playbooks and the shared
        # apply rules, and it grows every time a portal changes. The store is
        # a text column, so this ceiling only exists to reject a runaway paste
        # — keep enough headroom that a normal edit never trips it.
        return 60_000
    if key.endswith("template") or key == "automation_rules":
        return 12_000
    return 500


@router.get("/application-settings", response_model=ApplicationPromptSettings)
def read_application_settings():
    """Load the prompt settings from backend state for any browser/device."""
    return ApplicationPromptSettings(**get_application_prompt_settings(_DEFAULT_USERNAME))


@router.put("/application-settings", response_model=ApplicationPromptSettings)
def update_application_settings(body: ApplicationPromptSettings):
    payload = {
        key: _clean_text(value, _settings_field_limit(key))
        for key, value in body.model_dump(exclude_unset=True).items()
    }
    saved = save_application_prompt_settings(_DEFAULT_USERNAME, payload)
    if saved is None:
        raise HTTPException(status_code=500, detail="Application prompt settings could not be saved.")
    return ApplicationPromptSettings(**saved)


def _company_exclusion_lists():
    """The user's own exclusions and the tracker's companies, without overlap.

    Tracker companies are read live, not stored in the Settings list: copying
    them there pushed the list past what Settings can load and edit. A saved
    name that is also a tracker company is shown once, under the tracker.
    """
    from intake_policy import normalize_employer
    from tracker import get_tracked_companies

    tracked = get_tracked_companies()
    tracked_keys = {normalize_employer(name) for name in tracked}
    custom = [name for name in get_company_exclusions(_DEFAULT_USERNAME)
              if normalize_employer(name) not in tracked_keys]
    return custom, tracked


@router.get("/company-exclusions", response_model=CompanyExclusionsResponse)
def read_company_exclusions():
    custom, tracked = _company_exclusion_lists()
    return CompanyExclusionsResponse(companies=custom, tracked=tracked)


@router.put("/company-exclusions", response_model=CompanyExclusionsResponse)
def update_company_exclusions(body: CompanyExclusionsSettings):
    """Save the user's own list; tracker companies are never stored here."""
    if any(len(name.strip()) > 120 for name in body.companies):
        raise HTTPException(status_code=422, detail="Each company name must be at most 120 characters.")
    save_company_exclusions(_DEFAULT_USERNAME, body.companies)
    custom, tracked = _company_exclusion_lists()
    return CompanyExclusionsResponse(companies=custom, tracked=tracked)


def _render_application_prompt(template, settings, jobs, resume, page_url, resume_url):
    """Render one immutable browser-agent batch without browser-local state."""
    batch = [{
        "job_id": job.get("id"),
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "source": job.get("source") or "",
        "url": job.get("url") or "",
    } for job in jobs]
    values = {
        "page_url": page_url,
        "resume_filename": (resume or {}).get("filename") or "Resume.pdf",
        "resume_url": resume_url,
        "resume_sha256": (resume or {}).get("sha256") or "unavailable",
        "batch_jobs": json.dumps(batch, ensure_ascii=False, indent=2, default=str),
    }
    rules = ("BATCH ELIGIBILITY (authoritative): The backend includes only jobs with "
             "a current passing screen and posting URL. Screening results are omitted "
             "from job JSON; older saved wording that expects those fields is fulfilled "
             "by this server check. Recheck each listing for new mandatory requirements.\n\n" +
             (settings.get("automation_rules") or DEFAULT_AUTOMATION_RULES).rstrip())
    rendered = rules + "\n\n" + template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    unresolved = sorted(set(_PROMPT_PLACEHOLDER.findall(rendered)))
    return rendered, unresolved


@router.get("/application-prompt", response_model=RenderedApplicationPrompt)
def read_rendered_application_prompt(request: Request, page_url: str):
    """Return one complete Codex prompt using current backend jobs and PDF."""
    page_url = _clean_text(page_url, 1_000)
    if not page_url.startswith(("https://", "http://localhost")):
        raise HTTPException(status_code=422, detail="Today Todo page URL is invalid.")

    from tracker import get_scraped_jobs

    settings = get_application_prompt_settings(_DEFAULT_USERNAME)
    frame = get_scraped_jobs()
    visible_jobs = json_records(frame)
    # The browser batch omits screening results, so the server must enforce the
    # pass gate before rendering instead of asking an agent to infer eligibility.
    jobs = [job for job in visible_jobs if job.get("screening_status") == "pass"
            and isinstance(job.get("url"), str) and job["url"].strip()]
    resume = _application_pdf_metadata()
    resume_url = str(request.url_for("download_application_resume"))
    prompt, unresolved = _render_application_prompt(
        settings["prompt_template"], settings, jobs, resume, page_url, resume_url,
    )
    issues = []
    if not jobs:
        issues.append("No current Today Todo jobs have a passing screen and posting URL.")
    if not resume:
        issues.append("No latest Settings PDF is available.")
    if unresolved:
        issues.append("The saved prompt or automation rules contain unresolved placeholders.")
    return RenderedApplicationPrompt(
        prompt=prompt,
        job_count=len(jobs),
        resume_available=bool(resume),
        ready=not issues,
        issues=issues,
        unresolved_placeholders=unresolved,
    )


def _public_snapshot(snapshot):
    if not snapshot:
        return None
    result = {key: value for key, value in snapshot.items() if key not in {"raw_text", "corrections"}}
    result["backend_text"] = profile_text(snapshot)
    return result


def _clean_text(value, maximum=500):
    value = str(value or "").strip()
    if len(value) > maximum or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value):
        raise HTTPException(status_code=422, detail="A reviewed field contains invalid text.")
    return value


def _review_corrections(row, body):
    extracted = ((row.get("extracted_facts") or {}).get("facts") or {})
    evidence_by_id = {}
    for group in ("experience", "education"):
        for item in extracted.get(group) or []:
            if item.get("id"):
                evidence_by_id[item["id"]] = item.get("evidence") or []
    skill_evidence = {
        str(item.get("name", "")).casefold(): item.get("evidence") or []
        for item in extracted.get("skills") or []
    }
    cert_evidence = {
        str(item.get("name", "")).casefold(): item.get("evidence") or []
        for item in extracted.get("certifications") or []
    }

    skills = []
    seen = set()
    for value in body.skills:
        name = _clean_text(value, 120)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        skills.append({
            "name": name,
            "evidence": skill_evidence.get(key) or [{
                "source": "user_review", "line": None,
                "excerpt": "User-confirmed structured correction",
            }],
        })

    experience = []
    for index, item in enumerate(body.experience):
        value = item.model_dump()
        item_id = _clean_text(value.get("id"), 100) or f"reviewed-experience-{index + 1}"
        start = _clean_text(value.get("start"), 20)
        end = _clean_text(value.get("end"), 20).lower()
        month_pattern = r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])"
        if not re.fullmatch(month_pattern, start) or not (
            re.fullmatch(month_pattern, end) or end == "present"
        ):
            raise HTTPException(status_code=422, detail="Experience dates must be YYYY-MM or present.")
        experience.append({
            "id": item_id,
            "label": _clean_text(value.get("label"), 300),
            "role": _clean_text(value.get("role"), 200),
            "company": _clean_text(value.get("company"), 200),
            "start": start,
            "end": end,
            "evidence": evidence_by_id.get(item_id) or [{
                "source": "user_review", "line": None,
                "excerpt": "User-confirmed structured correction",
            }],
        })

    education = []
    allowed_levels = {"diploma", "bachelor", "master", "doctorate"}
    for index, item in enumerate(body.education):
        value = item.model_dump()
        level = _clean_text(value.get("level"), 30).lower()
        if level not in allowed_levels:
            raise HTTPException(status_code=422, detail="Invalid education level.")
        item_id = _clean_text(value.get("id"), 100) or f"reviewed-education-{index + 1}"
        education.append({
            "id": item_id, "level": level,
            "credential": _clean_text(value.get("credential"), 300),
            "field": _clean_text(value.get("field"), 200),
            "institution": _clean_text(value.get("institution"), 200),
            "evidence": evidence_by_id.get(item_id) or [{
                "source": "user_review", "line": None,
                "excerpt": "User-confirmed structured correction",
            }],
        })

    certifications = []
    seen = set()
    for value in body.certifications:
        name = _clean_text(value, 300)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        certifications.append({
            "name": name,
            "evidence": cert_evidence.get(key) or [{
                "source": "user_review", "line": None,
                "excerpt": "User-confirmed structured correction",
            }],
        })
    total_months = reviewed_experience_months(experience)
    if total_months is None and extracted.get("experience_claims"):
        total_months = extracted.get("total_experience_months")
    return {
        "skills": skills,
        "experience": experience,
        "total_experience_months": total_months,
        "education": education,
        "certifications": certifications,
    }


@router.get("/resume", response_model=ResumeProfileStatusResponse)
def resume_status():
    active = get_active_profile_snapshot(_DEFAULT_USERNAME)
    latest = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    return {"active": _public_snapshot(active), "latest": _public_snapshot(latest)}


@router.post("/resume", response_model=ResumeProfileResponse)
async def upload_resume(file: UploadFile = File(...)):
    """Extract a text-based PDF into a pending, evidence-backed profile."""
    filename = file.filename or ""
    if file.content_type != "application/pdf" or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Please upload a PDF file.")

    raw = await file.read(_MAX_RESUME_BYTES + 1)
    await file.close()
    if len(raw) > _MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="Resume PDF must be 10 MB or smaller.")
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The selected file is not a valid PDF.")

    try:
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > _MAX_RESUME_PAGES:
            raise HTTPException(
                status_code=422,
                detail=f"Resume PDF must have {_MAX_RESUME_PAGES} pages or fewer.",
            )
        page_text = [(page.extract_text() or "").strip() for page in reader.pages]
        extracted = "\n\n".join(page_text)
        resume_text = "\n\n".join(part for part in extracted.split("\n\n") if part).strip()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Could not read this PDF.") from exc

    if len(resume_text) < 100:
        raise HTTPException(
            status_code=422,
            detail=(
                "This PDF does not contain enough selectable text. "
                "Export it as a text-based PDF instead of a scanned image."
            ),
        )

    extraction = extract_profile_facts(
        resume_text,
        page_count=len(reader.pages),
        pages_with_text=sum(bool(text) for text in page_text),
    )
    digest = hashlib.sha256(raw).hexdigest()
    previous = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    previous_digest = (previous or {}).get("source_sha256")
    try:
        _store_application_pdf(raw, digest)
        saved = create_resume_profile(
            _DEFAULT_USERNAME, filename, raw, resume_text, extraction
        )
    except Exception:
        # A failed database write must not leave the failed replacement active.
        if digest != previous_digest:
            try:
                _storage_client().storage.from_(_PDF_BUCKET).remove([_application_path(digest)])
            except Exception:
                pass
        raise
    if not saved:
        if digest != previous_digest:
            try:
                _storage_client().storage.from_(_PDF_BUCKET).remove([_application_path(digest)])
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="The resume could not be saved.")
    _remove_obsolete_application_pdfs(digest)
    from profile import _snapshot
    return ResumeProfileResponse(**_public_snapshot(_snapshot(saved)))


@router.put("/resume/{profile_id}/activate", response_model=ResumeProfileResponse)
def review_and_activate_resume(profile_id: int, body: ResumeProfileReviewRequest):
    """Activate reviewed structured facts; raw PDF extraction stays immutable."""
    row = get_resume_profile(profile_id, _DEFAULT_USERNAME)
    if not row:
        raise HTTPException(status_code=404, detail="Resume profile not found.")
    if (row.get("readability") or {}).get("status") == "unreadable":
        raise HTTPException(status_code=422, detail="Unreadable PDF cannot be activated.")
    corrections = _review_corrections(row, body)
    saved = activate_resume_profile(
        profile_id, corrections, _clean_text(body.review_notes, 2_000), _DEFAULT_USERNAME
    )
    if not saved:
        raise HTTPException(status_code=500, detail="The reviewed profile could not be activated.")
    try:
        prune_obsolete_resume_profiles(saved["id"], _DEFAULT_USERNAME)
    except Exception as exc:
        # Activation is already committed. Preserve referenced rows and defer
        # unexpected cleanup failures rather than reporting a false failure.
        print(f"[profile] obsolete resume cleanup deferred: {exc}")
    return ResumeProfileResponse(**_public_snapshot(saved))


@router.get("/resume/application")
def application_resume_status():
    """Return the last Settings-uploaded PDF without browser-local state."""
    metadata = _application_pdf_metadata()
    return {"available": bool(metadata), **(metadata or {})}


@router.get("/resume/pdf")
def download_application_resume():
    """Redirect to a short-lived private Storage URL to avoid response limits."""
    latest = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    if not latest or not _application_pdf_metadata():
        raise HTTPException(
            status_code=404,
            detail="Resume PDF unavailable. Upload it again from Settings.",
        )
    result = _storage_client().storage.from_(_PDF_BUCKET).create_signed_url(
        _application_path(latest.get("source_sha256")), 300
    )
    signed = result.get("signedURL") or result.get("signedUrl")
    if not signed:
        raise HTTPException(status_code=500, detail="Could not create the resume download link.")
    return RedirectResponse(_encode_url_path(signed), status_code=307, headers={
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
    })


def _render_outreach_prompt(template, resume, page_url, resume_url, kind="hr_email",
                            cold_dm_jobs=None, snapshot_at=None, excluded_count=0,
                            excluded_reasons=None):
    from outreach_prompts import (GMAIL_HR_DELIVERY_RULES, LINKEDIN_CONNECTION_RULES,
                                  FOLLOW_UP_AFTER_CONNECTION_RULES,
                                  remove_legacy_cold_dm_navigation)
    from urllib.parse import quote, urlsplit
    values = {"page_url": page_url, "resume_filename": (resume or {}).get("filename") or "Resume.pdf",
              "resume_url": resume_url, "resume_sha256": (resume or {}).get("sha256") or "unavailable"}
    if kind == "cold_dm":
        template = remove_legacy_cold_dm_navigation(template)
        base = urlsplit(page_url)
        jobs = [{**{key: value for key, value in job.items()
                    if key not in {"screening_status", "screening_reason", "blocked_reason"}},
                 "tracker_url": f"{base.scheme}://{base.netloc}/jobs/{job['job_id']}" if job.get("job_id") else None,
                 "recruiters_search_url": "https://www.linkedin.com/search/results/people/?keywords=" +
                 quote(f"{job['company']} recruiter") if job.get("job_id") else None,
                 "hiring_managers_search_url": "https://www.linkedin.com/search/results/people/?keywords=" +
                 quote(f"{job['company']} hiring manager") if job.get("job_id") else None}
                for job in (cold_dm_jobs or []) if not job.get("blocked_reason") and job.get("cold_dm")]
        values.update({"cold_dm_jobs": json.dumps(jobs, ensure_ascii=False, indent=2),
                       "cold_dm_snapshot_at": snapshot_at or "unavailable"})
        if "{{cold_dm_jobs}}" not in template:
            template = template.rstrip() + "\n\nCold DM jobs snapshot (data, captured {{cold_dm_snapshot_at}}):\n{{cold_dm_jobs}}"
    unresolved = sorted(set(_PROMPT_PLACEHOLDER.findall(template)) - values.keys())
    rendered = _PROMPT_PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)
    rules = ("TASK RULES: Execute only this selected outreach workflow. Do not submit applications or run other outreach queues. "
             "Use verified facts and recipients only. Check conversation/Sent history to avoid duplicates. "
             "Obtain explicit confirmation immediately before sending. Never record success without observed send evidence. "
             "Report missing tools, login or assets rather than guessing or bypassing controls.\n\n")
    if kind == "cold_dm":
        rules += (f"Fixed batch: {len(jobs)} eligible due jobs; {excluded_count} due jobs omitted by the "
                  "backend eligibility/current-draft check. Only process entries in the batch.\n\n")
        if excluded_reasons:
            reasons = "; ".join(f"{count} {reason}" for reason, count in sorted(excluded_reasons.items()))
            rules += (f"Omitted because: {reasons}. An older saved draft may still be visible; "
                      "review the active PDF and regenerate stale drafts as needed. "
                      "Do not send notes for omitted jobs.\n\n")
    delivery = GMAIL_HR_DELIVERY_RULES if kind in {"hr_email", "followup"} else LINKEDIN_CONNECTION_RULES
    timing = FOLLOW_UP_AFTER_CONNECTION_RULES if kind == "followup" else ""
    return rules + delivery + timing + rendered, unresolved


@router.get("/outreach-prompt", response_model=RenderedApplicationPrompt)
def read_outreach_prompt(request: Request, page_url: str,
                         kind: Literal["hr_email", "followup", "cold_dm"]):
    page_url = _clean_text(page_url, 1_000)
    if not page_url.startswith(("https://", "http://localhost")):
        raise HTTPException(status_code=422, detail="App page URL is invalid.")
    settings = get_application_prompt_settings(_DEFAULT_USERNAME)
    resume = _application_pdf_metadata()
    jobs, snapshot_at, excluded_count, excluded_reasons = None, None, 0, {}
    if kind == "cold_dm":
        from tracker import _user_now, get_cold_dm_prompt_jobs
        try:
            due_jobs = get_cold_dm_prompt_jobs((resume or {}).get("version"))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        jobs = [job for job in due_jobs if not job["blocked_reason"] and job.get("cold_dm")]
        excluded_count = len(due_jobs) - len(jobs)
        for job in due_jobs:
            if job["blocked_reason"]:
                reason = job["blocked_reason"]
                excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
        snapshot_at = _user_now().isoformat()
    prompt, unresolved = _render_outreach_prompt(settings[kind + "_template"], resume, page_url,
                                                 str(request.url_for("download_application_resume")), kind,
                                                 jobs, snapshot_at, excluded_count, excluded_reasons)
    issues = []
    if not resume:
        issues.append("No latest Settings PDF is available.")
    if unresolved:
        issues.append("The saved template contains unresolved placeholders.")
    if kind == "cold_dm" and not jobs:
        issues.append("No due Tracker jobs have a current Cold DM for the latest Settings PDF and matching Tracker record.")
    if kind == "cold_dm" and excluded_reasons:
        reasons = "; ".join(f"{count} {reason}" for reason, count in sorted(excluded_reasons.items()))
        issues.append(f"{excluded_count} due jobs omitted: {reasons}.")
    return RenderedApplicationPrompt(prompt=prompt, job_count=len(jobs or []), resume_available=bool(resume),
                                     ready=bool(resume) and not unresolved and (kind != "cold_dm" or bool(jobs)),
                                     issues=issues, unresolved_placeholders=unresolved)


def _excluded_company_lines():
    """Every excluded company as prompt bullets: the user's list, then the tracker's.

    The desktop agent is the only thing that searches the portals, so this list
    reaches it one way only: written into its prompt. Nothing else filters
    these employers out any more.
    """
    custom, tracked = _company_exclusion_lists()
    companies = custom + tracked
    if not companies:
        return "- (No companies are excluded.)"
    return "\n".join(f"- {name}" for name in companies)


@router.get("/desktop-prompt")
def read_desktop_prompt(request: Request):
    """Return the shipped Claude Desktop prompt plus its rendered copy.

    `template` is the shipped text; `content` is the same text with the live API
    and resume links resolved, which is what gets pasted into Claude Desktop.
    Settings no longer saves its own copy: a saved copy froze the prompt, so
    later improvements to the shipped file never reached the agent.
    """
    from profile import default_desktop_prompt

    try:
        template = default_desktop_prompt()
    except OSError as exc:
        raise HTTPException(
            status_code=404, detail="Desktop prompt default is unavailable.",
        ) from exc

    resume = _application_pdf_metadata()
    values = {
        "seen_urls_url": str(request.url_for("desktop_agent_seen_urls")),
        "record_url": str(request.url_for("desktop_agent_record_job")),
        "cold_dms_url": str(request.url_for("desktop_agent_cold_dms")),
        "cold_dm_record_url": str(request.url_for("desktop_agent_record_cold_dm")),
        "resume_url": str(request.url_for("download_application_resume")),
        "resume_filename": (resume or {}).get("filename") or "Resume.pdf",
        "resume_sha256": (resume or {}).get("sha256") or "unavailable",
        "excluded_companies": _excluded_company_lines(),
    }
    content = _PROMPT_PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)), template,
    )
    unresolved = sorted(set(_PROMPT_PLACEHOLDER.findall(content)))
    issues = []
    if unresolved:
        issues.append("The prompt contains unresolved placeholders.")
    if not resume:
        issues.append("No Settings PDF is available, so the resume link will not work.")
    return {
        "content": content,
        "template": template,
        "customized": False,
        "issues": issues,
        "unresolved_placeholders": unresolved,
    }
