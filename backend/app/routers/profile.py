import hashlib
from io import BytesIO
import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pypdf import PdfReader

from ..models.schemas import (
    ResumeProfileResponse,
    ResumeProfileReviewRequest,
    ResumeProfileStatusResponse,
    UserProfileRequest,
    UserProfileResponse,
)
from profile import (
    activate_resume_profile,
    create_resume_profile,
    get_active_profile_snapshot,
    get_latest_profile_snapshot,
    get_profile,
    get_resume_profile,
    prune_obsolete_resume_profiles,
    upsert_profile,
)
from resume_profile import extract_profile_facts, reviewed_experience_months, profile_text
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
    try:
        latest = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    except Exception:
        return None
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
    try:
        active = get_active_profile_snapshot(_DEFAULT_USERNAME)
        latest = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    except Exception:
        active = None
        latest = None
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
    try:
        previous = get_latest_profile_snapshot(_DEFAULT_USERNAME)
    except Exception:
        previous = None
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
        raise HTTPException(
            status_code=503,
            detail="The database is temporarily unavailable. Please try again in a few minutes.",
        )
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


@router.get("/desktop-prompt")
def read_desktop_prompt(request: Request, page_url: str = "",
                        kind: str = "applications"):
    """Return one shipped Claude Desktop prompt plus its rendered copy.

    `kind` picks which: `applications` (search and apply) or `outreach` (the
    cold DMs and follow-ups). They are separate runs, each pasted on its own,
    so each is fetched separately rather than sliced out of one document.

    `template` is the shipped text; `content` is the same text with the live API
    and resume links resolved, which is what gets pasted into Claude Desktop.
    Settings no longer saves its own copy: a saved copy froze the prompt, so
    later improvements to the shipped file never reached the agent.
    """
    from profile import DESKTOP_PROMPT_KINDS, default_desktop_prompt

    if kind not in DESKTOP_PROMPT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {sorted(DESKTOP_PROMPT_KINDS)}.",
        )
    try:
        template = default_desktop_prompt(kind)
    except OSError as exc:
        raise HTTPException(
            status_code=404, detail="Desktop prompt default is unavailable.",
        ) from exc

    # The outreach prompt works the Dashboard's Follow-ups Due queue in the
    # browser rather than through an API, so it needs the app's own origin, and
    # only the browser knows it. The applications prompt has no such link, so
    # it is required of whichever template actually carries the placeholder
    # rather than of every caller.
    page_url = _clean_text(page_url, 1_000)
    needs_page_url = "{{page_url}}" in template
    if page_url and not page_url.startswith(("https://", "http://localhost")):
        raise HTTPException(status_code=422, detail="page_url must be an https URL.")
    if needs_page_url and not page_url:
        raise HTTPException(status_code=422, detail="page_url is required for this prompt.")

    resume = _application_pdf_metadata()
    values = {
        "seen_urls_url": str(request.url_for("desktop_agent_seen_urls")),
        "record_url": str(request.url_for("desktop_agent_record_job")),
        "cold_dms_url": str(request.url_for("desktop_agent_cold_dms")),
        "cold_dm_record_url": str(request.url_for("desktop_agent_record_cold_dm")),
        "resume_url": str(request.url_for("download_application_resume")),
        "resume_filename": (resume or {}).get("filename") or "Resume.pdf",
        "resume_sha256": (resume or {}).get("sha256") or "unavailable",
        "page_url": page_url,
    }
    content = _PROMPT_PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)), template,
    )
    unresolved = sorted(set(_PROMPT_PLACEHOLDER.findall(content)))
    issues = []
    if unresolved:
        issues.append("The prompt contains unresolved placeholders.")
    # Only the applications prompt links the resume; warning about it on the
    # outreach prompt would be an issue the user cannot act on.
    if not resume and "{{resume_url}}" in template:
        issues.append("No Settings PDF is available, so the resume link will not work.")
    return {
        "content": content,
        "template": template,
        "kind": kind,
        "customized": False,
        "issues": issues,
        "unresolved_placeholders": unresolved,
    }
