"""Pure persisted-application aggregations used by the dashboard and tests."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

POSITIVE_RESPONSE_STATUSES = {"Interview", "Interview Scheduled", "Interviewed", "Offer"}
STATUS_ORDER = (
    "Applied", "Follow-up Sent", "Assignment Submitted", "Interview",
    "Offer", "Rejected", "Ghosted", "Not Interested",
)


def _rows(rows):
    return [dict(row) for row in (rows or [])]


def attach_tracker_job_ids(applications, scraped_jobs):
    """Exact URL mapping; missing/deleted scraped records remain explicit None."""
    by_url = {row.get("url"): row.get("id") for row in _rows(scraped_jobs)
              if row.get("url") and type(row.get("id")) is int}
    return [{**row, "scraped_job_id": by_url.get(row.get("url"))}
            for row in _rows(applications)]


def weekly_trend(rows, weeks=12):
    buckets = defaultdict(lambda: {"Job": 0, "Internship": 0, "total": 0})
    for row in _rows(rows):
        try:
            value = date.fromisoformat(str(row.get("date_applied", ""))[:10])
        except ValueError:
            continue
        monday = value - timedelta(days=value.weekday())
        kind = "Internship" if str(row.get("type", "")).casefold() == "internship" else "Job"
        buckets[monday][kind] += 1
        buckets[monday]["total"] += 1
    result = []
    for monday in sorted(buckets)[-weeks:]:
        values = buckets[monday]
        result.append({"week": monday.isoformat(), "week_end": (monday + timedelta(days=6)).isoformat(), **values})
    return result


def platform_effectiveness(rows):
    groups = defaultdict(list)
    for row in _rows(rows):
        groups[str(row.get("platform") or "Unknown").strip() or "Unknown"].append(row)
    result = []
    for platform, values in groups.items():
        responses = sum(row.get("status") in POSITIVE_RESPONSE_STATUSES for row in values)
        applications = len(values)
        result.append({
            "platform": platform, "applications": applications,
            "responses": responses,
            "response_rate": round(responses * 100 / applications, 1),
        })
    return sorted(result, key=lambda row: (-row["applications"], row["platform"].casefold()))


def status_breakdown(rows):
    rows = _rows(rows)
    if not rows:
        return {}
    counts = Counter(str(row.get("status") or "Unknown") for row in rows)
    result = {status: counts.pop(status, 0) for status in STATUS_ORDER}
    for status in sorted(counts):
        result[status] = counts[status]
    return result


# Job-title structure rather than subject matter: seniority, employment shape,
# generic role nouns and working arrangements. These say how a job is packaged,
# never what it is about, so they must not become groups of their own.
_TITLE_NOISE = frozenset("""
engineer engineering developer development specialist consultant analyst architect
manager programmer practitioner professional intern internship trainee fresher
graduate apprentice senior sr junior jr lead principal staff associate assoc entry
mid level grade remote onsite on site hybrid wfh work from home office full part
time fulltime contract permanent freelance position role job opening hiring urgent
immediate years year yrs exp experience and or of for the with in at to new team
services service solutions technologies technology based
""".split())

# Spelling, not taxonomy. The same subject gets written several ways, and
# without this "AI/ML", "AI ML" and "Machine Learning" form three groups that
# all mean one thing. Each rewrites to a single token so the grouping below
# stays a pure word count.
_CANONICAL_SPELLINGS = (
    (r"\b(?:artificial intelligence)\b", "ai"),
    (r"\b(?:machine learning|ml)\b", "ml"),
    (r"\b(?:gen(?:erative)?\s*ai|genai)\b", "genai"),
    (r"\b(?:natural language processing|nlp)\b", "nlp"),
    (r"\b(?:large language models?|llms?)\b", "llm"),
    (r"\b(?:computer vision|cv)\b", "computervision"),
    (r"\b(?:deep learning|dl)\b", "deeplearning"),
    (r"\b(?:data scien(?:ce|tist)s?)\b", "datascience"),
    (r"\b(?:data engineer(?:ing)?)\b", "dataengineering"),
    (r"\b(?:ml\s*ops|mlops)\b", "mlops"),
    (r"\b(?:agentic|agents?)\b", "agent"),
    (r"\b(?:back\s*end)\b", "backend"),
    (r"\b(?:front\s*end)\b", "frontend"),
    (r"\b(?:full\s*stack)\b", "fullstack"),
)

# How the canonical tokens read on screen; anything else is title-cased.
_WORD_LABELS = {
    "ai": "AI", "ml": "ML", "genai": "Generative AI", "nlp": "NLP", "llm": "LLM",
    "computervision": "Computer Vision", "deeplearning": "Deep Learning",
    "datascience": "Data Science", "dataengineering": "Data Engineering",
    "mlops": "MLOps", "agent": "Agents", "backend": "Backend",
    "frontend": "Frontend", "fullstack": "Full Stack", "aws": "AWS",
    "gcp": "GCP", "api": "API", "sql": "SQL", "qa": "QA", "ui": "UI", "ux": "UX",
}

# A term has to describe at least this many applications to be a group of its
# own; below that it is a one-off, not a pattern worth a row on the Dashboard.
_MIN_ROLES_PER_GROUP = 3
# Beyond this the card stops being readable; the rest fall into "Other".
_MAX_ROLE_GROUPS = 14


def _role_terms(role):
    """The subject-matter words and word pairs in one job title."""
    text = re.sub(r"[^a-z0-9+#]+", " ", str(role or "").casefold())
    for pattern, canonical in _CANONICAL_SPELLINGS:
        text = re.sub(pattern, canonical, text)
    words = [word for word in text.split()
             if word not in _TITLE_NOISE and not word.isdigit() and len(word) > 1]
    # Pairs are stored in a fixed order so "AI Agent" and "Agentic AI" are one
    # term, and a word paired with itself is not a term at all. A pair earns a
    # group only where it is rarer than either word alone, i.e. where the
    # combination really is its own kind of role.
    pairs = {" ".join(sorted((first, second)))
             for first, second in zip(words, words[1:]) if first != second}
    return set(words) | pairs


def _role_label(term):
    return " / ".join(_WORD_LABELS.get(word, word.title()) for word in term.split())


def role_analysis(rows):
    """Group roles by what they are actually called, learned from the titles.

    There is no fixed list of role families here. The groups are the words and
    word pairs that actually recur across the tracked job titles, so a kind of
    role that was never anticipated still gets its own row once a few of them
    have been applied to, and nothing has to be edited when the market moves.

    Each title is filed under the *rarest* term it contains. That is what keeps
    a broad word from swallowing everything: "AI" only claims a title that has
    nothing more specific in it, so "Applied Computer Vision Engineer" is
    Computer Vision and a plain "AI Engineer" is AI.
    """
    rows = _rows(rows)
    if not rows:
        return []
    terms_per_row = [_role_terms(row.get("role")) for row in rows]
    frequency = Counter(term for terms in terms_per_row for term in terms)

    groups = defaultdict(list)
    unassigned = []
    for row, terms in zip(rows, terms_per_row):
        usable = [term for term in terms if frequency[term] >= _MIN_ROLES_PER_GROUP]
        if not usable:
            unassigned.append(row)
            continue
        # Rarest first. When a pair and one of its words describe the same
        # titles the extra word adds nothing, so the shorter label wins.
        groups[min(usable, key=lambda t: (frequency[t], t.count(" "), t))].append(row)

    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    kept, overflow = ranked[:_MAX_ROLE_GROUPS], ranked[_MAX_ROLE_GROUPS:]
    named = [(_role_label(term), members) for term, members in kept]
    leftovers = unassigned + [row for _, members in overflow for row in members]
    if leftovers:
        named.append(("Other", leftovers))

    result = []
    for label, members in named:
        responses = sum(row.get("status") in POSITIVE_RESPONSE_STATUSES for row in members)
        applied = len(members)
        examples = sorted({str(row.get("role") or "").strip()
                           for row in members if row.get("role")})[:3]
        result.append({
            "role_keyword": label, "applied": applied, "responses": responses,
            "response_rate": round(responses * 100 / applied, 1),
            "example_roles": examples,
        })
    # "Other" is a remainder, not a finding, so it sorts last whatever its size.
    return sorted(result, key=lambda row: (row["role_keyword"] == "Other",
                                           -row["applied"], row["role_keyword"]))
