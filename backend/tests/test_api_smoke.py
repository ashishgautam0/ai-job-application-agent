"""Smoke coverage that every read endpoint the frontend calls serialises.

The other lanes run without the API dependencies, so they cannot catch a break
that only appears once the real libraries are installed. That is exactly how
the tracker shipped broken: `pandas>=2.0.0` resolved to pandas 3 in the
deployed function, pandas 3 represents a text column's SQL NULL as NaN instead
of None, NaN is not valid JSON, and Starlette refused the whole response. Five
deployments went out with `GET /api/applications` returning a 500 and the
tracker page reading it as empty, with every lane green.

This lane installs requirements.txt, builds the real FastAPI app against a fake
Supabase client whose rows carry NULLs in every nullable column, and asserts
each endpoint returns 200 with strictly valid JSON and no NaN. It fails if a
resolved dependency changes how a missing value is rendered, or if a new
endpoint serialises a frame without sanitising it.
"""
import json
import math
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules"))

# NULL in every nullable column: the shape that broke production.
TABLES = {
    "applications": [
        {"id": 1, "company": "Alpha AI", "role": "AI Engineer", "type": "Job",
         "platform": "LinkedIn", "url": "https://jobs.test/1",
         "date_applied": "2026-10-01", "status": "Applied",
         "follow_up_date": "2026-10-08", "noc_compatible": "Unknown",
         "conversion_potential": "N/A", "salary_range": "", "notes": "",
         "created_at": "2026-10-01T00:00:00+00:00", "follow_up_count": 0,
         "hr_email_sent_at": "2026-10-02T00:00:00+00:00"},
        # Every nullable column NULL — one such row used to 500 the endpoint.
        {"id": 2, "company": "Beta Labs", "role": "ML Engineer", "type": "Job",
         "platform": "Wellfound", "url": "https://jobs.test/2",
         "date_applied": "2026-09-20", "status": "Applied",
         "follow_up_date": None, "noc_compatible": "Unknown",
         "conversion_potential": "N/A", "salary_range": "", "notes": "",
         "created_at": "2026-09-20T00:00:00+00:00", "follow_up_count": 0,
         "hr_email_sent_at": None},
    ],
    "scraped_jobs": [
        {"id": 11, "title": "AI Engineer", "company": "Alpha AI",
         "location": "Remote", "url": "https://jobs.test/1",
         "description": "LLM work", "source": "LinkedIn",
         "date_posted": "2026-10-01", "scraped_at": "2026-10-01T00:00:00+00:00",
         "match_score": 91, "status": "new", "screening_status": "pass",
         "screening_reason": "fits", "profile_version": 1, "is_dismissed": False},
        {"id": 12, "title": "ML Engineer", "company": "Beta Labs",
         "location": None, "url": "https://jobs.test/2",
         "description": None, "source": "Wellfound",
         "date_posted": None, "scraped_at": "2026-09-20T00:00:00+00:00",
         "match_score": None, "status": "new", "screening_status": None,
         "screening_reason": None, "profile_version": None, "is_dismissed": False},
    ],
    "job_messages": [
        {"id": 21, "scraped_job_id": 11, "message_type": "demo_html",
         "content": "<html></html>", "status": "ready",
         "generated_at": "2026-10-02T00:00:00+00:00", "is_stale": False,
         "profile_version": 1},
        {"id": 22, "scraped_job_id": 11, "message_type": "cold_dm",
         "content": "Hi Dana, I recently applied...", "status": "ready",
         "generated_at": "2026-10-02T00:00:00+00:00", "is_stale": False,
         "profile_version": 1},
    ],
    "follow_up_history": [
        {"id": 31, "entity_type": "application", "entity_id": 1,
         "channel": "LinkedIn connection", "message_content": None,
         "follow_up_number": 1, "follow_up_outcome": None,
         "sent_at": "2026-10-02T00:00:00+00:00",
         "created_at": "2026-10-02T00:00:00+00:00"},
    ],
    "company_research_cache": [
        {"id": 41, "company": "Alpha AI", "website": "https://alpha.test",
         "hiring_email": "jobs@alpha.test", "hiring_contact_name": "Dana",
         "researched_at": "2026-10-02T00:00:00+00:00"},
        {"id": 42, "company": "Beta Labs", "website": None,
         "hiring_email": None, "hiring_contact_name": None,
         "researched_at": "2026-09-21T00:00:00+00:00"},
    ],
    "mini_demos": [
        {"id": 51, "company": "Alpha AI", "role": "AI Engineer",
         "demo_idea": "RAG over their docs", "status": "active",
         "github_url": None, "demo_url": "https://demo.test",
         "hours_spent": None, "result": None,
         "created_at": "2026-10-01T00:00:00+00:00"},
    ],
    "message_requests": [],
    "resume_profiles": [
        {"id": 71, "username": "fixture", "version": 1, "source_kind": "pdf",
         "source_filename": "resume.pdf", "source_sha256": "abc",
         "extraction_method": "pypdf", "raw_text": "Resume evidence",
         "extracted_facts": {"skills": [{"name": "Python"}]}, "corrections": {},
         "evidence": {}, "readability": {}, "review_notes": "", "status": "active",
         "created_at": "2026-09-01T00:00:00+00:00",
         "reviewed_at": "2026-09-01T00:00:00+00:00",
         "activated_at": "2026-09-01T00:00:00+00:00"},
    ],
    "user_profile": [
        {"id": 1, "username": "fixture", "scoring_weights": {}, "automation_rules": {}},
    ],
    "notifications": [
        {"id": 61, "title": "Outreach", "body": "1 piece written",
         "type": "outreach", "metadata": None, "is_read": False,
         "created_at": "2026-10-02T00:00:00+00:00"},
    ],
}


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class Query:
    """Chainable stand-in for the Supabase query builder.

    Filters are deliberately no-ops: this lane proves the response serialises,
    which the filtering logic's own lanes already cover.
    """

    def __init__(self, rows):
        self._rows = rows
        self._count = None
        self._single = False
        self._range = None

    def select(self, *_args, **kwargs):
        self._count = kwargs.get("count")
        return self

    def single(self):
        self._single = True
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def __getattr__(self, name):
        # eq / neq / in_ / lte / gte / lt / gt / is_ / ilike / like / order /
        # limit / not_ / or_ — all return the builder unchanged.
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self

    def execute(self):
        rows = self._rows
        if self._range is not None:
            start, end = self._range
            rows = rows[start:end + 1]
        if self._single:
            return Result(rows[0] if rows else None)
        return Result(list(rows), self._count and len(rows))


class FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return Query(self.tables.get(name, []))

    # Storage is only reached by upload/download paths, not by these reads.
    @property
    def storage(self):
        raise AssertionError("a read endpoint must not touch Storage")


# Endpoints the frontend's Dashboard, Tracker and Jobs pages read.
ENDPOINTS = (
    "/api/applications",
    "/api/applications?status=Applied",
    "/api/applications?platform=LinkedIn",
    "/api/stats/dashboard",
    "/api/stats/weekly-trend",
    "/api/stats/platform-effectiveness",
    "/api/stats/role-analysis",
    "/api/stats/status-funnel",
    "/api/stats/hr-email-todos",
    "/api/stats/cold-dm-todos",
    "/api/stats/follow-ups",
    "/api/demos",
    "/api/scraped-jobs",
    "/api/notifications",
)


class ApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = FakeClient(TABLES)
        # tracker and profile each cache their own client global.
        import tracker
        import profile
        cls._restore = [(tracker, tracker._supabase_client),
                        (profile, profile._supabase_client)]
        tracker._supabase_client = client
        profile._supabase_client = client
        from fastapi.testclient import TestClient
        from app.main import app
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        """Restore the globals; a full-suite run shares one interpreter and the
        fake client must not leak into another test file."""
        for module, previous in cls._restore:
            module._supabase_client = previous

    def _get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200,
                         f"{path} returned {response.status_code}: {response.text[:400]}")
        return response.text

    def test_every_read_endpoint_returns_strictly_valid_json(self):
        for path in ENDPOINTS:
            with self.subTest(endpoint=path):
                body = self._get(path)
                # Browsers reject NaN/Infinity; so must the response.
                def reject(constant):
                    raise AssertionError(f"{path} emitted the invalid JSON constant {constant}")
                json.loads(body, parse_constant=reject)

    def test_no_endpoint_renders_a_missing_value_as_nan(self):
        """pandas 3 turns a NULL text column into NaN where pandas 2 kept None.
        A NULL must arrive as JSON null on every endpoint, under either."""
        for path in ENDPOINTS:
            with self.subTest(endpoint=path):
                for row in self._walk(json.loads(self._get(path))):
                    for key, value in row.items():
                        if isinstance(value, float) and math.isnan(value):
                            self.fail(f"{path} rendered {key} as NaN, not null")
                        self.assertNotEqual(value, "nan", f"{path}: {key} stringified a NULL")

    def test_the_tracker_returns_every_application_with_nulls_intact(self):
        rows = json.loads(self._get("/api/applications"))
        self.assertEqual(len(rows), len(TABLES["applications"]))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[1]["follow_up_date"], "2026-10-08")
        # The row whose nullable columns are all NULL is the regression.
        self.assertIsNone(by_id[2]["follow_up_date"])
        self.assertIsNone(by_id[2]["hr_email_sent_at"])
        self.assertEqual(by_id[2]["company"], "Beta Labs")

    def test_a_nullable_score_survives_as_null_on_the_jobs_page(self):
        rows = json.loads(self._get("/api/scraped-jobs"))
        scores = {row["id"]: row.get("match_score") for row in rows if "id" in row}
        self.assertIn(None, scores.values(), f"no NULL score survived: {scores}")

    def test_the_resolved_dependencies_are_reported(self):
        """Printed so a red lane shows which resolution broke it."""
        import fastapi
        import pandas
        import starlette
        versions = (f"python={sys.version.split()[0]} pandas={pandas.__version__} "
                    f"fastapi={fastapi.__version__} starlette={starlette.__version__}")
        print(f"\nresolved: {versions}", file=sys.stderr)
        self.assertTrue(pandas.__version__)

    @classmethod
    def _walk(cls, payload):
        """Yield every dict in a JSON response, nested ones included."""
        if isinstance(payload, dict):
            yield payload
            for value in payload.values():
                yield from cls._walk(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from cls._walk(item)


if __name__ == "__main__":
    unittest.main()
