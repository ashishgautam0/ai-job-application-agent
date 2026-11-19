"""
Profile data layer — stores and retrieves user profile data from Supabase.
Other modules import from here to get dynamic profile/skills/projects instead
of using hardcoded values.
"""


import hashlib
import os
from datetime import datetime

from resume_profile import merge_reviewed_facts, profile_text as snapshot_text

# --- Supabase client (reuse from tracker) ---
_supabase_client = None


def _get_client():
    """Return the Supabase client, creating it on first call."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    from supabase import create_client

    _url = os.environ.get("SUPABASE_URL", "")
    _key = os.environ.get("SUPABASE_KEY", "")

    if not _url or not _key:
        raise RuntimeError(
            "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY."
        )

    _supabase_client = create_client(_url, _key)
    return _supabase_client


# ===================== PROFILE CRUD =====================

_PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
)
# Applications and outreach are two prompts and two runs, each pasted on its
# own. They share nothing: the outreach prompt never searches or applies, and
# the applications prompt never opens LinkedIn.
DESKTOP_PROMPT_KINDS = {
    "applications": "job-agent-desktop.md",
    "outreach": "job-agent-outreach.md",
}
DEFAULT_DESKTOP_PROMPT_KIND = "applications"
_DESKTOP_PROMPT_PATH = os.path.join(_PROMPTS_DIR,
                                    DESKTOP_PROMPT_KINDS[DEFAULT_DESKTOP_PROMPT_KIND])


def desktop_prompt_path(kind=DEFAULT_DESKTOP_PROMPT_KIND):
    """The shipped file for one prompt kind; KeyError for an unknown kind."""
    return os.path.join(_PROMPTS_DIR, DESKTOP_PROMPT_KINDS[kind])


# A tripwire against runaway growth, not a budget: every line of a shipped
# prompt is pasted into the agent's context on every run.
DESKTOP_PROMPT_MAX_CHARS = 80_000


def default_desktop_prompt(kind=DEFAULT_DESKTOP_PROMPT_KIND):
    """Read one of the shipped Claude Desktop prompts."""
    with open(desktop_prompt_path(kind), encoding="utf-8") as handle:
        return handle.read()


def get_profile(username="subidh"):
    """Get full profile dict from Supabase. Returns None if not found."""
    try:
        db = _get_client()
        result = db.table("user_profile").select("*").eq("username", username).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[profile] get_profile failed: {e}")
    return None


def upsert_profile(username="subidh", data=None):
    """Create or update a user profile. Returns the saved row."""
    if data is None:
        data = {}
    db = _get_client()
    payload = {**data, "username": username, "updated_at": datetime.now().isoformat()}
    # Remove id if present to avoid conflicts
    payload.pop("id", None)

    result = db.table("user_profile").upsert(
        payload, on_conflict="username"
    ).execute()
    return result.data[0] if result.data else None


# ===================== VERSIONED PDF PROFILE =====================

def create_resume_profile(username, filename, pdf_bytes, raw_text, extraction):
    """Create a pending review version from a PDF, or return the same upload."""
    db = _get_client()
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    existing = (db.table("resume_profiles").select("*")
                .eq("username", username).eq("source_sha256", digest)
                .limit(1).execute())
    if existing.data:
        return existing.data[0]

    latest = (db.table("resume_profiles").select("version")
              .eq("username", username).order("version", desc=True)
              .limit(1).execute())
    version = int(latest.data[0]["version"]) + 1 if latest.data else 1
    payload = {
        "username": username,
        "version": version,
        "source_kind": "pdf",
        "source_filename": filename,
        "source_sha256": digest,
        "extraction_method": extraction.get("parser_version", "pdf-profile-v1"),
        "raw_text": raw_text,
        "extracted_facts": extraction,
        "corrections": {},
        "evidence": {"facts": extraction.get("facts", {})},
        "readability": extraction.get("readability", {}),
        "status": "pending_review",
    }
    result = db.table("resume_profiles").insert(payload).execute()
    return result.data[0] if result.data else None


def get_resume_profile(profile_id, username="subidh"):
    result = (_get_client().table("resume_profiles").select("*")
              .eq("id", profile_id).eq("username", username).limit(1).execute())
    return result.data[0] if result.data else None


def get_latest_resume_profile(username="subidh"):
    result = (_get_client().table("resume_profiles").select("*")
              .eq("username", username).order("version", desc=True).limit(1).execute())
    return result.data[0] if result.data else None


def _snapshot(row):
    if not row:
        return None
    extracted = row.get("extracted_facts") or {}
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "version": row.get("version"),
        "source_kind": row.get("source_kind"),
        "source_filename": row.get("source_filename"),
        "source_sha256": row.get("source_sha256"),
        "extraction_method": row.get("extraction_method"),
        "raw_text": row.get("raw_text") or "",
        "facts": merge_reviewed_facts(extracted, row.get("corrections")),
        "extracted_facts": extracted.get("facts", {}),
        "corrections": row.get("corrections") or {},
        "evidence": row.get("evidence") or {},
        "readability": row.get("readability") or {},
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "reviewed_at": row.get("reviewed_at"),
        "activated_at": row.get("activated_at"),
    }


def get_active_profile_snapshot(username="subidh"):
    """Read the one reviewed active profile. No legacy or hardcoded fallback."""
    result = (_get_client().table("resume_profiles").select("*")
              .eq("username", username).eq("status", "active")
              .order("version", desc=True).limit(1).execute())
    return _snapshot(result.data[0]) if result.data else None


def get_latest_profile_snapshot(username="subidh"):
    return _snapshot(get_latest_resume_profile(username))


def activate_resume_profile(profile_id, corrections, review_notes="", username="subidh"):
    """Atomically activate a reviewed version and stale dependent artifacts."""
    db = _get_client()
    current = get_resume_profile(profile_id, username)
    if current and current.get("status") == "active":
        # Editing the same PDF retains its source version. Invalidate BEFORE
        # activation so a failure cannot leave old artifacts marked current.
        db.table("scraped_jobs").update({"analysis_stale": True}).eq(
            "profile_version", current["version"]
        ).execute()
        db.table("job_messages").update({"is_stale": True}).eq(
            "profile_version", current["version"]
        ).execute()
        db.table("cover_letter_drafts").update({"is_outdated": True}).eq(
            "resume_version", current["version"]
        ).execute()
    result = db.rpc("activate_resume_profile", {
        "p_profile_id": profile_id,
        "p_username": username,
        "p_corrections": corrections or {},
        "p_review_notes": review_notes or "",
    }).execute()
    row = result.data[0] if isinstance(result.data, list) and result.data else result.data
    return _snapshot(row) if row else get_active_profile_snapshot(username)


def prune_obsolete_resume_profiles(keep_profile_id, username="subidh"):
    """Delete obsolete profile rows unless an audited cover letter references them."""
    db = _get_client()
    rows = (db.table("resume_profiles").select("id")
            .eq("username", username).neq("id", keep_profile_id).execute()).data or []
    old_ids = [row["id"] for row in rows if row.get("id") is not None]
    if not old_ids:
        return 0
    referenced = (db.table("cover_letter_drafts").select("resume_profile_id")
                  .in_("resume_profile_id", old_ids).execute()).data or []
    protected = {row.get("resume_profile_id") for row in referenced}
    deletable = [profile_id for profile_id in old_ids if profile_id not in protected]
    if deletable:
        db.table("resume_profiles").delete().in_("id", deletable).execute()
    return len(deletable)


# ===================== HELPER ACCESSORS =====================

def get_profile_text(username="subidh"):
    """Return reviewed active PDF text, or None when no version is active."""
    return snapshot_text(get_active_profile_snapshot(username)) or None


def get_resume_text(username="subidh"):
    """Get text from the reviewed active PDF version only."""
    return get_profile_text(username)


def get_projects(username="subidh"):
    """Get the user's projects as a {name: {keywords, one_liner}} dict.
    Returns None if not found. No caller should invent a fallback project.
    """
    profile = get_profile(username)
    if not profile:
        return None

    projects = profile.get("projects") or []
    if not projects:
        return None

    result = {}
    for p in projects:
        name = p.get("name", "")
        if name:
            result[name] = {
                "keywords": p.get("keywords", []),
                "one_liner": p.get("description", ""),
            }
    return result if result else None


def get_skills(username="subidh"):
    """Get the user's skills list from their profile.
    Returns None if not found.
    """
    snapshot = get_active_profile_snapshot(username)
    if not snapshot:
        return None
    skills = snapshot.get("facts", {}).get("skills") or []
    names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in skills]
    names = [name for name in names if name]
    return names or None


def get_scoring_weights(username="subidh"):
    """Get custom scoring weights. Returns None if not found."""
    profile = get_profile(username)
    if not profile:
        return None

    weights = profile.get("scoring_weights") or {}
    return weights if weights else None
