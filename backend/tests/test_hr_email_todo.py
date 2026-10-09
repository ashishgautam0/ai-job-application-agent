import ast
import io
import os
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
# cmd_save imports outreach_quality at call time. This lane installs nothing,
# and that module only needs `re`, so making it importable keeps the test on
# the real validator instead of a stub.
sys.path.insert(0, str(ROOT / "modules"))


def function(path, name, env):
    node = next(
        item for item in ast.parse(path.read_text()).body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return env[name]


class HrEmailTodoTests(unittest.TestCase):
    def test_mark_complete_and_reopen_persist_on_application(self):
        db = MagicMock()
        moment = datetime.fromisoformat("2026-09-20T12:00:00+05:30")
        update = function(
            ROOT / "modules/tracker.py",
            "set_hr_email_todo_completed",
            {"_get_client": lambda: db, "_user_now": lambda: moment},
        )

        self.assertEqual(update(42, True), moment.isoformat())
        db.table.assert_called_with("applications")
        db.table.return_value.update.assert_called_with({
            "hr_email_sent_at": moment.isoformat(),
        })
        db.table.return_value.update.return_value.eq.assert_called_with("id", 42)

        db.reset_mock()
        self.assertIsNone(update(42, False))
        db.table.return_value.update.assert_called_with({"hr_email_sent_at": None})

    def test_todays_hr_emails_are_counted_against_a_daily_target(self):
        """The Dashboard showed a daily target for applications and Cold DMs but
        not for the third day-8 action, so there was no way to see how much of
        the HR email queue had gone out today."""
        db = MagicMock()
        moment = datetime.fromisoformat("2026-09-20T12:00:00+05:30")
        count = function(
            ROOT / "modules/tracker.py", "count_hr_emails_today",
            {"_get_client": lambda: db, "_user_now": lambda: moment})

        db.table.return_value.select.return_value.gte.return_value.execute.return_value = \
            SimpleNamespace(count=4, data=None)
        self.assertEqual(count(), 4)
        db.table.assert_called_with("applications")
        # Counted from midnight in the user's timezone, not the last 24 hours.
        db.table.return_value.select.return_value.gte.assert_called_with(
            "hr_email_sent_at", moment.replace(hour=0, minute=0, second=0,
                                               microsecond=0).isoformat())

        # A driver that returns rows instead of a count must still total them.
        db.table.return_value.select.return_value.gte.return_value.execute.return_value = \
            SimpleNamespace(count=None, data=[{"id": 1}, {"id": 2}])
        self.assertEqual(count(), 2)

    def test_the_dashboard_reports_the_hr_email_target_beside_the_dm_one(self):
        """A failed count must not take the whole Dashboard down with it, which
        is why the Cold DM count is already wrapped."""
        source = (ROOT / "modules/tracker.py").read_text()
        self.assertIn("DAILY_HR_EMAIL_TARGET = 10", source)
        progress = function(
            ROOT / "modules/tracker.py", "_add_dm_progress",
            {"count_dms_today": lambda: 6, "DAILY_DM_TARGET": 10,
             "count_hr_emails_today": lambda: 3, "DAILY_HR_EMAIL_TARGET": 10})
        stats = {}
        progress(stats)
        self.assertEqual(stats, {"dms_today": 6, "dm_target": 10,
                                 "hr_emails_today": 3, "hr_email_target": 10})

        def boom():
            raise RuntimeError("database unavailable")
        failing = function(
            ROOT / "modules/tracker.py", "_add_dm_progress",
            {"count_dms_today": lambda: 6, "DAILY_DM_TARGET": 10,
             "count_hr_emails_today": boom, "DAILY_HR_EMAIL_TARGET": 10})
        stats = {}
        failing(stats)
        self.assertEqual(stats["hr_emails_today"], 0)
        self.assertEqual(stats["dms_today"], 6)

    def test_the_dashboard_shows_the_hr_email_target_card(self):
        page = (ROOT.parent / "frontend/src/app/(app)/dashboard/page.tsx").read_text()
        self.assertIn("Daily Target — HR Emails", page)
        self.assertIn("{hrCount} / {hrTarget} company HR emails sent today", page)
        self.assertIn("stats?.hr_emails_today ?? 0", page)
        # Three cards now, so the row has to widen past two columns.
        self.assertIn("md:grid-cols-2 xl:grid-cols-3", page)
        types = (ROOT.parent / "frontend/src/lib/types.ts").read_text()
        for field in ("hr_emails_today: number;", "hr_email_target: number;"):
            with self.subTest(field=field):
                self.assertIn(field, types)

    def test_pending_query_excludes_terminal_records_and_maps_true_job_id(self):
        app_rows = [
            {"id": 1, "url": "https://jobs.test/one", "status": "Applied"},
            {"id": 2, "url": "https://jobs.test/two", "status": "Rejected"},
        ]

        class Query:
            def __init__(self, rows):
                self.rows = rows
                self.null_filter = None
                self.date_filter = None
            def select(self, *_): return self
            def is_(self, column, value):
                self.null_filter = (column, value)
                return self
            def lte(self, column, value):
                self.date_filter = (column, value)
                return self
            def order(self, *_args, **_kwargs): return self
            def in_(self, *_): return self
            def execute(self): return types.SimpleNamespace(data=self.rows)

        app_query = Query(app_rows)
        job_query = Query([{"id": 91, "url": "https://jobs.test/one"}])

        class DB:
            def table(self, name):
                return app_query if name == "applications" else job_query

        analytics = types.ModuleType("analytics")
        analytics.attach_tracker_job_ids = lambda apps, jobs: [
            {**row, "scraped_job_id": next(
                (job["id"] for job in jobs if job["url"] == row["url"]), None
            )}
            for row in apps
        ]
        fake_pd = types.SimpleNamespace(DataFrame=lambda rows=(): list(rows))
        load = function(
            ROOT / "modules/tracker.py",
            "get_hr_email_todos",
            {
                "_get_client": DB,
                "TERMINAL_STATUSES": ["Offer", "Rejected", "Ghosted", "Not Interested"],
                "pd": fake_pd,
                "_user_now": lambda: datetime.fromisoformat("2026-09-30T10:00:00+05:30"),
                "timedelta": timedelta,
                "APPLICATION_CADENCE": [7, 15],
            },
        )
        original = sys.modules.get("analytics")
        sys.modules["analytics"] = analytics
        try:
            self.assertEqual(load(), [{
                "id": 1,
                "url": "https://jobs.test/one",
                "status": "Applied",
                "scraped_job_id": 91,
            }])
        finally:
            if original is None:
                sys.modules.pop("analytics", None)
            else:
                sys.modules["analytics"] = original
        self.assertEqual(app_query.null_filter, ("hr_email_sent_at", "null"))
        # Day 8 is the first outreach day, so only applications sent on or
        # before today minus the cadence's first gap are asked for.
        self.assertEqual(app_query.date_filter, ("date_applied", "2026-09-23"))

    def test_the_cadence_is_day_8_outreach_then_one_day_16_follow_up(self):
        """Day 1 tracked, day 8 HR email + connection note, day 16 the single
        follow-up round, then Ghosted. The gap between rounds is taken from the
        cadence so a late send does not make the next round instantly overdue."""
        # The shipped constant, read without importing supabase.
        source = (ROOT / "modules/tracker.py").read_text()
        cadence = next(line for line in source.splitlines()
                       if line.startswith("APPLICATION_CADENCE"))
        self.assertIn("[7, 15]", cadence)

        moment = datetime.fromisoformat("2026-09-29T10:00:00+05:30")
        db = MagicMock()
        add_with_db = function(ROOT / "modules/tracker.py", "add_application", {
            "_get_client": lambda: db, "_user_now": lambda: moment,
            "APPLICATION_CADENCE": [7, 15], "timedelta": timedelta,
        })
        add_with_db("Acme", "ML Engineer", "Full-time", "LinkedIn")
        inserted = db.table.return_value.insert.call_args.args[0]
        self.assertEqual(inserted["date_applied"], "2026-09-29")
        # Day 1 + 7 = day 8.
        self.assertEqual(inserted["follow_up_date"], "2026-10-06")

    def test_recording_the_day_8_send_schedules_day_16_then_stops(self):
        moment = datetime.fromisoformat("2026-10-06T10:00:00+05:30")

        def run(existing_count):
            db = MagicMock()
            (db.table.return_value.select.return_value.eq.return_value
               .single.return_value.execute.return_value.data) = {
                   "follow_up_count": existing_count}
            update = function(ROOT / "modules/tracker.py", "update_status", {
                "_get_client": lambda: db, "_user_now": lambda: moment,
                "APPLICATION_CADENCE": [7, 15], "timedelta": timedelta,
                "TERMINAL_STATUSES": ["Offer", "Rejected", "Ghosted", "Not Interested"],
                "INTERVIEW_FOLLOW_UP_DAYS": 3,
            })
            update(42, "Follow-up Sent")
            return db.table.return_value.update.call_args.args[0]

        # First round recorded on day 8 → next due 8 days later, day 16.
        first = run(0)
        self.assertEqual(first["follow_up_count"], 1)
        self.assertEqual(first["follow_up_date"], "2026-10-14")
        self.assertNotEqual(first.get("status"), "Ghosted")
        # Second round recorded → cadence exhausted, no third.
        second = run(1)
        self.assertEqual(second["follow_up_count"], 2)
        self.assertIsNone(second["follow_up_date"])
        self.assertEqual(second["status"], "Ghosted")

    def test_the_linkedin_follow_up_needs_an_accepted_connection(self):
        rules = (ROOT / "modules/outreach_prompts.py").read_text()
        for required in (
            "day 1 the job enters the Tracker",
            "Day 8 is the first outreach",
            "Day 16 is the single follow-up round",
            "LINKEDIN FOLLOW-UP DM IS CONDITIONAL",
            "once the invitation has been ACCEPTED",
            # The email is NOT gated on the connection.
            "whatever happened on LinkedIn",
        ):
            with self.subTest(required=required):
                self.assertIn(required, rules)
        agent = (ROOT.parent / ".claude/agents/followup.md").read_text()
        self.assertIn("requires an accepted connection", agent)
        self.assertIn("Never send another invitation in its place", agent)

    def test_schema_backfills_only_old_rows_before_new_todos_begin(self):
        schema = (ROOT.parent / "supabase/schema.sql").read_text()
        migration = (ROOT.parent / "supabase/add_hr_email_todo.sql").read_text()
        self.assertIn("hr_email_sent_at      timestamptz", schema)
        self.assertIn("where hr_email_sent_at is null", migration)
        self.assertIn("if not exists (", migration)
        self.assertLess(migration.index("update public.applications"),
                        migration.index("create index if not exists"))

    def test_dashboard_and_detail_expose_the_todo(self):
        """The README no longer documents the flow — it is intentionally empty —
        so the UI surfaces are what this guards."""
        dashboard = (ROOT.parent / "frontend/src/app/(app)/dashboard/page.tsx").read_text()
        detail = (ROOT.parent / "frontend/src/app/(app)/jobs/[id]/page.tsx").read_text()
        self.assertIn("Email Company HR", dashboard)
        self.assertIn("Mark emailed", dashboard)
        self.assertIn("Todo now — email Company HR", detail)

    def test_dashboard_lists_a_cold_dm_only_once_its_note_is_ready(self):
        """A due job whose note is not written yet is counted, not listed —
        there is nothing to review until the routine writes it."""
        dashboard = (ROOT.parent / "frontend/src/app/(app)/dashboard/page.tsx").read_text()
        self.assertIn("coldDmTodos.filter((todo) => todo.cold_dm_ready && todo.scraped_job_id)",
                      dashboard)
        self.assertIn("readyColdDms.map(", dashboard)
        self.assertNotIn("coldDmTodos.map(", dashboard)
        self.assertNotIn('"Not ready"', dashboard)

    def test_a_cold_dm_waits_for_the_jobs_demo_too(self):
        """The note carries this job's demo link, and this list only offers
        jobs with no draft at all — so a note written before the demo existed
        would keep its missing link for good."""
        apps = [{"url": "https://jobs.test/ready", "status": "Applied"},
                {"url": "https://jobs.test/no-demo", "status": "Applied"}]
        jobs = [{"id": 10, "url": "https://jobs.test/ready", "title": "AI Engineer",
                 "company": "Ready Co", "location": "", "description": "Role"},
                {"id": 20, "url": "https://jobs.test/no-demo", "title": "ML Engineer",
                 "company": "No Demo Co", "location": "", "description": "Role"}]

        class Query:
            def __init__(self, rows): self.rows = rows
            def select(self, *_): return self
            def in_(self, column, values):
                self.rows = [row for row in self.rows if row.get(column) in values]
                return self
            def execute(self): return SimpleNamespace(data=self.rows)

        tracker = types.ModuleType("tracker")
        tracker.TERMINAL_STATUSES = ["Offer", "Rejected", "Ghosted", "Not Interested"]
        tracker._get_client = lambda: SimpleNamespace(
            table=lambda name: Query(list(apps if name == "applications" else jobs)))
        tracker.get_job_message = lambda job_id, message_type: (
            {"content": "demo"} if message_type == "demo_html" and job_id == 10 else None)
        load = function(ROOT / "modules/pending_messages.py", "_tracked_jobs_missing", {"os": os})
        with patch.dict(sys.modules, {"tracker": tracker}):
            candidates, _ = load("cold_dm", 10)
        self.assertEqual([row["id"] for row in candidates], [10])
        # Asset types with no demo of their own are unaffected by the gate.
        with patch.dict(sys.modules, {"tracker": tracker}):
            other, _ = load("resume_points", 10)
        self.assertEqual([row["id"] for row in other], [20, 10])

    def test_the_capped_run_writes_the_soonest_due_jobs_first(self):
        """Every run is capped, so the order decides what gets written. Sorting
        by job id served the newest first and left the already-due ones until
        last, so a job could come due with no draft to send."""
        apps = [
            {"url": "https://jobs.test/newest", "status": "Applied", "follow_up_date": "2026-10-09"},
            {"url": "https://jobs.test/overdue", "status": "Applied", "follow_up_date": "2026-09-20"},
            {"url": "https://jobs.test/duetoday", "status": "Applied", "follow_up_date": "2026-09-30"},
            {"url": "https://jobs.test/nodate", "status": "Applied", "follow_up_date": None},
        ]
        jobs = [
            {"id": 900, "url": "https://jobs.test/newest", "title": "T", "company": "C",
             "location": "", "description": "d"},
            {"id": 100, "url": "https://jobs.test/overdue", "title": "T", "company": "C",
             "location": "", "description": "d"},
            {"id": 200, "url": "https://jobs.test/duetoday", "title": "T", "company": "C",
             "location": "", "description": "d"},
            {"id": 950, "url": "https://jobs.test/nodate", "title": "T", "company": "C",
             "location": "", "description": "d"},
        ]

        class Query:
            def __init__(self, rows): self.rows = rows
            def select(self, *_): return self
            def is_(self, *_): return self
            def lte(self, *_): return self
            def order(self, *_a, **_k): return self
            def in_(self, column, values):
                self.rows = [r for r in self.rows if r.get(column) in values]
                return self
            def execute(self): return types.SimpleNamespace(data=self.rows)

        tracker = types.ModuleType("tracker")
        tracker.TERMINAL_STATUSES = ["Offer", "Rejected", "Ghosted", "Not Interested"]
        tracker._get_client = lambda: types.SimpleNamespace(
            table=lambda name: Query(list(apps if name == "applications" else jobs)))
        tracker.get_job_message = lambda job_id, message_type: (
            {"content": "demo"} if message_type == "demo_html" else None)
        load = function(ROOT / "modules/pending_messages.py", "_tracked_jobs_missing", {"os": os})
        with patch.dict(sys.modules, {"tracker": tracker}):
            got, _ = load("cold_dm", 10)
        # Overdue, then due today, then the future one, and no date goes last.
        self.assertEqual([r["id"] for r in got], [100, 200, 900, 950])

        # The cap spends itself on the soonest-due jobs, not the newest.
        with patch.dict(sys.modules, {"tracker": tracker}):
            capped, _ = load("cold_dm", 2)
        self.assertEqual([r["id"] for r in capped], [100, 200])

    def test_hr_email_candidates_require_tracker_and_live_demo(self):
        apps = [
            {"url": "https://jobs.test/ready", "status": "Applied"},
            {"url": "https://jobs.test/no-demo", "status": "Applied"},
        ]
        jobs = [
            {"id": 10, "url": "https://jobs.test/ready", "title": "AI Engineer",
             "company": "Ready Co", "location": "", "description": "Role"},
            {"id": 20, "url": "https://jobs.test/no-demo", "title": "ML Engineer",
             "company": "No Demo Co", "location": "", "description": "Role"},
            {"id": 30, "url": "https://jobs.test/not-tracked", "title": "Other",
             "company": "Outside Co", "location": "", "description": "Role"},
        ]

        class Query:
            def __init__(self, rows): self.rows = rows
            def select(self, *_): return self
            def in_(self, column, values):
                self.rows = [row for row in self.rows if row.get(column) in values]
                return self
            def execute(self): return SimpleNamespace(data=self.rows)

        class DB:
            def table(self, name): return Query(list(apps if name == "applications" else jobs))

        tracker = types.ModuleType("tracker")
        tracker.TERMINAL_STATUSES = ["Offer", "Rejected", "Ghosted", "Not Interested"]
        tracker._get_client = DB
        tracker.get_job_message = lambda job_id, message_type: (
            {"content": "demo"} if message_type == "demo_html" and job_id == 10 else None
        )
        load = function(
            ROOT / "modules/pending_messages.py",
            "_tracked_jobs_missing",
            {"os": os},
        )
        with patch.dict(sys.modules, {"tracker": tracker}), \
             patch.dict(os.environ, {"PUBLIC_API_URL": "https://api.test/"}):
            candidates, tracked_total = load("hr_email", 10)

        self.assertEqual(tracked_total, 2)
        self.assertEqual([row["id"] for row in candidates], [10])
        self.assertEqual(candidates[0]["demo_url"], "https://api.test/api/demo/10")
        self.assertIn("latest PDF", candidates[0]["resume_attachment"])

    def test_hr_email_save_enforces_tracker_demo_link_resume_and_length(self):
        saved_content = {}
        tracker = types.ModuleType("tracker")
        tracker.is_scraped_job_tracked = lambda _job_id: True
        save = MagicMock(return_value=True)

        def get_message(job_id, message_type):
            if message_type == "demo_html":
                return {"content": "<html>demo</html>"}
            if message_type == "hr_email" and saved_content:
                return {"content": saved_content["content"]}
            return None

        def save_message(job_id, content, message_type):
            saved_content["content"] = content
            return save(job_id, content, message_type=message_type)

        command = function(
            ROOT / "modules/pending_messages.py",
            "cmd_save",
            {
                "get_job_message": get_message,
                "save_job_message": save_message,
                "os": os,
                "sys": sys,
            },
        )
        args = SimpleNamespace(job_id=10, type="hr_email", content=None)
        valid = (
            "To: careers@ready.test\nSubject: AI Engineer application\n\n"
            "Dear Hiring Team,\n\nI applied for the AI Engineer role. One verified "
            "project matches your needs, and I built this concise working demo: "
            "https://api.test/api/demo/10. My resume is attached for review.\n\n"
            "Best regards,\nSubidh Khanal"
        )

        cases = [
            valid.replace("https://api.test/api/demo/10", ""),
            valid.replace("My resume is attached for review.", ""),
            valid + " extra" * 151,
        ]
        with patch.dict(sys.modules, {"tracker": tracker}), \
             patch.dict(os.environ, {"PUBLIC_API_URL": "https://api.test"}):
            for content in cases:
                with self.subTest(content=content[-30:]), \
                     patch("sys.stdin", io.StringIO(content)), \
                     patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(command(args), 1)
            save.assert_not_called()

            with patch("sys.stdin", io.StringIO(valid)), \
                 patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(command(args), 0)
        save.assert_called_once()

        tracker.is_scraped_job_tracked = lambda _job_id: False
        saved_content.clear()
        save.reset_mock()
        with patch.dict(sys.modules, {"tracker": tracker}), \
             patch.dict(os.environ, {"PUBLIC_API_URL": "https://api.test"}), \
             patch("sys.stdin", io.StringIO(valid)), \
             patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(command(args), 1)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
