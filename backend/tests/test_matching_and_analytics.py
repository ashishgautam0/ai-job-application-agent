import json
import math
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT))

from analytics import attach_tracker_job_ids, platform_effectiveness, role_analysis, status_breakdown, weekly_trend
from jd_analyzer import _extract_degree_requirements, _extract_experience_requirement, full_analyze, quick_ats
from ranking import compute_application_priority
from resume_profile import extract_profile_facts, phrase_present, reviewed_experience_months
import tracker
from app.models.schemas import UserProfileRequest
from app.routers import scraper as scraper_router

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(text, version=1):
    extracted = extract_profile_facts(text, today=date(2026, 9, 18))
    return {"id": 1, "version": version, "status": "active", "source_kind": "pdf",
            "raw_text": text, "facts": extracted["facts"], "readability": extracted["readability"]}


class MatchingTests(unittest.TestCase):
    def test_legacy_text_resume_input_is_rejected(self):
        with self.assertRaises(ValidationError):
            UserProfileRequest.model_validate({"resume_text": "plain text is not an upload"})

    def test_boundary_matching_stops_substrings(self):
        self.assertFalse(phrase_present("MongoDB", "go"))
        self.assertFalse(phrase_present("object storage", "rag"))
        self.assertFalse(phrase_present("NoSQL", "sql"))
        self.assertTrue(phrase_present("Go and SQL", "go"))

    def test_benchmark_scores(self):
        cases = json.loads((FIXTURES / "matching_benchmark.json").read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertEqual(quick_ats(case["jd"], snapshot(case["resume"])), case["expected_score"])

    def test_experience_uses_union_of_real_dates(self):
        entries = [{"start": "2024-01", "end": "2024-12"}, {"start": "2024-06", "end": "2025-05"}]
        self.assertEqual(reviewed_experience_months(entries), 17)
        self.assertIsNone(reviewed_experience_months([]))
        reqs = _extract_experience_requirement("Minimum 3-5 years of experience required")
        self.assertEqual(reqs[0]["years"], 3)
        year_only = extract_profile_facts("Experience\nEngineer\n2024 - 2025", today=date(2026, 9, 18))["facts"]
        self.assertIsNone(year_only["total_experience_months"])
        self.assertEqual(year_only["experience"][0]["date_precision"], "year_only_requires_review")

    def test_degree_alternatives_take_minimum_and_unknown_is_review(self):
        req = _extract_degree_requirements("Bachelor's or Master's degree required")[0]
        self.assertEqual(req["level"], "bachelor")
        result = full_analyze("Engineer", "Bachelor's degree required. Python.", snapshot("Python developer"))
        self.assertEqual(result["mandatory_eligibility"]["overall"], "review")

    def test_empty_jd_is_unknown_not_perfect(self):
        result = full_analyze("Engineer", "", snapshot("Python FastAPI"))
        self.assertIsNone(result["resume_jd_match"]["score"])
        self.assertEqual(result["verdict"], "unknown")

    def test_priority_is_separate_and_mandatory_failure_blocks(self):
        job = {"analysis_details": {"mandatory_eligibility": {"status": "not_met"},
               "resume_jd_match": {"score": 99}}, "scraped_at": "2026-09-18T00:00:00Z", "verdict": "EASY_APPLY"}
        score, _ = compute_application_priority(job, job["analysis_details"])
        self.assertEqual(score, 0)

    def test_ranked_api_serializes_mixed_scored_and_unscored_jobs(self):
        """SQL NULL numeric values must remain JSON null, never Pandas NaN."""
        rows = pd.DataFrame([
            {
                "id": 1, "title": "Python Engineer", "company": "One",
                "description": "Python FastAPI role.", "location": "India",
                "scraped_at": "2026-09-18T12:00:00Z", "verdict": "EXTERNAL",
                "ats_score": 80, "skill_match": 75,
            },
            {
                "id": 2, "title": "AI Engineer", "company": "Two",
                "description": "Machine learning and Python.", "location": "India",
                "scraped_at": "2026-09-18T13:00:00Z", "verdict": "EXTERNAL",
                "ats_score": None, "skill_match": None,
            },
        ])
        active = snapshot("Skills\nPython\nFastAPI\nMachine learning")
        with (
            patch.object(scraper_router, "get_scraped_jobs", return_value=rows),
            patch.object(scraper_router, "_active_profile", return_value=active),
        ):
            result = scraper_router.ranked_scraped_jobs(limit=200)

        by_id = {row["id"]: row for row in result}
        self.assertIsNone(by_id[2]["ats_score"])
        self.assertIsNone(by_id[2]["skill_match"])
        self.assertFalse(any(
            isinstance(value, float) and math.isnan(value)
            for row in result for value in row.values()
        ))
        json.dumps(result, allow_nan=False)


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((FIXTURES / "analytics_applications.json").read_text())

    def test_platform_metric_has_correct_denominator(self):
        values = {row["platform"]: row for row in platform_effectiveness(self.rows)}
        self.assertEqual(values["LinkedIn"], {"platform": "LinkedIn", "applications": 2, "responses": 1, "response_rate": 50.0})
        self.assertEqual(values["Referral"]["response_rate"], 50.0)

    def test_status_breakdown_is_lossless(self):
        counts = status_breakdown(self.rows)
        self.assertEqual(sum(counts.values()), len(self.rows))
        self.assertEqual(counts["Assignment Submitted"], 1)
        self.assertEqual(counts["Ghosted"], 1)

    def test_weekly_trend_uses_dates_and_types(self):
        trend = weekly_trend(self.rows)
        self.assertEqual([row["week"] for row in trend], ["2026-09-07", "2026-09-14"])
        self.assertEqual(trend[0]["total"], 2)
        self.assertEqual(trend[0]["Internship"], 1)

    def test_every_application_lands_in_exactly_one_group(self):
        rows = role_analysis(self.rows)
        self.assertEqual(sum(row["applied"] for row in rows), len(self.rows))
        self.assertEqual(len({row["role_keyword"] for row in rows}), len(rows))


class RoleGroupingTests(unittest.TestCase):
    """The groups are learned from the job titles, not a list in the code.

    The old version sorted every job into seven hardcoded families. The catch-all
    one swallowed half of a real tracker, and kinds of role nobody had added —
    computer vision among them — fell into "Other / unclassified".
    """

    @staticmethod
    def groups(*titles):
        return {row["role_keyword"]: row["applied"]
                for row in role_analysis([{"role": t, "status": "Applied"} for t in titles])}

    def test_a_kind_of_role_nobody_anticipated_gets_its_own_group(self):
        """Nothing in the code mentions robotics; three of them is a pattern."""
        got = self.groups(*["Robotics Engineer"] * 3, *["AI Engineer"] * 3)
        self.assertEqual(got.get("Robotics"), 3)

    def test_a_broad_word_does_not_swallow_a_specific_title(self):
        """Every one of these says "AI", so a first-match rule files them all
        under AI. The rarest term in each title wins instead."""
        got = self.groups(*["AI Engineer"] * 3,
                          *["AI Engineer - Computer Vision"] * 3,
                          *["Applied AI Engineer, Computer Vision"] * 0,
                          *["AI Engineer (NLP)"] * 3)
        self.assertEqual(got.get("Computer Vision"), 3)
        self.assertEqual(got.get("NLP"), 3)
        self.assertEqual(got.get("AI"), 3)

    def test_one_subject_written_several_ways_is_one_group(self):
        got = self.groups("AI/ML Engineer", "AI ML Developer", "Machine Learning Engineer",
                          "ML Engineer", "Machine-Learning Intern")
        self.assertEqual(got, {"ML": 5})

    def test_word_order_does_not_split_a_group(self):
        # "Agent", "Agentic" and "Agents" are one subject, and the pair it
        # forms with "AI" must not become a second group beside it.
        got = self.groups("AI Agent Engineer", "Agentic AI Developer", "AI Agents Engineer")
        self.assertEqual(got, {"Agents": 3})

    def test_a_pair_only_groups_where_it_is_rarer_than_its_own_words(self):
        # Both titles say AI, so the pair "ai computervision" describes exactly
        # the titles "computervision" already does and adds nothing; the
        # shorter label is the honest one.
        got = self.groups(*["AI Engineer"] * 3, *["AI Engineer - Computer Vision"] * 3)
        self.assertEqual(got, {"AI": 3, "Computer Vision": 3})

    def test_seniority_and_working_arrangement_never_become_groups(self):
        """These describe how a job is packaged, not what it is about."""
        got = self.groups(*["Senior NLP Engineer (Remote)"] * 3,
                          *["Junior NLP Developer - Onsite Intern"] * 3)
        self.assertEqual(got, {"NLP": 6})

    def test_one_off_titles_fall_to_other_which_sorts_last(self):
        rows = role_analysis([{"role": t, "status": "Applied"} for t in
                              ["NLP Engineer"] * 3 + ["Quantum Cryptography Lead"]])
        self.assertEqual([row["role_keyword"] for row in rows], ["NLP", "Other"])
        self.assertEqual(rows[-1]["applied"], 1)

    def test_response_rate_is_counted_per_group(self):
        rows = role_analysis([
            {"role": "NLP Engineer", "status": "Offer"},
            {"role": "NLP Engineer", "status": "Applied"},
            {"role": "NLP Engineer", "status": "Rejected"},
        ])
        self.assertEqual((rows[0]["applied"], rows[0]["responses"],
                          rows[0]["response_rate"]), (3, 1, 33.3))

    def test_no_applications_is_no_groups(self):
        self.assertEqual(role_analysis([]), [])

    def test_tracker_navigation_uses_exact_id_and_preserves_missing(self):
        apps = [
            {"id": 7, "url": "https://jobs/one"},
            {"id": 8, "url": "https://jobs/missing"},
            {"id": 9, "url": "https://jobs/one-more"},
        ]
        mapped = attach_tracker_job_ids(apps, [
            {"id": 42, "url": "https://jobs/one"},
            {"id": 84, "url": "https://jobs/one-more"},
        ])
        self.assertEqual(mapped[0]["scraped_job_id"], 42)
        self.assertIsNone(mapped[1]["scraped_job_id"])
        self.assertEqual(mapped[2]["scraped_job_id"], 84)

    def test_application_list_emits_persisted_detail_ids(self):
        application_rows = [
            {"id": 1, "url": "https://jobs/alpha", "date_applied": "2026-09-18"},
            {"id": 2, "url": "https://jobs/beta", "date_applied": "2026-09-17"},
            {"id": 3, "url": "https://jobs/manual", "date_applied": "2026-09-16"},
        ]
        scraped_rows = [
            {"id": 101, "url": "https://jobs/alpha"},
            {"id": 202, "url": "https://jobs/beta"},
        ]

        class Query:
            def __init__(self, table): self.table = table
            def select(self, *_): return self
            def order(self, *_args, **_kwargs): return self
            def in_(self, key, values):
                self.values = values
                return self
            def execute(self):
                rows = application_rows if self.table == "applications" else scraped_rows
                return type("Response", (), {"data": rows})()

        class DB:
            def table(self, name): return Query(name)

        with patch.object(tracker, "_get_client", return_value=DB()):
            rows = tracker.get_all_applications().to_dict("records")
        self.assertEqual(
            [(row["id"], row["scraped_job_id"]) for row in rows],
            [(1, 101), (2, 202), (3, None)],
        )

    def test_follow_up_date_persists_iso_value(self):
        calls = {}
        class Query:
            def update(self, payload): calls["payload"] = payload; return self
            def eq(self, key, value): calls["where"] = (key, value); return self
            def execute(self): return None
        class DB:
            def table(self, name): calls["table"] = name; return Query()
        with patch.object(tracker, "_get_client", return_value=DB()):
            tracker.snooze_follow_up(9, "2026-10-03")
        self.assertEqual(calls, {"table": "applications", "payload": {"follow_up_date": "2026-10-03"}, "where": ("id", 9)})
        with self.assertRaises(ValueError):
            tracker.snooze_follow_up(9, "03/10/2026")


if __name__ == "__main__":
    unittest.main()
