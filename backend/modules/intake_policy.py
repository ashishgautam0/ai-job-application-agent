"""User's intake preferences, independent of resume-match scoring.

Company exclusions come solely from the user's Settings page (stored in the
database). Unknown employers remain eligible.
"""
import html
import re
import unicodedata

_SUFFIX = re.compile(
    r"\s+(?:private limited|pvt ltd|pvt limited|private ltd|limited|ltd|incorporated|inc|"
    r"corporation|corp|llc|llp|plc|gmbh|india)$"
)

def normalize_employer(value):
    value = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    value = re.sub(r"[^\w]+", " ", value).strip()
    while True:
        trimmed = _SUFFIX.sub("", value).strip()
        if trimmed == value:
            return value
        value = trimmed

def excluded_employer(company, company_exclusions=()):
    normalized = normalize_employer(company)
    return bool(normalized and normalized in {
        normalize_employer(name) for name in company_exclusions
    })

_NUMBER = r"\d+(?:\.\d+)?"
_DURATION = (
    rf"(?P<strict>more than|over|greater than|>)?\s*"
    rf"(?P<low>{_NUMBER})\s*(?:(?P<plus>\+)|(?:-|–|—|to)\s*(?P<high>{_NUMBER}))?\s*"
    r"(?P<unit>years?|yrs?|months?|mos?)"
)
# Work/technical modifiers allowed, but do not span another sentence or number.
_AFTER = re.compile(
    _DURATION + r"\s*(?:of\s+)?(?:[a-z]+[\s/-]+){0,5}(?:experience|exp\b)", re.I
)
_BEFORE = re.compile(
    r"\b(?:experience|exp)\s*(?:required|requirement|minimum)?\s*[:=-]\s*" + _DURATION,
    re.I,
)
_OPTIONAL = re.compile(r"\b(?:preferred|preferably|desirable|nice to have|optional|a plus)\b", re.I)
_REQUIRED = re.compile(r"\b(?:must|required|mandatory|minimum|at least|essential)\b", re.I)
# "minimum 1 year" and "at least 1 year" are "1+ years" in other words: open
# ended upward, so they name no top at all.
_AT_LEAST = re.compile(r"\b(?:minimum|at ?least|min\.?)\s*(?:of\s*)?$", re.I)
# A section heading names no span of time. "mandatory 5 years experience" is a
# requirement that happens to open with a heading word, and reading it as a
# heading skipped the line whole.
_HAS_DURATION = re.compile(
    r"\d[\d.\s+\-–—]*(?:to\s+\d[\d.\s]*)?(?:years?|yrs?|months?|mos?)\b", re.I
)
_WORD_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4",
                 "five": "5", "six": "6", "seven": "7", "eight": "8",
                 "nine": "9", "ten": "10"}

def experience_exclusion(description):
    """Return evidence for a stated experience band reaching past 12 months.

    Only a band that stays inside a year is eligible: "0-1 years" and "1 year"
    survive, while "0-2 years", "1-2 years", "up to 2 years" and "1+ years" do
    not, because each names a figure beyond the ceiling. Unknown experience is
    retained. Preferred sections are optional unless an individual clause
    explicitly says required.
    """
    text = html.unescape(description or "")
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b",
                  lambda m: _WORD_NUMBERS[m.group().lower()], text, flags=re.I)
    section_optional = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) < 85 and not _HAS_DURATION.search(line) and re.fullmatch(
            r"(?:preferred|desired|desirable|nice.to.have|optional)(?:\s+\w+){0,4}\s*:?", line, re.I
        ):
            section_optional = True
            continue
        if len(line) < 85 and not _HAS_DURATION.search(line) and re.fullmatch(
            r"(?:required|minimum|basic|essential|mandatory)(?:\s+\w+){0,4}\s*:?", line, re.I
        ):
            section_optional = False
            continue
        for clause in re.split(r";|(?<!\d)\.(?!\d)|\bbut\b", line):
            for pattern in (_AFTER, _BEFORE):
                for match in pattern.finditer(clause):
                    # Local qualification only: a later 'Python preferred' must
                    # not erase a preceding '2 years required'.
                    tail = clause[match.end():]
                    local = clause[max(0, match.start()-35):match.end()]
                    local += re.split(r",|\band\b", tail, maxsplit=1)[0]
                    if (_OPTIONAL.search(local) or section_optional) and not _REQUIRED.search(local):
                        continue
                    unit = 12 if match["unit"].lower().startswith(("year", "yr")) else 1
                    # Only a band that stays inside a year is eligible, so the
                    # TOP of what the posting names is what counts. "0-2 years"
                    # reaches 2 and is out, even though a fresher fits its
                    # floor, and so is "up to 2 years" -- an upper bound is a
                    # figure the posting named, not a reason to ignore it.
                    top = float(match["high"] or match["low"]) * unit
                    # Open ended upward names no top: "1+ years", "more than 1
                    # year", "minimum 1 year" all reach past the ceiling.
                    open_ended = bool(match["strict"] or match["plus"]) or bool(
                        _AT_LEAST.search(clause[:match.start()]))
                    if top > 12 or (open_ended and float(match["low"]) * unit >= 12):
                        return match.group().strip()
    return None

def exclusion_reason(job, company_exclusions=()):
    if excluded_employer(job.get("company"), company_exclusions):
        return "excluded by Settings company list"
    evidence = experience_exclusion(job.get("description"))
    if evidence:
        return "experience band reaches past one year: " + evidence
    return None

def filter_jobs(jobs, company_exclusions=()):
    kept, excluded = [], []
    for job in jobs:
        reason = exclusion_reason(job, company_exclusions)
        if reason:
            excluded.append((job, reason))
        else:
            kept.append(job)
    return kept, excluded
