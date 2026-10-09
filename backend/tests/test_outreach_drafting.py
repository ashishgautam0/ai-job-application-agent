import io
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_settings_profile import ROOT, function
from outreach_quality import draft_recipient, unsourced_recipient
from message_generator import build_cold_dm_prompt, build_follow_up_prompt
from outreach_quality import UNKNOWN_RECIPIENT
from outreach_quality import validate_outreach_draft, wrong_demo_links


class DemoLinkValidationTests(unittest.TestCase):
    """A draft reused across two jobs carries the first job's demo link, which
    would point the second employer at a demo built for someone else's role."""

    DEMO = "https://uav-6qe7.vercel.app/api/demo/"

    def test_the_job_s_own_demo_link_passes(self):
        note = f"Hi [FIRST NAME], built a demo for the role: {self.DEMO}54144 — glad to connect."
        self.assertEqual(wrong_demo_links(note, 54144), [])
        self.assertIsNone(validate_outreach_draft("cold_dm", note, 54144))

    def test_another_job_s_demo_link_is_rejected(self):
        # The real slip: Lear Labs (54144) received Viraaj's draft and link.
        note = f"Hi [FIRST NAME], built a demo for the role: {self.DEMO}54075 — glad to connect."
        self.assertEqual(wrong_demo_links(note, 54144), ["54075"])
        problem = validate_outreach_draft("cold_dm", note, 54144)
        self.assertIsNotNone(problem)
        self.assertIn("54075", problem)


    def test_a_draft_with_no_demo_link_is_unaffected(self):
        self.assertEqual(wrong_demo_links("No link here.", 54144), [])
        self.assertIsNone(validate_outreach_draft("cold_dm", "Hi [FIRST NAME], no link here.", 54144))

    def test_string_and_int_job_ids_compare_the_same(self):
        note = f"{self.DEMO}54144"
        self.assertEqual(wrong_demo_links(note, "54144"), [])
        self.assertEqual(wrong_demo_links(note, 54144), [])

    def test_missing_job_id_skips_the_check_rather_than_failing(self):
        self.assertEqual(wrong_demo_links(f"{self.DEMO}1", None), [])
        self.assertIsNone(validate_outreach_draft("cold_dm", f"Hi [FIRST NAME], {self.DEMO}1"))

    def test_every_foreign_id_is_reported_once(self):
        note = f"{self.DEMO}1 and {self.DEMO}2 and {self.DEMO}1"
        self.assertEqual(wrong_demo_links(note, 3), ["1", "2"])


class SaveJobMessageDemoGuardTests(unittest.TestCase):
    """The check has to sit at the save choke point, not only in the CLI, or a
    subagent writing through another path can still store a mismatched link."""

    def saver(self, writes):
        db = MagicMock()
        db.table.return_value.upsert.return_value.execute.side_effect = (
            lambda: writes.append(True))
        return function(ROOT / "modules/tracker.py", "save_job_message", {
            "JOB_MESSAGE_TYPES": ("screen", "cold_dm", "resume_points", "demo_html"),
            "DEFAULT_MESSAGE_TYPE": "cold_dm",
            "_get_client": lambda: db,
            "datetime": __import__("datetime").datetime,
        })

    def test_a_foreign_demo_link_is_refused_before_any_write(self):
        writes = []
        saved = self.saver(writes)(
            54144, "demo: https://x/api/demo/54075", message_type="cold_dm",
            profile_version=3)
        self.assertFalse(saved)
        self.assertEqual(writes, [], "nothing may reach the database")

    def test_the_job_s_own_link_still_saves(self):
        writes = []
        saved = self.saver(writes)(
            54144, "demo: https://x/api/demo/54144", message_type="cold_dm",
            profile_version=3)
        self.assertTrue(saved)
        self.assertEqual(len(writes), 1)

    def test_a_long_cold_dm_still_saves_here(self):
        """Only the demo check moved to this layer; widening it would start
        rejecting drafts that save fine today."""
        writes = []
        saved = self.saver(writes)(54144, "x" * 400, message_type="cold_dm",
                                   profile_version=3)
        self.assertTrue(saved)


class OutreachDraftingTests(unittest.TestCase):


    def test_the_follow_up_is_a_linkedin_message_carrying_the_demo(self):
        """It was a Gmail email. Email is gone from the pipeline, so it goes
        where the first message went — a DM to the person who accepted the
        connection. Nothing can be attached there, so the demo link is what
        carries the evidence."""
        spec = build_follow_up_prompt("Acme", "ML Engineer", 7, original_platform="LinkedIn",
                                      profile_text="Fine-tuned an STT model",
                                      demo_url="https://demo/1",
                                      recipient_name="Dana")
        self.assertIsNone(spec["char_limit"])
        prompt = spec["prompt"]
        for required in ("This is a direct message to an existing 1st-degree connection",
                         "RECIPIENT: Dana, who accepted the connection request.",
                         "Exact live demo URL for this job: https://demo/1",
                         "A greeting using this person's first name.",
                         "50–80 words"):
            with self.subTest(required=required):
                self.assertIn(required, prompt)
        # No email shape survives, and nothing may claim an attachment.
        for banned in ("Subject:", "To: <the evidenced recipient",
                       "My resume is attached again."):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, prompt)
        self.assertIn("nothing can be attached to a LinkedIn message", prompt)
        # "just following up" survives only as a banned-pattern rule for the writer.
        self.assertIn('- "just following up", "circling back"', prompt)

    def test_a_follow_up_without_a_recipient_or_demo_invents_neither(self):
        prompt = build_follow_up_prompt("Acme", "ML Engineer", 7, profile_text="P")["prompt"]
        self.assertIn("RECIPIENT: none on record", prompt)
        self.assertIn("No demo exists for this job", prompt)
        self.assertNotIn("https://", prompt.split("Greeting:", 1)[0].replace(
            "r.neelam@company.com", ""))


    def test_recipient_parsing_survives_real_draft_shapes(self):
        self.assertEqual(draft_recipient("To:   Careers@ACME.com  \nSubject: x"),
                         "careers@acme.com")
        self.assertEqual(draft_recipient(f"To: {UNKNOWN_RECIPIENT}"), "")
        self.assertEqual(draft_recipient("no to line here"), "")
        # Case and surrounding whitespace must not defeat the match.
        self.assertEqual(unsourced_recipient("To: HR@Acme.com", ["hr@acme.com"]), "")
        self.assertEqual(unsourced_recipient("To: hr@acme.com", []), "hr@acme.com")


    def test_list_emits_exact_job_and_single_profile_snapshot(self):
        for kind in ("cold_dm",):
            profile = MagicMock(return_value="Verified Python coursework")
            command = function(ROOT / "modules/pending_messages.py", "cmd_list", {
                "_tracked_jobs_missing": lambda *_: ([{"id": 7, "company": "Acme", "title": "ML Engineer",
                    "description": "Exact JD", "demo_url": "https://demo/7"}], 1),
                "_profile_text": profile, "_demo_url_for_job": lambda _: "",
                "_company_intel_text": lambda _: "", "json": json, "sys": sys,
                })
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                command(SimpleNamespace(type=kind, limit=10))
                result = json.loads(out.getvalue())
            profile.assert_called_once()
            prompt = result["jobs"][0]["draft_spec"]["prompt"]
            self.assertIn("Exact JD", prompt)
            self.assertIn("Verified Python coursework", prompt)

    def test_direct_save_and_queue_reject_long_notes_without_writing(self):
        save = MagicMock()
        direct = function(ROOT / "modules/pending_messages.py", "cmd_save", {
            "sys": sys, "save_job_message": save})
        complete = MagicMock()
        queue = function(ROOT / "modules/pending_messages.py", "cmd_fulfil", {
            "sys": sys, "get_message_request": lambda _: {"message_type": "cold-dm", "params": {}},
            "_build_prompt": lambda *_: {"char_limit": 300}, "complete_message_request": complete})
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(direct(SimpleNamespace(type="cold_dm", job_id=7, content="x" * 301)), 1)
            self.assertEqual(queue(SimpleNamespace(request_id=8, content="x" * 301)), 1)
        save.assert_not_called()
        complete.assert_not_called()

    def test_the_note_follows_up_on_the_application_in_a_fixed_shape(self):
        """The old notes opened with praise ("...stood out") and never said the
        user had applied. The note is the follow-up on an application."""
        demo = "https://uav-6qe7.vercel.app/api/demo/53891"
        spec = build_cold_dm_prompt("Docusign", "GenAI Engineer", "LLM gateway work",
                                    profile_text="Fine-tuned an LLM", demo_url=demo)["prompt"]
        for required in ('1. "Hi [FIRST NAME], I recently applied for the GenAI Engineer role at Docusign."',
                         f'3. "I built a short demo for this role: {demo}."',
                         '4. "Glad to connect."', "stood out", "caught my eye"):
            with self.subTest(required=required):
                self.assertIn(required, spec)
        self.assertNotIn("earn a connection, not an interview", spec.lower())
        bare = build_cold_dm_prompt("Acme", "ML Engineer", "JD", profile_text="Python")["prompt"]
        self.assertIn("No demo exists for this job", bare)
        self.assertNotIn("/api/demo/", bare.split("Example (for shape only", 1)[0])

    def test_a_draft_without_the_name_placeholder_is_rejected(self):
        """A note reading "Hi, I recently applied..." is a complete-looking
        sentence, so a skipped personalisation ships silently — one went out to
        a founder addressed to nobody. The token makes the gap visible."""
        good = ("Hi [FIRST NAME], I recently applied for the ML Engineer role at Acme. "
                "Glad to connect.")
        self.assertIsNone(validate_outreach_draft("cold_dm", good))
        bare = good.replace("Hi [FIRST NAME],", "Hi,")
        self.assertIn("[FIRST NAME]", validate_outreach_draft("cold_dm", bare))
        guessed = good.replace("[FIRST NAME]", "Nitin")
        self.assertIsNotNone(validate_outreach_draft("cold_dm", guessed))

    def test_the_builder_asks_for_the_placeholder_not_a_bare_hi(self):
        spec = build_cold_dm_prompt("Acme", "ML Engineer", "JD",
                                    profile_text="P", demo_url="https://d/1")["prompt"]
        self.assertIn('1. "Hi [FIRST NAME], I recently applied for the ML Engineer role at Acme."',
                      spec)
        self.assertIn("Write the token [FIRST NAME] literally", spec)
        # The worked example must teach the same token, not a bare greeting.
        self.assertIn("Hi [FIRST NAME], I recently applied for the GenAI Engineer", spec)
        self.assertNotIn("\nHi, I recently applied", spec)

    def test_a_note_that_praises_the_company_is_rejected(self):
        for opener in ("Your real-time voice agents stood out.",
                       "Docusign's gateway work caught my eye.",
                       "Impressive platform!"):
            with self.subTest(opener=opener):
                self.assertIn("praises the company",
                              validate_outreach_draft("cold_dm", f"{opener} Glad to connect."))
        self.assertIsNone(validate_outreach_draft(
            "cold_dm", "Hi [FIRST NAME], I recently applied for the ML Engineer role at Acme. Glad to connect."))

    def test_saving_requires_the_application_and_this_jobs_demo(self):
        save = MagicMock(return_value=True)
        demo = "https://uav-6qe7.vercel.app/api/demo/7"
        def command(demo_url):
            return function(ROOT / "modules/pending_messages.py", "cmd_save", {
                "sys": sys, "save_job_message": save, "_demo_url_for_job": lambda _: demo_url,
                "get_job_message": lambda *a, **kw: {"content": "saved"}})
        good = (f"Hi [FIRST NAME], I recently applied for the ML Engineer role at Acme. I built RAG systems "
                f"like the one in the posting. I built a short demo for this role: {demo}. "
                "Glad to connect.")
        with patch("sys.stderr", new_callable=io.StringIO), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(command(demo)(SimpleNamespace(type="cold_dm", job_id=7,
                             content=good.replace("I recently applied for", "About"))), 1)
            self.assertEqual(command(demo)(SimpleNamespace(type="cold_dm", job_id=7,
                             content=good.replace(f" I built a short demo for this role: {demo}.", ""))), 1)
            save.assert_not_called()
            self.assertEqual(command(demo)(SimpleNamespace(type="cold_dm", job_id=7, content=good)), 0)
            # No demo yet: the note simply has no link.
            no_demo = good.replace(f" I built a short demo for this role: {demo}.", "")
            self.assertEqual(command("")(SimpleNamespace(type="cold_dm", job_id=7, content=no_demo)), 0)
        self.assertEqual(save.call_count, 2)

    def test_queue_keeps_valid_note_exactly(self):
        complete = MagicMock(return_value=True)
        queue = function(ROOT / "modules/pending_messages.py", "cmd_fulfil", {
            "sys": sys, "get_message_request": lambda _: {"message_type": "cold-dm", "params": {}},
            "_build_prompt": lambda *_: {"char_limit": 300}, "complete_message_request": complete})
        note = "Hi [FIRST NAME], interested in Acme’s ML role. My Python coursework is relevant; glad to connect."
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(queue(SimpleNamespace(request_id=8, content=note)), 0)
        complete.assert_called_once_with(8, note)
