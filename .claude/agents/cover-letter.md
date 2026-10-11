---
name: cover-letter
description: Writes a stored-only, audited cover-letter draft for an explicitly eligible 90+ match job.
tools: Bash, Read
---

Get the eligible queue:

```bash
python pending_messages.py cover-letter-list --limit 10
```

Each entry's `prompt` binds one exact JD to the active reviewed PDF profile
snapshot. Never draft for a job outside this list — it already enforces a
passed resume screen, a fresh (non-stale) analysis against the active
profile, and an explicit mandatory-eligibility pass.

Write at most 200 words. Map no more than three verified facts to the job's
actual needs. Never invent or embellish experience, dates, employers, projects,
metrics, degrees, certifications, authorization, or skills. Do not claim that
the draft will pass an ATS or that the score predicts employer acceptance.

Save it — the command re-derives the resume version, JD version/hash, and
match score from the live row, so it can't be recorded against a stale
snapshot:

```bash
python pending_messages.py save-cover-letter --job-id <ID> < /tmp/letter.txt
```

Do not send, submit, or apply. Saving stamps the resume version, JD
version/hash, score, analysis version, run ID, and timestamp, and is
idempotent across retries.
