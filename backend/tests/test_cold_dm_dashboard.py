"""Dashboard Cold DMs share the tracker's due schedule and stored draft state."""

import unittest
import sys
import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))
import tracker
import pending_messages
from test_settings_profile import ROOT, function


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.sort_column = None
        self.bounds = None

    def select(self, *_): return self

    def eq(self, column, value):
        self.filters.append(lambda row: row.get(column) == value)
        return self

    def lte(self, column, value):
        self.filters.append(lambda row: row.get(column) is not None and row[column] <= value)
        return self

    def in_(self, column, values):
        self.filters.append(lambda row: row.get(column) in values)
        return self

    def order(self, column):
        self.sort_column = column
        return self

    def range(self, start, end):
        self.bounds = (start, end)
        return self

    def execute(self):
        rows = [row for row in self.rows if all(test(row) for test in self.filters)]
        if self.sort_column:
            rows.sort(key=lambda row: row[self.sort_column])
        if self.bounds:
            rows = rows[self.bounds[0]:self.bounds[1] + 1]
        return SimpleNamespace(data=rows)


class Database:
    def __init__(self, applications, jobs, messages, history=None):
        self.rows = {"applications": applications, "scraped_jobs": jobs,
                     "job_messages": messages, "follow_up_history": history or []}
        self.queries = []

    def table(self, name):
        query = Query(self.rows[name])
        self.queries.append(name)
        return query


class ColdDmDashboardTests(unittest.TestCase):
    def test_first_connection_moves_only_its_job_from_cold_dm_to_followup_after_seven_local_days(self):
        apps = [{"id": i, "company": f"Company {i}", "role": "Engineer",
                 "url": f"https://jobs/{i}", "status": "Applied", "follow_up_date": "2026-09-27"}
                for i in range(1, 5)]
        jobs = [{"id": 900 + i, "company": f"Company {i}", "url": f"https://jobs/{i}"}
                for i in range(1, 5)]
        notes = [{"scraped_job_id": 900 + i, "message_type": "cold_dm", "content": "Current note",
                  "is_stale": False, "profile_version": 5} for i in range(1, 5)]
        history = [
            {"id": 1, "entity_type": "application", "entity_id": 2,
             "channel": "LinkedIn connection", "sent_at": "2026-09-20T18:29:00Z"},  # 23:59 Kolkata
            {"id": 2, "entity_type": "application", "entity_id": 3,
             "channel": "LinkedIn connection", "sent_at": "2026-09-20T18:31:00Z"},  # 00:01 Sep 21
            # The generic "LinkedIn" channel is NOT a recorded connection, so
            # job 4 stays a Cold DM however old this row is.
            {"id": 3, "entity_type": "application", "entity_id": 4,
             "channel": "LinkedIn", "sent_at": "2026-09-19T12:00:00Z"},
        ]
        db = Database(apps, jobs, notes, history)
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-27T12:00:00+05:30")
        ):
            self.assertEqual(tracker.get_post_connection_follow_ups_due()["id"].tolist(), [2])
            self.assertEqual([card["id"] for card in tracker.get_cold_dm_todos(5)], [1, 4])

    def test_recorded_send_schedules_next_followup_from_send_day_not_old_application_date(self):
        """The day-16 round is placed 8 days after the day-8 send is recorded,
        not 15 days after a long-past application date — a late send must not
        arrive already overdue."""
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "follow_up_count": 0, "date_applied": "2026-08-01"}
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-27T21:00:00+05:30")
        ):
            tracker.update_status(42, "Follow-up Sent")
        db.table.return_value.update.assert_called_once_with({
            "status": "Follow-up Sent", "follow_up_count": 1, "follow_up_date": "2026-10-05"})

    def test_invalid_connection_timestamp_neither_resends_nor_activates_followup(self):
        apps = [{"id": 8, "company": "Example", "role": "Engineer", "url": "https://jobs/8",
                 "status": "Applied", "follow_up_date": "2026-09-20"}]
        db = Database(apps, [{"id": 908, "company": "Example", "url": "https://jobs/8"}], [],
                      [{"id": 1, "entity_type": "application", "entity_id": 8,
                        "channel": "LinkedIn connection", "sent_at": "invalid"}])
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-27T12:00:00+05:30")
        ):
            self.assertTrue(tracker.get_post_connection_follow_ups_due().empty)
            self.assertEqual(tracker.get_cold_dm_todos(5), [])

    def test_claude_followup_queue_waits_for_recorded_connection_and_week(self):
        apps = [{"id": 8, "company": "Example", "role": "Engineer", "url": "https://jobs/8",
                 "status": "Follow-up Sent", "platform": "LinkedIn", "date_applied": "2026-08-01",
                 "follow_up_date": "2026-09-20"}]
        history = [{"id": 1, "entity_type": "application", "entity_id": 8,
                    "channel": "LinkedIn connection", "sent_at": "2026-09-21T00:00:00Z",
                    "recipient_name": "Viren Khatri", "message_content": "note",
                    "recipient_profile_url": "https://www.linkedin.com/in/viren/"}]
        db = Database(apps, [], [], history)
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-27T12:00:00+05:30")
        ), patch.object(pending_messages, "get_message_requests", return_value=[]), patch.object(
            tracker, "get_follow_up_history", return_value=history
        ), patch.object(tracker, "create_message_request", return_value={"id": 90}) as create:
            output = io.StringIO()
            with redirect_stdout(output):
                pending_messages.cmd_followups(None)
            self.assertEqual(json.loads(output.getvalue())["queued"], [])
            create.assert_not_called()
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-28T12:00:00+05:30")
        ), patch.object(pending_messages, "get_message_requests", return_value=[]), patch.object(
            tracker, "get_follow_up_history", return_value=history
        ), patch.object(tracker, "create_message_request", return_value={"id": 90}) as create:
            output = io.StringIO()
            with redirect_stdout(output):
                pending_messages.cmd_followups(None)
            self.assertEqual(json.loads(output.getvalue())["queued"][0]["application_id"], 8)
            params = create.call_args.args[1]
            self.assertEqual(params["follow_up_number"], 2)
            # Both halves of the recorded connection reach the writer: the name
            # to greet them, and the URL to open and confirm they accepted.
            self.assertEqual(params["recipient_name"], "Viren Khatri")
            self.assertEqual(params["recipient_profile_url"],
                             "https://www.linkedin.com/in/viren/")

    def test_dashboard_endpoint_uses_same_pdf_version_as_settings_prompt(self):
        captured = []
        env = {"get_cold_dm_todos": lambda version: captured.append(version) or []}
        env["_resume_version"] = function(ROOT / "app/routers/stats.py", "_resume_version", env)
        endpoint = function(ROOT / "app/routers/stats.py", "cold_dm_todos", env)
        metadata = SimpleNamespace(get_latest_profile_snapshot=lambda: {"version": 7})
        with patch.dict(sys.modules, {"profile": metadata}):
            self.assertEqual(endpoint(), [])
        self.assertEqual(captured, [7])
        # A profile that has never been snapshotted must not crash the card.
        captured.clear()
        with patch.dict(sys.modules, {"profile": SimpleNamespace(
                get_latest_profile_snapshot=lambda: None)}):
            self.assertEqual(endpoint(), [])
        self.assertEqual(captured, [None])

    def test_both_queues_off_one_snapshot_match_reading_them_separately(self):
        """Loading the Dashboard used to re-read the due applications and the
        recorded connection dates once per queue. Sharing one snapshot has to
        produce exactly what the separate reads did."""
        apps = [
            # Connected 2026-09-21, so its follow-up is due from 2026-09-28.
            {"id": 8, "company": "Connected", "role": "Engineer", "url": "https://jobs/8",
             "status": "Follow-up Sent", "platform": "LinkedIn",
             "date_applied": "2026-08-01", "follow_up_date": "2026-09-20"},
            # No connection recorded, so it belongs to the Cold DM queue.
            {"id": 9, "company": "Fresh", "role": "Analyst", "url": "https://jobs/9",
             "status": "Applied", "follow_up_date": "2026-09-20"},
        ]
        jobs = [{"id": 908, "company": "Connected", "url": "https://jobs/8"},
                {"id": 909, "company": "Fresh", "url": "https://jobs/9"}]
        notes = [{"scraped_job_id": 909, "message_type": "cold_dm", "content": "Note",
                  "is_stale": False, "profile_version": 5}]
        history = [{"id": 1, "entity_type": "application", "entity_id": 8,
                    "channel": "LinkedIn connection", "sent_at": "2026-09-21T00:00:00Z"}]
        db = Database(apps, jobs, notes, history)
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now",
            return_value=datetime.fromisoformat("2026-09-29T12:00:00+05:30")
        ):
            db.queries.clear()
            separate_follow_ups = tracker.get_post_connection_follow_ups_due()
            separate_cold_dms = tracker.get_cold_dm_todos(5)
            separate_reads = list(db.queries)
            # Now the shared path: one due list, one read of the dates.
            db.queries.clear()
            due = tracker.get_follow_ups_due()
            dates = tracker._linkedin_connection_dates(due["id"].tolist())
            shared_follow_ups = tracker.get_post_connection_follow_ups_due(due, dates)
            shared_cold_dms = tracker.get_cold_dm_todos(5, due, dates)
            shared_reads = list(db.queries)
        self.assertEqual(shared_follow_ups["id"].tolist(),
                         separate_follow_ups["id"].tolist())
        self.assertEqual(shared_cold_dms, separate_cold_dms)
        # Same answers, fewer reads — which is the whole point. The separate
        # path reads applications and follow_up_history once per queue; the
        # shared path reads each once for both.
        self.assertEqual(separate_reads.count("applications"), 2)
        self.assertEqual(separate_reads.count("follow_up_history"), 2)
        self.assertEqual(shared_reads.count("applications"), 1)
        self.assertEqual(shared_reads.count("follow_up_history"), 1)
        # ...and the split is the one the cadence calls for: the connected job
        # is a follow-up, the other is still a Cold DM.
        self.assertEqual(shared_follow_ups["id"].tolist(), [8])
        self.assertEqual([card["id"] for card in shared_cold_dms], [9])

    def test_due_cards_map_to_exact_jobs_and_only_current_drafts_are_ready(self):
        apps = [
            {"id": 10, "company": "First", "role": "Engineer", "url": "https://jobs/one", "status": "Applied", "follow_up_date": "2026-09-26"},
            {"id": 11, "company": "Second", "role": "Researcher", "url": "https://jobs/two", "status": "Applied", "follow_up_date": "2026-09-27"},
            {"id": 12, "company": "Third", "role": "Analyst", "url": "https://jobs/three", "status": "Applied", "follow_up_date": "2026-09-27"},
            {"id": 13, "company": "Closed", "role": "Engineer", "url": "https://jobs/closed", "status": "Rejected", "follow_up_date": "2026-09-25"},
            {"id": 14, "company": "Future", "role": "Engineer", "url": "https://jobs/future", "status": "Applied", "follow_up_date": "2026-09-28"},
        ]
        jobs = [{"id": 901, "company": "First", "url": "https://jobs/one"},
                {"id": 902, "company": "Second", "url": "https://jobs/two"},
                {"id": 904, "company": "Closed", "url": "https://jobs/closed"},
                {"id": 905, "company": "Future", "url": "https://jobs/future"}]
        messages = [
            {"scraped_job_id": 901, "message_type": "screen", "content": "PASS: Eligible", "is_stale": False, "profile_version": 5},
            {"scraped_job_id": 901, "message_type": "cold_dm", "content": "Verified job-specific note", "is_stale": False, "profile_version": 5},
            {"scraped_job_id": 902, "message_type": "cold_dm", "content": "Old resume note", "is_stale": True, "profile_version": 4},
            {"scraped_job_id": 902, "message_type": "resume_points", "content": "Wrong type", "is_stale": False},
            {"scraped_job_id": 904, "message_type": "cold_dm", "content": "Closed", "is_stale": False},
            {"scraped_job_id": 905, "message_type": "cold_dm", "content": "Future", "is_stale": False},
        ]
        db = Database(apps, jobs, messages)
        with patch.object(tracker, "_get_client", return_value=db), patch.object(
            tracker, "_user_now", return_value=datetime.fromisoformat("2026-09-27T12:00:00+05:30")
        ):
            cards = tracker.get_cold_dm_todos(5)

        self.assertEqual([(row["id"], row["scraped_job_id"], row["cold_dm_ready"]) for row in cards],
                         [(10, 901, True), (11, 902, False), (12, None, False)])
        self.assertEqual(cards[0]["follow_up_date"], "2026-09-26")
        self.assertIsInstance(cards[0]["scraped_job_id"], int)
        self.assertEqual(cards[1]["follow_up_date"], "2026-09-27")
        self.assertEqual(cards[1]["readiness_issue"], "No current Cold DM for the latest Settings PDF")
        self.assertEqual(cards[2]["readiness_issue"], "No matching scraped job")
        self.assertNotIn("content", cards[0])
        self.assertEqual(db.queries.count("job_messages"), 1)

    def test_empty_due_queue_does_not_query_drafts(self):
        db = Database([], [], [])
        with patch.object(tracker, "_get_client", return_value=db):
            self.assertEqual(tracker.get_cold_dm_todos(5), [])
        self.assertNotIn("job_messages", db.queries)

    def test_due_cards_are_not_silently_cut_off_at_data_api_page_size(self):
        apps = [{"id": n, "company": "Fixture", "role": "Engineer", "url": "",
                 "status": "Applied", "follow_up_date": "2026-09-26"} for n in range(1, 1002)]
        apps[-1]["url"] = "https://jobs/last"
        db = Database(apps, [{"id": 7001, "company": "Fixture", "url": "https://jobs/last"}], [
            {"scraped_job_id": 7001, "message_type": "screen", "content": "PASS: Eligible", "is_stale": False, "profile_version": 5},
            {"scraped_job_id": 7001, "message_type": "cold_dm", "content": "Last page note", "is_stale": False, "profile_version": 5},
        ])
        with patch.object(tracker, "_get_client", return_value=db):
            cards = tracker.get_cold_dm_todos(5)
        self.assertEqual(len(cards), 1001)
        self.assertEqual(cards[-1]["scraped_job_id"], 7001)
        self.assertTrue(cards[-1]["cold_dm_ready"])
        self.assertEqual(db.queries.count("applications"), 2)

    def test_current_draft_for_due_tracked_job_does_not_need_new_job_screen(self):
        db = Database([{"id": 10, "company": "Fixture", "role": "Engineer",
                        "url": "https://jobs/one", "status": "Applied", "follow_up_date": "2026-09-26"}],
                      [{"id": 901, "company": "Fixture", "url": "https://jobs/one"}],
                      [{"scraped_job_id": 901, "message_type": "cold_dm", "content": "Visible draft",
                        "is_stale": False, "profile_version": 5}])
        with patch.object(tracker, "_get_client", return_value=db):
            card = tracker.get_cold_dm_todos(5)[0]
        self.assertTrue(card["cold_dm_ready"])
        self.assertIsNone(card["readiness_issue"])
        self.assertEqual(card["scraped_job_id"], 901)


if __name__ == "__main__":
    unittest.main()
