# Follow-Up System — Complete Technical Documentation

## 1. Architecture Overview

The follow-up system spans three layers:

| Layer | Technology | Key Files |
|-------|-----------|-----------|
| **Database** | Supabase (PostgreSQL) | 2 tables: `applications`, `follow_up_history` |
| **Backend API** | FastAPI (Python) | `tracker.py`, `message_generator.py`, `follow_ups.py`, `messages.py`, `schemas.py` |
| **Frontend** | Next.js 16 + React 19 | `dashboard/page.tsx`, `messages/page.tsx`, `tracker/page.tsx`, `api.ts`, `types.ts` |
| **LLM** | Claude, via the scheduled routine — no API key | `message_generator.py` builds the prompt; `pending_messages.py` is the routine's interface |

---

## 2. Database Schema (3 Tables Involved)

### 2a. `applications` table
The primary table. Key follow-up columns:
- `status` — Current state: `Applied`, `Follow-up Sent`, `Interview`, `Interview Scheduled`, `Interviewed`, `Offer`, `Rejected`, `Ghosted`, `Not Interested`
- `date_applied` — The date the user submitted the application (YYYY-MM-DD)
- `follow_up_date` — The next date a follow-up is due (YYYY-MM-DD, or NULL if no follow-up needed)
- `follow_up_count` — Integer tracking how many follow-ups have been sent (0, 1, 2, 3...)

### 2b. `follow_up_history` table
Granular log of every follow-up message ever sent:
- `entity_type` — `"application"`
- `entity_id` — The ID of the application this follow-up belongs to
- `message_content` — The actual text of the message that was sent
- `channel` — Where it was sent: `"LinkedIn connection"` for the invitation that opens the thread, `"LinkedIn"` for the later direct message.
- `follow_up_number` — Sequential counter: 1, 2, 3...
- `follow_up_outcome` — `"pending"`, `"responded"`, or `"no_response"`
- `sent_at` — Timestamp (auto-set by Supabase)

---

## 3. Global Cadence Constants

Defined at the top of `backend/modules/tracker.py`:

```python
APPLICATION_CADENCE = [7, 14, 21]   # days from date_applied
INTERVIEW_FOLLOW_UP_DAYS = 3
TERMINAL_STATUSES = ["Offer", "Rejected", "Ghosted", "Not Interested"]
```

**How cadence works:**
- `APPLICATION_CADENCE = [7, 14, 21]` means: follow-up #1 is due 7 days after `date_applied`, follow-up #2 is due 14 days after `date_applied`, follow-up #3 is due 21 days after `date_applied`.
- These are **absolute offsets from the application date**, not relative to the previous follow-up.
- `INTERVIEW_FOLLOW_UP_DAYS = 3` — After an interview, set a 3-day follow-up for a thank-you/status-check.
- `TERMINAL_STATUSES` — Once an application reaches any of these, all follow-up dates are cleared and no more follow-ups are surfaced.

---

## 4. The Follow-Up Lifecycle (Step by Step)

### Step 1: Application Created
When the user logs a new application via `add_application()`:
- `date_applied` is set to today
- `follow_up_date` is set to **today + 7 days** (the first cadence step)
- `follow_up_count` defaults to 0 (not set explicitly, column default)
- `status` defaults to `"Applied"`

### Step 2: Follow-Up Becomes Due
The function `get_follow_ups_due()` runs when the dashboard loads:
1. Queries all applications where `follow_up_date <= today`
2. Filters OUT any application whose status is in `TERMINAL_STATUSES` (`Offer`, `Rejected`, `Ghosted`, `Not Interested`)
3. Filters out rows with NULL or empty `follow_up_date`
4. **Sorts by `follow_up_date` ascending** — so the most overdue follow-ups appear first (urgency sorting)
5. Returns the filtered DataFrame

The dashboard frontend receives this list and renders each one as a card with urgency indicators:
- **Red border + "Overdue" badge** — `follow_up_date < today`
- **Amber border + "Today" badge** — `follow_up_date === today`
- **Default amber** — due but not yet overdue
- Each card also shows a **`#X of 3` badge** indicating which follow-up attempt is next
- Each card has an **inline date picker** for snoozing to a later date

### Step 3: User Generates a Follow-Up Message
The user goes to the Messages page, selects "Follow-up" type, and fills in:
- Company name
- Role title
- Days since applied
- Follow-up # (1, 2, or 3) — dropdown with labels:
  - `#1 — Polite check-in`
  - `#2 — With value-add`
  - `#3 — Final follow-up`
- Platform (LinkedIn, Twitter, Wellfound)

These fields can also be **pre-filled via URL query params** (e.g., when navigating from the Tonight's Plan page with `?type=follow-up&company=Stripe&role=AI+Engineer&days=7&follow_up_number=2`).

The frontend calls `POST /api/messages/follow-up` with this payload:
```json
{
  "company": "ContextJet.ai",
  "role": "AI-ML Systems Engineering Internship",
  "days": 7,
  "platform": "LinkedIn",
  "follow_up_number": 1
}
```

### Step 4: LLM Generates the Message
The API handler calls `generate_follow_up()` in `message_generator.py`.

This function does three things:

#### 4a. Loads the sender profile
```python
sender_profile = profile_text or _get_profile_text()
```
`_get_profile_text()` first tries to load the profile from the database (the `user_profiles` table). If that fails, it falls back to the hardcoded `_DEFAULT_PROFILE` string which contains:
- Work experience (AI Engineer Intern at PathToPR.ca)
- Education (M.Tech in AI from Amity University)
- Key projects (Agentic RAG Knowledge Base, BCT Engineering Notes)
- Tech stack
- Current job search status

#### 4b. Selects the tone directive based on follow-up number
This is the **escalating tone system**:

```python
if follow_up_number >= 3:
    tone_directive = "Tone: direct and final. This is the LAST follow-up. Signal
    that you're closing the loop — e.g. 'last note before I move on' or 'closing
    the loop on this'. No desperation, just professional finality."
elif follow_up_number == 2:
    tone_directive = "Tone: confident with a brief value-add. Mention one specific
    skill or project that's relevant to the role as a secondary hook, but keep the
    follow-up framing dominant."
else:
    tone_directive = "Tone: polite and professional check-in. Keep it simple —
    reference the application and ask about status. No value-add needed."
```

This creates three distinct message personalities:
- **#1**: Pure status check. No selling, no pitching. Just "I applied X days ago for Y role, any updates?"
- **#2**: Status check + one sentence mentioning a relevant project/skill. The follow-up framing stays dominant.
- **#3**: Professional finality. Signals this is the last message. "Closing the loop" energy.

#### 4c. Constructs and sends the two-message prompt

**SYSTEM MESSAGE** (sent as `{"role": "system"}`):

```
You write ultra-short follow-up messages for job applications.
The sender has ALREADY applied — this is NOT a cold outreach or pitch.
{tone_directive}
CRITICAL: The message will be sent via LinkedIn connection request which has a
300 character limit. Every message MUST be under 300 characters.
```

**USER MESSAGE** (sent as `{"role": "user"}`):

```
Write follow-up #{follow_up_number} for my existing job application.

CONTEXT:
- I applied to {company_name} for the {role_title} role {days_since_applied} days ago
- No response yet
- This is follow-up attempt #{follow_up_number} of 3
- Platform: {original_platform}

MY PROFILE (for the optional value-add only — use ONLY if follow-up #2):
{sender_profile}

MESSAGE STRUCTURE (follow this order):
1. Reference the original application — mention the role and timeframe briefly
2. (Follow-up #2 only) One short clause of new value if it fits within the limit
3. Close with a simple ask about application status or next steps

RULES:
- MUST be under 300 characters total (this is a LinkedIn connection request limit)
- This is a FOLLOW-UP, not a cold DM. Do NOT pitch yourself from scratch.
- Do NOT open with project descriptions or technical capabilities
- No "just following up", "circling back", or "I hope this finds you well"
- No greetings like "Hi [Name]" — keep every character for content
- Do NOT mention Canada, immigration, or PR goals

Generate 1 follow-up message, ready to copy. Output ONLY the message text, nothing else.
```

**LLM API call parameters:**
- Written by Claude in the scheduled routine (no hosted model call)
- Temperature: `0.7` (moderate creativity)
- Max tokens: `300` (hard cap on output length)
- Message format: `[{"role": "system", ...}, {"role": "user", ...}]` — two-message pair, NOT a single user message

**Why two messages instead of one:**
The system message establishes the LLM's identity and constraints persistently. The user message provides the specific task. This separation prevents the LLM from "forgetting" it's writing a follow-up and drifting into cold-DM territory, which was the original bug.

### Step 5: User Copies and Sends the Message
The generated message appears in a result card with two buttons:
- **"Copy"** — copies to clipboard
- **"Log as Sent"** — opens a dialog to log this follow-up to the system

### Step 6: User Logs the Follow-Up
When the user clicks "Log as Sent":
1. The frontend fetches all applications via `getApplications()`
2. It auto-matches the company name to pre-select the right application
3. The user confirms, and the frontend calls `POST /api/follow-ups/log` with:
```json
{
  "entity_type": "application",
  "entity_id": 42,
  "message_content": "Applied for the AI-ML Systems Engineering Internship 7 days ago...",
  "channel": "LinkedIn"
}
```

### Step 7: Backend Processes the Log
The `POST /log` handler does **three things in sequence**:

#### 7a. `log_follow_up()`
1. **Counts existing history** to determine the follow-up number:
   ```python
   resp = db.table("follow_up_history").select("id")
       .eq("entity_type", entity_type).eq("entity_id", entity_id).execute()
   follow_up_number = len(resp.data) + 1
   ```
2. **Auto-marks all prior pending follow-ups as "no_response":**
   ```python
   db.table("follow_up_history").update({"follow_up_outcome": "no_response"})
       .eq("entity_type", entity_type).eq("entity_id", entity_id)
       .eq("follow_up_outcome", "pending").execute()
   ```
   This is critical for accurate effectiveness stats. If you sent follow-up #1 and got no response, then when you send follow-up #2, the system infers that #1 was a no_response. You don't have to manually update it.
3. **Inserts the new history record** with `follow_up_outcome: "pending"`.
4. Returns the `follow_up_number`.

#### 7b. `update_status()`
Called with `update_status(entity_id, "Follow-up Sent")`. This function handles **all the cadence logic**:

1. **If the new status is terminal** (`Offer`, `Rejected`, `Ghosted`, `Not Interested`):
   - Clears `follow_up_date` to NULL. No more follow-ups.

2. **If the new status is `"Follow-up Sent"`:**
   - Fetches the current `follow_up_count` and `date_applied` from the DB
   - Increments the count: `count = (app.get("follow_up_count") or 0) + 1`
   - **If count < 3** (cadence not exhausted):
     - Calculates the next follow-up date using `APPLICATION_CADENCE[count]`
     - Example: if `date_applied = 2026-02-28` and `count = 1` (just sent first follow-up), next follow-up = `2026-02-28 + 14 days = 2026-03-14`
     - Updates `follow_up_date` and `follow_up_count`
   - **If count >= 3** (cadence exhausted — all 3 follow-ups sent):
     - Clears `follow_up_date` to NULL
     - **Auto-sets status to `"Ghosted"`** — the system recognizes this company never responded after 3 attempts

3. **If the new status is `"Interview"`:**
   - Sets `follow_up_date` to `today + 3 days` (for a post-interview thank-you/check-in)

### Step 8: The Cycle Repeats
After the follow-up is logged:
- The application's `follow_up_date` advances to the next cadence step
- Next time the dashboard loads, if that date has passed, the application appears again in the "Follow-ups Due" section
- The user generates follow-up #2 (with the value-add tone), sends it, logs it
- The cycle repeats for #3 (final tone)
- After #3, the application auto-transitions to "Ghosted" and disappears from follow-ups

---

## 5. Snooze/Reschedule System

`snooze_follow_up()` provides a way to push a follow-up to a later date **without affecting the status or follow-up count**:

```python
def snooze_follow_up(app_id, new_date):
    db = _get_client()
    db.table("applications").update(
        {"follow_up_date": new_date}
    ).eq("id", app_id).execute()
```

**API:** `PATCH /api/applications/{id}/snooze` with `{"new_date": "2026-03-15"}`

**Frontend:** Each follow-up card on the dashboard has an inline `<input type="date">` picker. When the user selects a date, it immediately calls `snoozeFollowUp(fu.id, newDate)` and updates the local state so the card reflects the new date without a page reload.

Use case: You know the hiring manager is on vacation, or you want to wait until after a conference before following up.

---

## 6. Follow-Up History & Outcome Tracking

### Viewing History
`GET /api/follow-ups/history?entity_type=application&entity_id=42` returns all follow-up records for that application, ordered by `sent_at` descending (newest first). Each record includes:
```json
{
  "id": 5,
  "entity_type": "application",
  "entity_id": 42,
  "message_content": "Applied for the AI-ML role 7 days ago...",
  "channel": "LinkedIn",
  "follow_up_number": 1,
  "follow_up_outcome": "no_response",
  "sent_at": "2026-02-28T10:30:00Z"
}
```

This is displayed in the **Tracker page** as an expandable row under each application card — a table showing each follow-up attempt, the message sent, the channel, and a dropdown to change the outcome.

### Updating Outcomes
`PATCH /api/follow-ups/{history_id}/outcome` with `{"outcome": "responded"}` — used when a company replies to a follow-up. The user manually sets this from the tracker UI via an outcome dropdown with three options: `pending`, `responded`, `no_response`.

### Automatic "no_response" Transition
When a new follow-up is logged (Step 7a above), **all previous pending follow-ups for that same entity are automatically set to `"no_response"`**. This means:
- If you sent follow-up #1 and it's still "pending", then when you send #2, the system assumes #1 got no response
- You only need to manually mark outcomes when a company *does* respond
- This keeps the effectiveness data accurate without requiring manual bookkeeping for the negative case

---

## 7. Follow-Up Effectiveness Analytics

`get_follow_up_effectiveness()` aggregates all records from `follow_up_history` and returns three views:

### 7a. Overall Stats
```json
{
  "total": 25,
  "responded": 4,
  "rate": 16.0
}
```
Total follow-ups sent, how many got a response, and the percentage.

### 7b. By Channel
```json
[
  {"channel": "LinkedIn connection", "total": 20, "responded": 3, "rate": 15.0},
  {"channel": "LinkedIn", "total": 5, "responded": 1, "rate": 20.0}
]
```
Shows which communication channel gets the best response rates. This lets you learn over time whether the connection invitation or the later direct message does more of the work.

### 7c. By Attempt Number
```json
[
  {"follow_up_number": 1, "total": 15, "responded": 1, "rate": 6.7},
  {"follow_up_number": 2, "total": 8, "responded": 2, "rate": 25.0},
  {"follow_up_number": 3, "total": 2, "responded": 1, "rate": 50.0}
]
```
Shows which follow-up attempt is most effective. Common finding: later follow-ups have higher response rates because they filter down to companies that were genuinely considering you.

### Dashboard Display
The dashboard shows this data in a **Follow-up Effectiveness card**:
- Large number showing overall response rate
- Total sent count
- Responded count (in green)
- "By Channel" breakdown (if more than 1 channel used)
- "By Attempt" breakdown (if more than 1 attempt number exists)

---

## 8. Status State Machine

The follow-up system is tightly integrated with the application status lifecycle. Here's the complete state machine:

```
                    +------------------------------------------+
                    |                                          |
                    v                                          |
  +---------+  +--------------+  +--------------+  +-----------+
  | Applied |->| Follow-up    |->| Follow-up    |->| Follow-up |
  |         |  | Sent (#1)    |  | Sent (#2)    |  | Sent (#3) |
  +----+----+  +------+-------+  +------+-------+  +------+----+
       |              |                 |                  |
       |              |                 |                  v
       |              |                 |            +----------+
       |              |                 |            | Ghosted  |
       |              |                 |            | (auto)   |
       |              |                 |            +----------+
       |              |                 |
       v              v                 v
  +----------+  (any status can jump to Interview)
  |Interview |--------------------------------------+
  +----+-----+                                      |
       | +3 days follow-up                          |
       v                                            v
  +----------+                               +----------+
  |  Offer   |                               | Rejected |
  +----------+                               +----------+
  (terminal -- clears follow_up_date)        (terminal)
```

Key transitions and their follow-up effects:
- `Applied -> Follow-up Sent`: follow_up_count increments, next date calculated from cadence
- `Follow-up Sent (count >= 3)`: auto-transitions to `Ghosted`, clears follow_up_date
- `Any -> Interview`: sets follow_up_date to today + 3 days
- `Any -> Offer/Rejected/Ghosted/Not Interested`: clears follow_up_date (terminal states)

---

## 9. The Complete Data Flow (End-to-End)

```
User applies to a job
  -> add_application() -> sets follow_up_date = today + 7
      -> 7 days pass...
          -> get_follow_ups_due() -> surfaces on dashboard (sorted by urgency)
              -> User clicks "Generate Follow-up" on Messages page
                  -> Selects #1 (polite check-in)
                      -> POST /api/messages/follow-up
                          -> generate_follow_up() builds system + user prompt
                              -> queued; the Claude routine writes the 300-char message
                                  -> User copies message, sends via LinkedIn
                                      -> User clicks "Log as Sent"
                                          -> POST /api/follow-ups/log
                                              |-> log_follow_up()
                                              |     -> auto-marks prior pending as no_response
                                              |     -> inserts new history record
                                              |-> update_status("Follow-up Sent")
                                                    -> follow_up_count: 0 -> 1
                                                    -> follow_up_date: date_applied + 14
                                                        -> 7 more days pass...
                                                            -> Appears on dashboard again
                                                                -> User generates #2 (value-add tone)
                                                                    -> ... cycle repeats ...
                                                                        -> After #3: auto-Ghosted
```

---

## 10. API Endpoints Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/messages/follow-up` | Generate an AI follow-up message |
| `POST` | `/api/follow-ups/log` | Log a follow-up as sent (triggers cadence advancement) |
| `GET` | `/api/follow-ups/history?entity_type=...&entity_id=...` | Get follow-up history for an entity |
| `PATCH` | `/api/follow-ups/{id}/outcome` | Update a follow-up's outcome (pending/responded/no_response) |
| `GET` | `/api/follow-ups/effectiveness` | Get aggregated effectiveness analytics |
| `PATCH` | `/api/applications/{id}/status` | Update application status (triggers cadence logic) |
| `PATCH` | `/api/applications/{id}/snooze` | Reschedule a follow-up date without changing status |
| `GET` | `/api/stats/follow-ups` | Get all applications with follow-ups currently due |

---

## 11. File-by-File Reference

| File | Role | Key Functions |
|------|------|---------------|
| `backend/modules/tracker.py` | Database layer (all Supabase CRUD) | `add_application()`, `update_status()`, `get_follow_ups_due()`, `snooze_follow_up()`, `log_follow_up()`, `get_follow_up_history()`, `update_follow_up_outcome()`, `get_follow_up_effectiveness()` |
| `backend/modules/message_generator.py` | LLM prompt construction and API calls | `generate_follow_up()` (+ `generate_cold_dm`, `generate_cover_letter`, etc.) |
| `backend/app/routers/follow_ups.py` | API endpoints for follow-up history | `POST /log`, `GET /history`, `PATCH /{id}/outcome`, `GET /effectiveness` |
| `backend/app/routers/messages.py` | API endpoints for message generation | `POST /follow-up` (+ cold-dm, cover-letter, etc.) |
| `backend/app/routers/applications.py` | API endpoints for applications | `PATCH /{id}/status`, `PATCH /{id}/snooze` |
| `backend/app/models/schemas.py` | Pydantic request/response models | `FollowUpRequest`, `LogFollowUpRequest`, `UpdateFollowUpOutcomeRequest`, `SnoozeRequest` |
| `frontend/src/app/(app)/dashboard/page.tsx` | Dashboard UI | Follow-up cards with urgency badges, snooze date picker, effectiveness analytics |
| `frontend/src/app/(app)/messages/page.tsx` | Message generator UI | Follow-up form with # selector, generate + copy + log-as-sent flow |
| `frontend/src/app/(app)/tracker/page.tsx` | Application tracker UI | Expandable follow-up history per app, outcome dropdowns |
| `frontend/src/lib/api.ts` | Frontend API client | `generateFollowUp()`, `logFollowUp()`, `getFollowUpHistory()`, `updateFollowUpOutcome()`, `getFollowUpEffectiveness()`, `snoozeFollowUp()` |
| `frontend/src/lib/types.ts` | TypeScript interfaces | `FollowUp`, `FollowUpHistory`, `FollowUpEffectiveness`, `FollowUpRequest`, `LogFollowUpRequest` |
