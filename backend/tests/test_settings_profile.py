import ast
import json
import pathlib
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules"))
from resume_profile import profile_text
import profile as profile_data
from json_safe import json_records

# The settings-profile CI lane intentionally runs without the API dependencies.
# Endpoints are extracted with the response constructor supplied by the test.

def function(path, name, env):
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return env[name]


class SettingsProfileTests(unittest.TestCase):
    def test_response_uses_same_context_as_backend(self):
        public = function(ROOT / "app/routers/profile.py", "_public_snapshot", {"profile_text": profile_text})
        snapshot = {"version": 2, "facts": {"skills": [{"name": "Python"}]},
                    "raw_text": "PDF source evidence", "corrections": {"private": "internal"}}
        result = public(snapshot)
        self.assertEqual(result["backend_text"], profile_text(snapshot))
        self.assertNotIn("raw_text", result)
        self.assertNotIn("corrections", result)
        self.assertIsNone(public(None))

    def test_corrected_experience_label_is_rendered(self):
        text = profile_text({"version": 1, "facts": {"experience": [{
            "role": "Updated Engineer", "company": "Updated Company",
            "label": "OLD EXTRACTED LABEL", "start": "2025-01", "end": "present"}]}})
        self.assertIn("Updated Engineer Updated Company", text)
        self.assertNotIn("OLD EXTRACTED LABEL", text)

    def activate(self, status):
        db = MagicMock()
        db.rpc.return_value.execute.return_value.data = [{"id": 1, "version": 2}]
        env = {"_get_client": lambda: db,
               "get_resume_profile": lambda *_: {"status": status, "version": 2},
               "_snapshot": lambda row: row}
        activate = function(ROOT / "modules/profile.py", "activate_resume_profile", env)
        result = activate(1, {"skills": [{"name": "Python"}]})
        self.assertEqual(result["version"], 2)
        return db

    def test_active_edits_invalidate_all_dependent_types_before_rpc(self):
        db = self.activate("active")
        tables = [call.args[0] for call in db.table.call_args_list]
        self.assertEqual(tables, ["scraped_jobs", "job_messages", "cover_letter_drafts"])
        calls = [str(call) for call in db.mock_calls]
        self.assertLess(next(i for i,c in enumerate(calls) if "table().update" in c),
                        next(i for i,c in enumerate(calls) if c.startswith("call.rpc(")))

    def test_pending_activation_uses_existing_rpc_invalidation(self):
        db = self.activate("pending_review")
        db.table.assert_not_called()
        db.rpc.assert_called_once()

    def test_interview_prep_is_removed_but_the_resume_bucket_survives(self):
        """The 28-day prep feature is gone. Its Supabase bucket name is not: the
        resume upload writes there, so renaming it would orphan every PDF."""
        repo = ROOT.parent
        for path in ("backend/modules/prep28.py", "backend/app/routers/prep28.py",
                     "frontend/src/lib/prep28.ts", "frontend/src/app/(app)/prep28"):
            with self.subTest(path=path):
                self.assertFalse((repo / path).exists())
        for path, gone in (
            ("backend/app/main.py", "prep28"),
            ("backend/app/models/schemas.py", "Prep28"),
            ("frontend/src/lib/api.ts", "prep28"),
            ("frontend/src/lib/types.ts", "Prep28State"),
            ("frontend/src/app/(app)/dashboard/page.tsx", "prep28"),
            ("supabase/schema.sql", "prep28_progress"),
        ):
            with self.subTest(path=path):
                self.assertNotIn(gone, (repo / path).read_text())
        storage = (repo / "backend/modules/pdf_storage.py").read_text()
        self.assertIn('_PDF_BUCKET = "prep28-pdfs"', storage)
        self.assertIn("orphan every", storage)
        # The drop is offered as a migration to run deliberately, not applied.
        self.assertIn("drop table if exists public.prep28_progress",
                      (repo / "supabase/remove_prep28.sql").read_text())

    def test_settings_lists_the_prompts_first_and_the_resume_last(self):
        page = (ROOT.parent / "frontend/src/app/(app)/settings/page.tsx").read_text()
        order = [
            '<DesktopPrompt kind="applications" />',
            '<DesktopPrompt kind="outreach" />',
            '<h1 className="text-2xl font-bold">Resume profile</h1>',
            "Active backend resume",
        ]
        found = [page.index(marker) for marker in order]
        self.assertEqual(found, sorted(found),
                         "Settings sections are out of order: " + ", ".join(order))
        # The two cards are one component with a kind, not two components: the
        # old pair drifted apart, and a second way to generate either prompt is
        # a second way to send the same message twice.
        for retired in ('kind="cold_dm"', "Cold DM prompt",
                        'kind="followup"', "LinkedIn follow-up prompt",
                        "OutreachPrompt",
                        # No employer is excluded now, so the card that managed
                        # that list is gone rather than left doing nothing.
                        "Exclude companies from scraped jobs"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, page)
        self.assertFalse(
            (ROOT.parent / "frontend/src/app/(app)/settings/outreach-prompt.tsx").exists(),
            "the outreach prompt card has no caller left; delete it rather than "
            "leaving a second way to send the same message")

    def test_resume_upload_sits_in_the_page_header(self):
        page = (ROOT.parent / "frontend/src/app/(app)/settings/page.tsx").read_text()
        header = page.split("<h1 className=\"text-2xl font-bold\">Resume profile</h1>", 1)[1] \
                     .split("{loadError &&", 1)[0]
        self.assertIn("Upload resume", header)
        self.assertIn('type="file"', header)
        # One step now: picking the PDF extracts it, so the old two-stage
        # drop zone and its separate button are gone.
        self.assertNotIn("Choose or drop a PDF", page)
        self.assertNotIn("Extract for review", page)

    def test_settings_no_longer_builds_the_codex_batch_prompt(self):
        """That prompt drove Today Todo, which Settings no longer offers."""
        page = (ROOT.parent / "frontend/src/app/(app)/settings/page.tsx").read_text()
        for gone in ("Ready-to-paste Codex prompt", "Copy complete prompt for Codex",
                     "Generate current batch", "renderedPrompt"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, page)

    def test_followups_use_dashboard_and_separate_confirmed_history_logging(self):
        detail = (ROOT.parent / "frontend/src/app/(app)/jobs/[id]/page.tsx").read_text()
        self.assertIn('entity_id: application.id', detail)
        self.assertIn('message_content: sentFollowUp.trim()', detail)
        self.assertIn('event.follow_up_number >= followUpDraft!.follow_up_number!', detail)
        self.assertIn('setFollowUpRecordLocked(true)', detail)

if __name__ == "__main__":
    unittest.main()
