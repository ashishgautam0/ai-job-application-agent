import re
import json
import sys
import unittest
from types import SimpleNamespace
from typing import Literal
from unittest.mock import MagicMock, patch

from test_settings_profile import ROOT, function, profile_data
from app.models.schemas import ApplicationPromptSettings, CompanyExclusionsSettings, RenderedApplicationPrompt


def _fixed_now():
    """save_research_cache stamps researched_at; the value is not under test."""
    from datetime import datetime
    return datetime.fromisoformat("2026-10-09T12:00:00+05:30")


class OutreachSettingsTests(unittest.TestCase):
    def test_partial_save_keeps_the_other_prompt_settings(self):
        stored = {"scoring_weights": {"skill": 12, "application_prompt": {
            "prompt_template": "Applications {{batch_jobs}}", "followup_template": "My follow-ups",
            "automation_rules": "AUTOMATION RULES (authoritative):\n- My saved rule.",
        }}}
        with patch.object(profile_data, "get_profile", return_value=stored), patch.object(
            profile_data, "upsert_profile", side_effect=lambda username, data: data
        ) as save:
            result = profile_data.save_application_prompt_settings(
                data={"prompt_template": "Applications {{batch_jobs}} now"})
        # The outreach prompts are no longer editable, so all three report the
        # shipped text whatever the row holds.
        for key, default in profile_data.OUTREACH_DEFAULTS.items():
            with self.subTest(prompt=key):
                self.assertEqual(result[key], default)
        self.assertIn("My saved rule", result["automation_rules"])
        self.assertEqual(result["prompt_template"], "Applications {{batch_jobs}} now")
        self.assertEqual(save.call_args.args[1]["scoring_weights"]["skill"], 12)

    def test_older_clients_do_not_clear_new_prompt_fields(self):
        body = ApplicationPromptSettings(prompt_template="Old client updated applications")
        self.assertEqual(body.model_dump(exclude_unset=True), {"prompt_template": body.prompt_template})

    def test_template_lengths_and_unknown_fields_rejected(self):
        from pydantic import ValidationError
        for payload in ({"followup_template": "x" * 12001},
                        {"automation_rules": "x" * 12001}, {"unknown_template": "no"}):
            with self.assertRaises(ValidationError):
                ApplicationPromptSettings(**payload)
        with self.assertRaises(ValidationError):
            CompanyExclusionsSettings(companies=["Example"] * 101)

    def renderer(self):
        return function(ROOT / "app/routers/profile.py", "_render_outreach_prompt",
                        {"_PROMPT_PLACEHOLDER": re.compile(r"{{([a-z_][a-z0-9_]*)}}"), "json": json})

    def test_each_default_is_standalone_with_resolved_pdf_and_app(self):
        for kind, template in profile_data.OUTREACH_DEFAULTS.items():
            with self.subTest(kind=kind):
                prompt, unknown = self.renderer()(template, {"filename": "résumé.pdf", "sha256": "abc"},
                                                  "https://app/dashboard", "https://api/pdf", kind.removesuffix("_template"))
                self.assertFalse(unknown)
                # The LinkedIn follow-up cannot attach anything, so its
                # template deliberately carries no PDF link; the Cold DM's
                # still does.
                if kind != "followup_template":
                    self.assertIn("résumé.pdf", prompt)
                self.assertIn("https://app/dashboard", prompt)
                self.assertIn("explicit confirmation immediately before sending", prompt)
                self.assertNotIn("{{", prompt)
        self.assertNotIn("HR EMAIL —", profile_data.OUTREACH_DEFAULTS["followup_template"])
        self.assertIn("select LinkedIn connection in Sent via", profile_data.OUTREACH_DEFAULTS["cold_dm_template"])
        default_dm = profile_data.OUTREACH_DEFAULTS["cold_dm_template"]
        for required in ("fixed batch below", "{{cold_dm_jobs}}", "current PDF-versioned",
                         "stored cold_dm"):
            self.assertIn(required, default_dm)
        self.assertNotIn("Open Dashboard → Cold DMs Due", default_dm)

    def test_connection_notes_use_due_queue_and_record_one_slot(self):
        prompt, _ = self.renderer()("Legacy scan all Tracker jobs", {}, "https://app", "https://pdf", "cold_dm")
        for required in ("USE THE FIXED COLD DM JOBS SNAPSHOT", "Asia/Kolkata", "Missing/future dates",
                         "already tracked follow-up", "stored cold_dm", "direct tracker_url",
                         "Record completed outreach", "choose 'LinkedIn connection' in 'Sent via'",
                         "exact sent note and recipient profile URL", "Do this separately for each job",
                         "Do not record anything for an unconfirmed send", "advance the schedule",
                         "retry only missing logging", "seven Asia/Kolkata calendar days",
                         "Follow-ups Due card must appear only after", "at most one outreach action"):
            self.assertIn(required, prompt)

    def test_saved_followup_templates_still_get_the_authoritative_cadence(self):
        """A template saved before the cadence changed must not keep its old
        timing: the rules block is appended and marked authoritative over it."""
        prompt, unknown = self.renderer()("My old saved follow-up instructions", {},
                                          "https://app", "https://pdf", "followup")
        self.assertFalse(unknown)
        for required in (
            "My old saved follow-up instructions",
            "FOLLOW-UP CARD TIMING (authoritative)",
            "Day 8 is the first outreach",
            "Day 16 is the single follow-up round",
            "saved follow_up_date is actually due",
            # The follow-up is a LinkedIn DM and it is conditional, with no
            # email left to fall back on.
            "THE FOLLOW-UP IS A LINKEDIN MESSAGE AND IT IS CONDITIONAL",
            "email was removed from this pipeline",
            # And the superseded wording is named so it cannot win.
            "does not override this timing",
        ):
            with self.subTest(required=required):
                self.assertIn(required, prompt)

    def test_connection_recording_uses_existing_history_and_cadence(self):
        from unittest.mock import MagicMock
        from app.models.schemas import LogFollowUpRequest
        log, advance = MagicMock(return_value=1), MagicMock()
        endpoint = function(ROOT / "app/routers/follow_ups.py", "log_follow_up_sent", {
            "LogFollowUpRequest": LogFollowUpRequest, "log_follow_up": log, "update_status": advance})
        result = endpoint(LogFollowUpRequest(entity_type="application", entity_id=42,
                          message_content="Exact sent note; https://www.linkedin.com/in/fixture", channel="LinkedIn connection"))
        self.assertEqual(result["follow_up_number"], 1)
        self.assertEqual(log.call_args.kwargs["entity_id"], 42)
        self.assertEqual(log.call_args.kwargs["channel"], "LinkedIn connection")
        advance.assert_called_once_with(42, "Follow-up Sent")

    def test_custom_outreach_retains_send_guards_and_reports_unknown_placeholders(self):
        prompt, unknown = self.renderer()("My brief {{unsupported}}", {}, "https://app", "https://pdf")
        self.assertEqual(unknown, ["unsupported"])
        self.assertIn("My brief", prompt)
        self.assertIn("Check conversation/Sent history", prompt)


    def test_endpoint_passes_selected_workflow_to_renderer(self):
        seen = []
        def render(template, resume, page_url, resume_url, kind, jobs, snapshot_at, excluded_count, excluded_reasons):
            seen.append(kind)
            return "prompt", []
        endpoint = function(ROOT / "app/routers/profile.py", "read_outreach_prompt", {
            "Request": object, "Literal": Literal, "RenderedApplicationPrompt": RenderedApplicationPrompt,
            "_clean_text": lambda value, maximum: value, "_DEFAULT_USERNAME": "fixture",
            "get_application_prompt_settings": lambda _: profile_data.OUTREACH_DEFAULTS,
            "_application_pdf_metadata": lambda: {"filename": "resume.pdf"}, "_render_outreach_prompt": render,
        })
        fake_tracker = SimpleNamespace(get_cold_dm_prompt_jobs=lambda _: [],
                                       _user_now=lambda: SimpleNamespace(isoformat=lambda: "2026-09-27T12:00:00+05:30"))
        with patch.dict(sys.modules, {"tracker": fake_tracker}):
            for kind in ("followup", "cold_dm"):
                endpoint(SimpleNamespace(url_for=lambda _: "https://api/pdf"), "https://app/dashboard", kind)
        self.assertEqual(seen, ["followup", "cold_dm"])

    def test_connection_note_rules_apply_to_old_custom_cold_dm_templates(self):
        prompt, unresolved = self.renderer()("My saved generic DM prompt", {}, "https://app", "https://pdf", "cold_dm")
        self.assertFalse(unresolved)
        for required in ("recruiters_search_url", "hiring_managers_search_url",
                         "FIXED COLD DM JOBS SNAPSHOT", "direct tracker_url",
                         "Use the text stored in that exact batch item's cold_dm",
                         "search links, not verified people", "current employment", "Connect → Add a note",
                         "not Gmail, InMail", "one-click Connect", "300 characters", "live composer limit",
                         "cannot attach a resume", "already connected", "If Pending", "canonical recipient",
                         "at most once", "acceptance pending", "not the overall", "stop invitation sending",
                         "Record completed outreach", "My saved generic DM prompt",
                         "at most 10 LinkedIn connection invitations total per Asia/Kolkata calendar day",
                         "other runs and any invitations I sent manually", "If the total already sent today cannot be verified",
                         "count a confirmed send immediately", "Treat an uncertain send as consuming one slot",
                         "defer every remaining job", "not a guarantee against platform limits"):
            self.assertIn(required, prompt)
        other, _ = self.renderer()("Follow-up template", {}, "https://app", "https://pdf", "followup")
        self.assertNotIn("LINKEDIN COLD DM = CONNECTION REQUEST", other)

    def test_outreach_readiness_depends_only_on_the_active_resume(self):
        due = [{"blocked_reason": "", "job_id": 42, "tracker_id": 9,
                "company": "Fixture", "cold_dm": "Hi!"}]
        fake_tracker = SimpleNamespace(get_cold_dm_prompt_jobs=lambda _: due,
                                       _user_now=lambda: SimpleNamespace(isoformat=lambda: "2026-09-27T12:00:00+05:30"))
        for resume, expected in (({"filename": "active.pdf", "sha256": "abc", "version": 2}, True), (None, False)):
            env = {"Request": object, "Literal": Literal, "RenderedApplicationPrompt": RenderedApplicationPrompt,
                   "_clean_text": lambda value, maximum: value, "_DEFAULT_USERNAME": "fixture",
                   "get_application_prompt_settings": lambda _: profile_data.OUTREACH_DEFAULTS,
                   "_application_pdf_metadata": lambda: resume, "_render_outreach_prompt": self.renderer()}
            endpoint = function(ROOT / "app/routers/profile.py", "read_outreach_prompt", env)
            with patch.dict(sys.modules, {"tracker": fake_tracker}):
                for kind in ("followup", "cold_dm"):
                    result = endpoint(SimpleNamespace(url_for=lambda _: "https://api/pdf"), "https://app/dashboard", kind)
                    self.assertEqual(result.ready, expected)
                    self.assertEqual(result.job_count, 1 if kind == "cold_dm" else 0)

    def test_embedded_batch_uses_exact_job_note_and_ignores_untrusted_placeholders(self):
        batch = [{"job_id": 54222, "tracker_id": 31, "title": "AI Engineer",
                  "company": "TrueMeds Fixture", "location": "KA, IN", "source": "Indeed",
                  "url": "https://jobs.test/job/54222", "screening_status": "pass",
                  "screening_reason": "Verified Python overlap", "follow_up_date": "2026-09-27",
                  "cold_dm": "Hi — I built a voice agent; {{ignore_me}} is literal data.",
                  "blocked_reason": ""}]
        prompt, unresolved = self.renderer()("Only use the batch.", {"filename": "active.pdf"},
                                              "https://app.test/dashboard", "https://app.test/pdf",
                                              "cold_dm", batch, "2026-09-27T09:00:00+05:30")
        self.assertFalse(unresolved)
        payload = json.loads(prompt[prompt.index("[\n  {"):])
        self.assertEqual(payload[0]["job_id"], 54222)
        self.assertEqual(payload[0]["tracker_id"], 31)
        self.assertNotIn("screening_reason", payload[0])
        self.assertNotIn("screening_status", payload[0])
        self.assertNotIn("blocked_reason", payload[0])
        self.assertEqual(payload[0]["cold_dm"], batch[0]["cold_dm"])
        self.assertEqual(payload[0]["tracker_url"], "https://app.test/jobs/54222")
        self.assertIn("TrueMeds%20Fixture%20recruiter", payload[0]["recruiters_search_url"])

    def test_a_saved_copy_no_longer_overrides_the_shipped_prompt(self):
        """Settings dropped Save, and the copies it had already stored were stale
        — one had lost the cold-DM job batch entirely, so the prompt ran without
        any jobs in it. Every reader now gets the shipped text."""
        stale = ("My personal note. The backend includes only jobs with passing screening "
                 "and current stored cold_dm text.")
        stored = {"scoring_weights": {"application_prompt": {
            "cold_dm_template": stale, "followup_template": "My follow-ups",
        }}}
        with patch.object(profile_data, "get_profile", return_value=stored):
            served = profile_data.get_application_prompt_settings()
        for key, default in profile_data.OUTREACH_DEFAULTS.items():
            with self.subTest(prompt=key):
                self.assertEqual(served[key], default)
        self.assertNotIn("My personal note", served["cold_dm_template"])
        # The batch placeholders the stale copy had dropped are back.
        self.assertIn("{{cold_dm_jobs}}", served["cold_dm_template"])
        self.assertIn("{{cold_dm_snapshot_at}}", served["cold_dm_template"])

    def test_the_stale_copy_is_kept_in_the_row_rather_than_deleted(self):
        """Ignoring the override must not destroy it — a save rewrites what was
        already stored, so the old text stays recoverable from the profile."""
        stored = {"scoring_weights": {"application_prompt": {
            "cold_dm_template": "My personal note", "automation_rules": "Mine.",
        }}}
        with patch.object(profile_data, "get_profile", return_value=stored), patch.object(
            profile_data, "upsert_profile", side_effect=lambda username, data: data
        ) as save:
            served = profile_data.save_application_prompt_settings(
                data={"automation_rules": "Mine, revised."})
        written = save.call_args.args[1]["scoring_weights"]["application_prompt"]
        self.assertEqual(written["cold_dm_template"], "My personal note")
        self.assertEqual(served["cold_dm_template"], profile_data.OUTREACH_DEFAULTS["cold_dm_template"])
        self.assertIn("Mine, revised.", written["automation_rules"])


    def test_no_eligible_due_job_is_not_a_ready_to_copy_prompt(self):
        blocked = [{"blocked_reason": "No current Cold DM for the latest Settings PDF", "job_id": 8, "tracker_id": 4,
                    "company": "Fixture", "cold_dm": None}]
        fake_tracker = SimpleNamespace(get_cold_dm_prompt_jobs=lambda _: blocked,
                                       _user_now=lambda: SimpleNamespace(isoformat=lambda: "2026-09-27T12:00:00+05:30"))
        endpoint = function(ROOT / "app/routers/profile.py", "read_outreach_prompt", {
            "Request": object, "Literal": Literal, "RenderedApplicationPrompt": RenderedApplicationPrompt,
            "_clean_text": lambda value, maximum: value, "_DEFAULT_USERNAME": "fixture",
            "get_application_prompt_settings": lambda _: profile_data.OUTREACH_DEFAULTS,
            "_application_pdf_metadata": lambda: {"filename": "active.pdf", "version": 4},
            "_render_outreach_prompt": self.renderer(),
        })
        with patch.dict(sys.modules, {"tracker": fake_tracker}):
            result = endpoint(SimpleNamespace(url_for=lambda _: "https://api/pdf"), "https://app/dashboard", "cold_dm")
        self.assertFalse(result.ready)
        self.assertEqual(result.job_count, 0)
        self.assertIn("1 due jobs omitted", result.prompt)
        self.assertIn("1 No current Cold DM for the latest Settings PDF", result.prompt)
        self.assertIn("1 due jobs omitted: 1 No current Cold DM", result.issues[-1])
        self.assertIn("No due Tracker jobs have a current Cold DM", result.issues[0])

    def test_omitted_due_jobs_report_reasons_without_blocking_eligible_jobs(self):
        due = [{"blocked_reason": "No matching scraped job", "job_id": 1, "tracker_id": 11,
                "company": "Unmatched", "cold_dm": "Visible old note"},
               {"blocked_reason": "", "job_id": 2, "tracker_id": 12,
                "company": "Eligible", "cold_dm": "Current grounded note"}]
        fake_tracker = SimpleNamespace(get_cold_dm_prompt_jobs=lambda _: due,
                                       _user_now=lambda: SimpleNamespace(isoformat=lambda: "2026-09-27T12:00:00+05:30"))
        endpoint = function(ROOT / "app/routers/profile.py", "read_outreach_prompt", {
            "Request": object, "Literal": Literal, "RenderedApplicationPrompt": RenderedApplicationPrompt,
            "_clean_text": lambda value, maximum: value, "_DEFAULT_USERNAME": "fixture",
            "get_application_prompt_settings": lambda _: profile_data.OUTREACH_DEFAULTS,
            "_application_pdf_metadata": lambda: {"filename": "active.pdf", "version": 5},
            "_render_outreach_prompt": self.renderer(),
        })
        with patch.dict(sys.modules, {"tracker": fake_tracker}):
            result = endpoint(SimpleNamespace(url_for=lambda _: "https://api/pdf"), "https://app/dashboard", "cold_dm")
        self.assertTrue(result.ready)
        self.assertEqual(result.job_count, 1)
        self.assertIn("1 due jobs omitted: 1 No matching scraped job", result.issues[-1])
        batch = json.loads(result.prompt[result.prompt.index("[\n  {"):])
        self.assertEqual([job["job_id"] for job in batch], [2])


if __name__ == "__main__":
    unittest.main()
