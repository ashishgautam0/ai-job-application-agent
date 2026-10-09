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
RenderedApplicationPrompt = SimpleNamespace

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

    def test_application_prompt_settings_are_allowlisted(self):
        """The form answers moved into the desktop prompt text, so they are no
        longer settings at all — only the prompt fields remain."""
        stored = {
            "scoring_weights": {
                "application_prompt": {
                    "prompt_template": "Mine {{batch_jobs}}",
                    "notice_period": "One month",
                    "unsupported": "must not escape",
                },
            },
        }
        with patch.object(profile_data, "get_profile", return_value=stored):
            result = profile_data.get_application_prompt_settings()
        self.assertEqual(result["prompt_template"], "Mine {{batch_jobs}}")
        self.assertTrue(result["automation_rules"].startswith("AUTOMATION RULES (authoritative):"))
        for gone in ("notice_period", "current_location", "submission_authorization",
                     "total_work_experience", "unsupported"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, result)

    def test_saving_application_prompt_preserves_other_scoring_settings(self):
        existing = {"scoring_weights": {"skill": 44, "application_prompt": {
            "prompt_template": "old {{batch_jobs}}"}}}
        captured = {}

        def save(username, data):
            captured.update(data)
            return {"username": username, **data}

        with patch.object(profile_data, "get_profile", return_value=existing), \
             patch.object(profile_data, "upsert_profile", side_effect=save):
            result = profile_data.save_application_prompt_settings(
                data={"prompt_template": "new {{batch_jobs}}", "unknown": "ignored"}
            )
        self.assertEqual(captured["scoring_weights"]["skill"], 44)
        self.assertEqual(result["prompt_template"], "new {{batch_jobs}}")
        self.assertIn("backend includes only jobs that passed current screening", result["automation_rules"])
        self.assertNotIn("unknown", captured["scoring_weights"]["application_prompt"])
        self.assertNotIn("notice_period", captured["scoring_weights"]["application_prompt"])

    def test_company_exclusions_roundtrip_without_erasing_profile_settings(self):
        rows = [{"scoring_weights": {"skill": 44, "application_prompt": {"prompt_template": "old"}}}]
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = rows

        def upsert(_username, data):
            rows[0] = data
            return data

        with patch.object(profile_data, "_get_client", return_value=db), patch.object(
            profile_data, "upsert_profile", side_effect=upsert
        ):
            self.assertEqual(profile_data.get_company_exclusions(), [])
            saved = profile_data.save_company_exclusions(companies=[
                " Rivet AI Ltd ", "RIVET AI", "Small Startup", "",
            ])
            self.assertEqual(profile_data.get_company_exclusions(), saved)
        self.assertEqual(saved, ["Rivet AI Ltd", "Small Startup"])
        self.assertEqual(rows[0]["scoring_weights"]["skill"], 44)
        self.assertEqual(rows[0]["scoring_weights"]["application_prompt"]["prompt_template"], "old")

    def test_company_exclusion_read_errors_stop_intake(self):
        with patch.object(profile_data, "_get_client", side_effect=RuntimeError("service unavailable")):
            with self.assertRaisesRegex(RuntimeError, "service unavailable"):
                profile_data.get_company_exclusions()

    def test_malformed_application_prompt_settings_are_treated_as_empty(self):
        with patch.object(profile_data, "get_profile", return_value={
            "scoring_weights": {"application_prompt": ["not", "a", "mapping"]},
        }):
            result = profile_data.get_application_prompt_settings()
        self.assertEqual(sorted(result), sorted(profile_data._APPLICATION_PROMPT_FIELDS))
        self.assertIn("AUTOMATION RULES (authoritative):", result["automation_rules"])
        self.assertIn("{{resume_url}}", result["prompt_template"])
        self.assertIn("No, I have not attended that company's selection process before", result["prompt_template"])
        self.assertIn("No, I have no commitment to another employer or organization", result["prompt_template"])
        self.assertIn("No, I have never worked for that company", result["prompt_template"])
        self.assertIn("For application-form questions, my total work experience is 1 year", result["prompt_template"])
        self.assertIn("answer Yes for any location", result["prompt_template"])
        self.assertIn("For an unrelated skill with no supplied or resume evidence, ask me", result["prompt_template"])

    def test_ready_prompt_resolves_fixed_batch_and_resume(self):
        render = function(
            ROOT / "app/routers/profile.py",
            "_render_application_prompt",
            {
                "json": json,
                "_PROMPT_PLACEHOLDER": re.compile(r"{{([a-z_]+)}}"),
                "DEFAULT_AUTOMATION_RULES": profile_data.DEFAULT_AUTOMATION_RULES,
            },
        )
        settings = {}
        template = (
            "{{page_url}}\n{{resume_filename}}\n"
            "{{resume_url}}\n{{resume_sha256}}\n{{batch_jobs}}"
        )
        jobs = [{
            "id": 91,
            "title": "ML Engineer",
            "company": "O'Reilly भारत",
            "location": "Remote",
            "source": "Indeed",
            "url": "https://jobs.example/91",
            "screening_status": "pass",
            "screening_reason": "Mandatory criteria verified",
            "description": "must not bloat the browser prompt",
        }]
        prompt, unresolved = render(
            template,
            settings,
            jobs,
            {"filename": "latest.pdf", "sha256": "abc123"},
            "https://app.example/tonight",
            "https://api.example/api/profile/resume/pdf",
        )
        self.assertFalse(unresolved)
        self.assertIn("Start working through the fixed batch immediately", prompt)
        self.assertIn("backend includes only jobs that passed current screening", prompt)
        self.assertNotIn('"screening_status"', prompt)
        self.assertNotIn('"screening_reason"', prompt)
        self.assertIn("No to having attended that employer's selection process before", prompt)
        self.assertIn("No to having a commitment to another employer", prompt)
        self.assertIn("No to having ever worked for that employer", prompt)
        self.assertIn("such as prior applications or employment with affiliates", prompt)
        self.assertIn("Read and accept required application terms, privacy/data-processing consents", prompt)
        self.assertIn("including when a saved custom template says to pause", prompt)
        self.assertIn("Do not opt into optional marketing", prompt)
        self.assertIn("unsupported factual assertion", prompt)
        self.assertIn("attempt the normal on-page challenge using supported browser", prompt)
        self.assertIn("If it cannot be completed, request the user's help, leave", prompt)
        self.assertIn("Never bypass the challenge or use a third-party solver", prompt)
        self.assertIn("Pause for login or a missing truthful answer", prompt)
        self.assertIn("no longer accepting applications", prompt)
        self.assertIn("permanently not found", prompt)
        self.assertIn("match job ID and URL; swipe left to Remove", prompt)
        self.assertIn("Do not mark it Applied or delete the database record", prompt)
        self.assertIn("For a temporary page error, login, unsolved CAPTCHA, or uncertain availability, leave its card", prompt)
        self.assertIn('"job_id": 91', prompt)
        self.assertIn("O'Reilly भारत", prompt)
        self.assertIn("https://api.example/api/profile/resume/pdf", prompt)
        self.assertNotIn("HR EMAIL —", prompt)
        self.assertNotIn("FOLLOW-UPS —", prompt)
        self.assertNotIn("click 'Mark emailed'", prompt)
        self.assertNotIn("must not bloat", prompt)
        for placeholder in ("page_url", "resume_filename",
                            "resume_url", "resume_sha256", "batch_jobs"):
            self.assertNotIn("{{" + placeholder + "}}", prompt)
        # Nothing appends an answers block any more; the answers live in the
        # Claude Desktop prompt file instead.
        self.assertNotIn("application answers", prompt.lower())

    def test_application_endpoint_excludes_nonpassing_jobs_before_omitting_results(self):
        renderer = function(ROOT / "app/routers/profile.py", "_render_application_prompt", {
            "json": json,
            "_PROMPT_PLACEHOLDER": re.compile(r"{{([a-z_]+)}}"),
            "DEFAULT_AUTOMATION_RULES": profile_data.DEFAULT_AUTOMATION_RULES,
        })
        settings = {"prompt_template": "Fixed batch:\n{{batch_jobs}}",
                    "automation_rules": profile_data.DEFAULT_AUTOMATION_RULES}
        endpoint = function(ROOT / "app/routers/profile.py", "read_rendered_application_prompt", {
            "Request": object, "RenderedApplicationPrompt": RenderedApplicationPrompt,
            "_clean_text": lambda value, maximum: value, "_DEFAULT_USERNAME": "fixture",
            "get_application_prompt_settings": lambda _: settings,
            "_application_pdf_metadata": lambda: {"filename": "active.pdf", "sha256": "abc"},
            "_render_application_prompt": renderer,
            "json_records": json_records,
        })
        jobs = [{"id": i, "title": f"Job {i}", "company": "Fixture", "url": f"https://jobs.test/{i}",
                 "screening_status": status, "screening_reason": f"Private {status} reason"}
                for i, status in enumerate(("pass", "pending", "review", "fail"), 1)]
        with patch.dict(sys.modules, {"tracker": SimpleNamespace(get_scraped_jobs=lambda: jobs)}):
            result = endpoint(SimpleNamespace(url_for=lambda _: "https://api.test/pdf"),
                              "https://app.test/tonight")
        self.assertTrue(result.ready)
        self.assertEqual(result.job_count, 1)
        batch = json.loads(result.prompt[result.prompt.index("[\n  {"):])
        self.assertEqual([job["job_id"] for job in batch], [1])
        self.assertNotIn("screening_status", batch[0])
        self.assertNotIn("screening_reason", batch[0])
        self.assertNotIn("Private", result.prompt)
        with patch.dict(sys.modules, {"tracker": SimpleNamespace(get_scraped_jobs=lambda: jobs[1:])}):
            blocked = endpoint(SimpleNamespace(url_for=lambda _: "https://api.test/pdf"),
                               "https://app.test/tonight")
        self.assertFalse(blocked.ready)
        self.assertEqual(blocked.job_count, 0)

    def test_legacy_screening_wording_is_migrated_without_erasing_other_rules(self):
        old_rules = ("AUTOMATION RULES (authoritative):\n" + profile_data._OLD_APPLICATION_RULE_SCREENING +
                     "\nKeep my custom rule.")
        old_template = ("Custom intro\n" + profile_data._OLD_APPLICATION_TEMPLATE_SCREENING +
                        "\n{{batch_jobs}}")
        stored = {"scoring_weights": {"application_prompt": {
            "automation_rules": old_rules, "prompt_template": old_template}}}
        with patch.object(profile_data, "get_profile", return_value=stored):
            settings = profile_data.get_application_prompt_settings()
        self.assertNotIn("screening_status", settings["automation_rules"] + settings["prompt_template"])
        self.assertIn("Keep my custom rule", settings["automation_rules"])
        self.assertIn("Custom intro", settings["prompt_template"])

    def test_ready_prompt_reports_unknown_placeholder(self):
        render = function(
            ROOT / "app/routers/profile.py",
            "_render_application_prompt",
            {
                "json": json,
                "_PROMPT_PLACEHOLDER": re.compile(r"{{([a-z_]+)}}"),
                "DEFAULT_AUTOMATION_RULES": profile_data.DEFAULT_AUTOMATION_RULES,
            },
        )
        _, unresolved = render(
            "Run {{unsupported_field}} for {{batch_jobs}}",
            {},
            [],
            None,
            "https://app.example/tonight",
            "https://api.example/api/profile/resume/pdf",
        )
        self.assertEqual(unresolved, ["unsupported_field"])

    def test_saved_automation_rules_replace_defaults_and_render_placeholders(self):
        stored = {"scoring_weights": {"skill": 33, "application_prompt": {
            "automation_rules": "AUTOMATION RULES (authoritative):\n- Visit {{page_url}} for this batch.",
            "prompt_template": "Apply to {{batch_jobs}}",
        }}}
        captured = {}
        with patch.object(profile_data, "get_profile", return_value=stored), patch.object(
            profile_data, "upsert_profile", side_effect=lambda username, data: captured.update(data) or data
        ):
            saved = profile_data.save_application_prompt_settings(
                data={"prompt_template": "Apply tomorrow to {{batch_jobs}}"})
        self.assertEqual(captured["scoring_weights"]["skill"], 33)
        self.assertEqual(saved["automation_rules"], stored["scoring_weights"]["application_prompt"]["automation_rules"])
        render = function(ROOT / "app/routers/profile.py", "_render_application_prompt", {
            "json": json,
            "_PROMPT_PLACEHOLDER": re.compile(r"{{([a-z_]+)}}"),
            "DEFAULT_AUTOMATION_RULES": profile_data.DEFAULT_AUTOMATION_RULES,
        })
        prompt, unresolved = render(saved["prompt_template"], saved, [{"id": 42}], None,
                                    "https://app.example/tonight", "https://api.example/resume")
        self.assertFalse(unresolved)
        self.assertIn("- Visit https://app.example/tonight for this batch.", prompt)
        self.assertIn('"job_id": 42', prompt)
        self.assertNotIn("Start working through the fixed batch immediately", prompt)
        custom, unresolved = render("Apply", {"automation_rules": "{{missing_rule_value}}"}, [], None,
                                    "https://app.example/tonight", "https://api.example/resume")
        self.assertIn("{{missing_rule_value}}", custom)
        self.assertEqual(unresolved, ["missing_rule_value"])

    def test_standalone_hr_workflow_retains_queue_and_send_guards(self):
        prompt = profile_data.OUTREACH_DEFAULTS["hr_email_template"]
        self.assertIn("Do not generate or send HR email before tracking", prompt)
        self.assertIn("HR email pending assets", prompt)
        self.assertIn("HR email blocked: mail access required", prompt)
        self.assertIn("Do not treat saved application", prompt)
        self.assertIn("If already completed, skip", prompt)
        self.assertIn("never guess a Tracker ID", prompt)
        self.assertIn("Use only its 'Email Company HR' todo section", prompt)
        self.assertIn("can include previously tracked jobs", prompt)
        self.assertIn("click that dashboard card", prompt)
        self.assertIn("Do not scan company details or every Tracker record", prompt)
        self.assertIn("report a queue error", prompt)
        self.assertIn("verify that todo is no longer pending", prompt)
        self.assertNotIn("For each eligible job in this fixed batch", prompt)
        self.assertNotIn("pay a fee, send email, or apply", prompt)

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
            "<DesktopPrompt />",
            'kind="hr_email"',
            'kind="followup"',
            "Exclude companies from scraped jobs",
            '<h1 className="text-2xl font-bold">Resume profile</h1>',
            "Active backend resume",
        ]
        found = [page.index(marker) for marker in order]
        self.assertEqual(found, sorted(found),
                         "Settings sections are out of order: " + ", ".join(order))
        # The Cold DM prompt card is retired: the desktop prompt sends cold DMs
        # end to end, so a second place to generate them is only a way to send
        # the same note twice.
        self.assertNotIn('kind="cold_dm"', page)
        self.assertNotIn("Cold DM prompt", page)

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
        followup = profile_data.OUTREACH_DEFAULTS["followup_template"]
        for requirement in ("Dashboard's 'Follow-ups Due'", "Click each dashboard follow-up card",
                            "skip future dates", "defer the follow-up", "pending draft",
                            "existing conversation/channel", "actual latest Settings PDF attachment",
                            "confirmation immediately before Send", "never blindly resend",
                            "Record sent follow-up", "do not also change status",
                            "at most one follow-up per record", "Never fabricate history"):
            self.assertIn(requirement, followup)
        detail = (ROOT.parent / "frontend/src/app/(app)/jobs/[id]/page.tsx").read_text()
        self.assertIn('entity_id: application.id', detail)
        self.assertIn('message_content: sentFollowUp.trim()', detail)
        self.assertIn('event.follow_up_number >= followUpDraft!.follow_up_number!', detail)
        self.assertIn('setFollowUpRecordLocked(true)', detail)

if __name__ == "__main__":
    unittest.main()
