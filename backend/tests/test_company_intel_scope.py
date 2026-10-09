import ast
import io
import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]


def function(path, name, env):
    node = next(
        item for item in ast.parse(path.read_text()).body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return env[name]


def _fixed_now():
    """save_research_cache stamps researched_at; the value is not under test."""
    from datetime import datetime
    return datetime.fromisoformat("2026-10-09T12:00:00+05:30")


class CompanyIntelScopeTests(unittest.TestCase):
    def test_cache_persists_website_and_hiring_contact_only(self):
        db = MagicMock()
        save = function(
            ROOT / "modules/tracker.py",
            "save_research_cache",
            {"_get_client": lambda: db, "_user_now": _fixed_now},
        )

        save("O'Brien Labs", {
            "product_url": "https://obrien.example",
            "description": "must not survive",
            "recent_news": "must not survive",
            "tech_signals": ["Python"],
            "hiring_contact": {
                "name": "Zoë O'Brien",
                "title": "Technical Recruiter",
                "linkedin_url": "https://www.linkedin.com/in/zoe-obrien",
            },
        })

        # The recipient and the page that published it are in scope; company
        # descriptions, news and tech signals still are not.
        payload = db.table.return_value.upsert.call_args.args[0]
        self.assertEqual(payload, {
            "company_name": "O'Brien Labs",
            "hiring_contact_name": "Zoë O'Brien",
            "hiring_contact_title": "Technical Recruiter",
            "hiring_contact_linkedin": "https://www.linkedin.com/in/zoe-obrien",
            "product_url": "https://obrien.example",
            # When it was researched, so a stale row can be told from a fresh
            # one. Still no description, news or tech signals.
            "researched_at": "2026-10-09T12:00:00+05:30",
        })
        db.table.return_value.upsert.assert_called_once_with(
            payload, on_conflict="company_name"
        )

    def test_cli_accepts_contact_without_website_and_rejects_bad_url(self):
        saved = MagicMock()
        fake_tracker = types.ModuleType("tracker")
        fake_tracker.save_research_cache = saved
        command = function(
            ROOT / "modules/pending_messages.py",
            "cmd_save_company",
            {"json": json, "sys": sys},
        )
        args = SimpleNamespace(name="Example Co")

        contact = {"hiring_contact": {
            "name": "Asha Rao", "title": "Recruiter",
            "linkedin_url": "https://www.linkedin.com/in/asha-rao",
        }}
        with patch.dict(sys.modules, {"tracker": fake_tracker}), \
             patch("sys.stdin", io.StringIO(json.dumps(contact))), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(command(args), 0)
        saved.assert_called_once_with("Example Co", {
            "product_url": "",
            "hiring_contact": contact["hiring_contact"],
        })

        saved.reset_mock()
        with patch.dict(sys.modules, {"tracker": fake_tracker}), \
             patch("sys.stdin", io.StringIO('{"product_url":"javascript:bad"}')), \
             patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(command(args), 1)
        saved.assert_not_called()

    def test_ui_and_cloud_agent_keep_the_requested_boundary(self):
        page = (ROOT.parent / "frontend/src/app/(app)/jobs/[id]/page.tsx").read_text()
        self.assertIn("Company website", page)
        for removed in ("intel.description", "intel.recent_news", "intel.tech_signals"):
            self.assertNotIn(removed, page)

        agent = (ROOT.parent / ".claude/agents/job-research.md").read_text()
        self.assertIn('"hiring_contact"', agent)
        self.assertIn("CONTACT:", agent)
        self.assertNotIn('"recent_news"', agent)
        self.assertNotIn('"tech_signals"', agent)

    def test_schema_and_migration_preserve_contact_columns(self):
        schema = (ROOT.parent / "supabase/schema.sql").read_text()
        table = schema.split("create table if not exists company_research_cache", 1)[1]
        table = table.split(");", 1)[0]
        for kept in ("product_url", "hiring_contact_name", "hiring_contact_title",
                     "hiring_contact_linkedin"):
            self.assertIn(kept, table)
        for removed in ("description", "recent_news", "tech_signals"):
            self.assertNotIn(removed, table)

        migration = (ROOT.parent / "supabase/trim_company_intel.sql").read_text()
        for kept in ("hiring_contact_name", "hiring_contact_title",
                     "hiring_contact_linkedin"):
            self.assertNotIn(f"drop column if exists {kept}", migration)
        for removed in ("description", "recent_news", "tech_signals"):
            self.assertIn(f"drop column if exists {removed}", migration)


if __name__ == "__main__":
    unittest.main()