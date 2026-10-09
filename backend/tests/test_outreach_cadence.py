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


class CompanyResearchSelectionTests(unittest.TestCase):
    """Which companies get researched, and when a cache row is refreshed."""

    MODULE = ROOT / "modules/pending_messages.py"

    def select(self, scraped, cache, now=None, limit=50):
        """Call cmd_companies with the data layer stubbed, and parse its JSON."""
        import contextlib
        import io
        import json as json_mod

        now = now or datetime.fromisoformat("2026-10-09T12:00:00+05:30")

        def table(name):
            node = MagicMock()
            if name == "scraped_jobs":
                node.select.return_value.gte.return_value.eq.return_value.execute.return_value = \
                    SimpleNamespace(data=[{"company": c} for c in scraped])
            else:
                node.select.return_value.execute.return_value = SimpleNamespace(data=cache)
            return node

        tracker = SimpleNamespace(_get_client=lambda: SimpleNamespace(table=table),
                                  _user_now=lambda: now)
        with patch.dict(sys.modules, {"tracker": tracker}):
            cmd = function(self.MODULE, "cmd_companies", {"json": json_mod, "sys": sys})
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd(SimpleNamespace(limit=limit))
        return json_mod.loads(buf.getvalue())["companies_needing_intel"]

    def test_a_company_with_a_cached_website_is_not_researched_again(self):
        got = self.select(scraped=["Known", "Unknown"],
                          cache=[{"company_name": "Known", "product_url": "https://k.test"}])
        self.assertEqual(got, ["Unknown"])

    def test_a_cached_row_without_a_website_still_needs_research(self):
        got = self.select(scraped=["No Site"],
                          cache=[{"company_name": "No Site", "product_url": ""}])
        self.assertEqual(got, ["No Site"])

    def test_the_limit_still_caps_the_batch(self):
        got = self.select(scraped=[f"Company {i}" for i in range(20)], cache=[], limit=3)
        self.assertEqual(len(got), 3)

    def test_research_no_longer_collects_or_caches_an_address(self):
        """Email is gone from the pipeline, so nothing stores one."""
        save = (ROOT / "modules/tracker.py").read_text()
        body = save.split("def save_research_cache", 1)[1].split("\n\n\n", 1)[0]
        self.assertNotIn("hiring_email", body)
        agent = (ROOT.parent / ".claude/agents/job-research.md").read_text()
        self.assertIn("Do not look for an email address", agent)
        self.assertNotIn("hiring_email", agent)

    def test_saving_research_stamps_when_it_was_researched(self):
        """The column default only fires on insert, so an upsert kept the old
        date: a row went stale at 14 days and stayed stale however often it was
        researched again."""
        db = MagicMock()
        moment = datetime.fromisoformat("2026-10-09T12:00:00+05:30")
        save = function(ROOT / "modules/tracker.py", "save_research_cache",
                        {"_get_client": lambda: db, "_user_now": lambda: moment})
        save("Acme", {"product_url": "https://acme.test"})
        written = db.table.return_value.upsert.call_args.args[0]
        self.assertEqual(written["researched_at"], moment.isoformat())
        self.assertEqual(written["product_url"], "https://acme.test")


class OutreachCadenceTests(unittest.TestCase):

    def test_the_cadence_is_day_8_outreach_then_one_day_16_follow_up(self):
        """Day 1 tracked, day 8 the connection note, day 16 the single
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
            "THE FOLLOW-UP IS A LINKEDIN MESSAGE AND IT IS CONDITIONAL",
            "once the invitation has been ACCEPTED",
            # There is no email to fall back on any more.
            "email was removed from this pipeline",
        ):
            with self.subTest(required=required):
                self.assertIn(required, rules)
        # Collapsed: these phrases wrap across lines, and a rewrap is not a
        # behaviour change.
        agent = " ".join((ROOT.parent / ".claude/agents/followup.md").read_text().split())
        self.assertIn("requires an accepted connection", agent)
        self.assertIn("Never send another invitation in its place", agent)
        self.assertIn("The follow-up is a LinkedIn message, not an email", agent)
        self.assertIn("Email is gone from this pipeline", agent)


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


if __name__ == "__main__":
    unittest.main()
