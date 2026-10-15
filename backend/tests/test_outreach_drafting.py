import io
import json
import re
import types
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_settings_profile import ROOT, function
from message_generator import build_cold_dm_prompt, build_follow_up_prompt
from outreach_quality import validate_outreach_draft, wrong_demo_links

_TRAILER = re.compile(r"\n\s*Sent to [^\n]*\Z")


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


    def test_the_follow_up_is_a_nudge_not_a_second_pitch(self):
        """The recipient accepted the invitation and read the connection note,
        so they already hold the pitch, the matching fact and the demo link.
        Re-sending any of it makes the message longer, not more useful."""
        spec = build_follow_up_prompt("Acme", "ML Engineer", 7, original_platform="LinkedIn",
                                      profile_text="Fine-tuned an STT model",
                                      demo_url="https://demo/1",
                                      recipient_name="Dana")
        self.assertIsNone(spec["char_limit"])
        prompt = spec["prompt"]
        for required in ("already accepted my connection request and read my",
                         "a nudge and not a second pitch",
                         "RECIPIENT: Dana, who accepted the connection request.",
                         "A greeting using this person's first name.",
                         "One short question asking where it stands.",
                         "25-45 words"):
            with self.subTest(required=required):
                self.assertIn(required, prompt)
        # The demo link was passed in and must still not reach the writer.
        self.assertNotIn("https://demo/1", prompt)
        for banned in ("Subject:", "My resume is attached again.",
                       "Exact live demo URL", "50–80 words"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, prompt)
        self.assertIn("nothing can be attached to a LinkedIn message", prompt)
        # "just following up" survives only as a banned-pattern rule for the writer.
        self.assertIn('- "just following up", "circling back"', prompt)

    def test_the_writer_is_told_not_to_restate_or_add_anything(self):
        prompt = build_follow_up_prompt("Acme", "ML Engineer", 7,
                                        recipient_name="Dana")["prompt"]
        for rule in ("do not restate what",
                     "do not add a new fact",
                     "do not send any link again",
                     "Anything already said in the connection note",
                     "A new fact, a skill list, a number, an employer or a project"):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)

    def test_the_recorded_profile_is_opened_before_anything_is_written(self):
        """LinkedIn carries a direct message only once the invitation has been
        accepted, and the only way to know is to open the profile the cold DM
        went to."""
        prompt = build_follow_up_prompt("Acme", "ML Engineer", 7, recipient_name="Dana",
                                        recipient_profile_url="https://www.linkedin.com/in/dana/")["prompt"]
        self.assertIn("THEIR PROFILE: https://www.linkedin.com/in/dana/", prompt)
        flat = " ".join(prompt.split())
        self.assertIn("Open it first and confirm the invitation was accepted", flat)
        self.assertIn("pending, was withdrawn or was ignored, write nothing for this job", flat)

    def test_no_recorded_profile_means_nothing_is_written(self):
        prompt = build_follow_up_prompt("Acme", "ML Engineer", 7,
                                        recipient_name="Dana")["prompt"]
        flat = " ".join(prompt.split())
        self.assertIn("THEIR PROFILE: none recorded", flat)
        self.assertIn("you cannot confirm the invitation was accepted, so write nothing", flat)

    def test_a_follow_up_without_a_recipient_keeps_the_placeholder(self):
        prompt = build_follow_up_prompt("Acme", "ML Engineer", 7, profile_text="P")["prompt"]
        self.assertIn("RECIPIENT: none on record", prompt)
        self.assertIn("never guess a name", prompt)
        self.assertNotIn("https://", prompt)

    def test_an_unused_param_from_an_older_queued_row_still_builds(self):
        """Requests queued by an older build carry profile_text, demo_url and
        company_intel, and the builder is called with **params."""
        spec = build_follow_up_prompt("Acme", "ML Engineer", 7, original_platform="LinkedIn",
                                      profile_text="P", demo_url="https://demo/9",
                                      company_intel="Website: https://acme.test",
                                      follow_up_number=2, previous_messages=["Hi Dana, note."],
                                      recipient_name="Dana")
        self.assertIn("Connection note:", spec["prompt"])
        self.assertNotIn("https://demo/9", spec["prompt"])
        self.assertNotIn("acme.test", spec["prompt"])


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
                       "Impressive platform!",
                       # The prompt bans "exciting"; the rule caught only the
                       # two-word "exciting work", so bare praise slipped by.
                       "Your product is exciting.",
                       "Exciting work on the gateway.",
                       "I love what you are building."):
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

    def test_the_save_guidance_names_the_token_it_requires(self):
        """The message told the writer to open a bare "Hi, I recently applied",
        which validate_outreach_draft rejects for missing the token — so an
        agent that tripped this check was sent to the other one."""
        save = MagicMock(return_value=True)
        command = function(ROOT / "modules/pending_messages.py", "cmd_save", {
            "sys": sys, "save_job_message": save,
            "_demo_url_for_job": lambda _: "",
            "get_job_message": lambda *a, **kw: {"content": "saved"}})
        stderr = io.StringIO()
        with patch("sys.stderr", stderr), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(command(SimpleNamespace(
                type="cold_dm", job_id=7, content="Hi [FIRST NAME], about the role.")), 1)
        guidance = stderr.getvalue()
        self.assertIn("[FIRST NAME]", guidance)
        self.assertNotIn('"Hi, I recently applied', guidance)
        save.assert_not_called()

    def test_queue_keeps_valid_note_exactly(self):
        complete = MagicMock(return_value=True)
        queue = function(ROOT / "modules/pending_messages.py", "cmd_fulfil", {
            "sys": sys, "get_message_request": lambda _: {"message_type": "cold-dm", "params": {}},
            "_build_prompt": lambda *_: {"char_limit": 300}, "complete_message_request": complete})
        note = "Hi [FIRST NAME], interested in Acme’s ML role. My Python coursework is relevant; glad to connect."
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(queue(SimpleNamespace(request_id=8, content=note)), 0)
        complete.assert_called_once_with(8, note)


class ConnectionRecipientTests(unittest.TestCase):
    """Where the follow-up's recipient comes from.

    Newer records carry it in `recipient_name`; older ones only have the
    "Sent to <name>: <url>" trailer, and a note recorded by hand before the
    form had a recipient field has neither. The column is preferred and the
    trailer is the fallback, so both keep working.
    """

    def _call(self, rows, name):
        """Run one of the recipient readers with the data layer stubbed.

        The readers now batch: one query covers every application asked about,
        so the stub answers `.in_("entity_id", [...])` and the rows carry the
        entity_id the real table returns.
        """
        from unittest.mock import MagicMock
        db = MagicMock()
        (db.table.return_value.select.return_value.eq.return_value.in_.return_value
         .eq.return_value.order.return_value.execute.return_value) = SimpleNamespace(
             data=[{**row, "entity_id": 131} for row in rows])
        tracker = types.ModuleType("tracker")
        tracker.DM_CHANNEL = "LinkedIn connection"
        tracker._get_client = lambda: db
        module = ROOT / "modules/pending_messages.py"
        # _PROFILE_URL is a module-level constant the AST helper does not carry,
        # so take the pattern from the source rather than restating it here.
        literal = re.search(r"_PROFILE_URL = re\.compile\((r.[^\n]+?.)\)\n",
                            module.read_text()).group(1)
        env = {"sys": sys, "re": re, "_PROFILE_URL": re.compile(eval(literal))}
        # The readers delegate down this chain, and `function` only lifts one
        # definition at a time, so each must be in the shared namespace.
        for helper in ("_record_from_rows", "_detail_from_rows",
                       "_connection_details", "_connection_records",
                       "_connection_record"):
            env[helper] = function(module, helper, env)
        with patch.dict(sys.modules, {"tracker": tracker}):
            return function(module, name, env)(131)

    def record(self, rows):
        """(name, url) — both halves the follow-up needs."""
        return self._call(rows, "_connection_record")

    def recipient(self, rows):
        return self._call(rows, "_connection_recipient")

    def test_the_column_is_preferred(self):
        self.assertEqual(self.recipient([
            {"recipient_name": "Viren Khatri",
             "message_content": "note\n\nSent to Someone Else: https://x"}]),
            "Viren Khatri")

    def test_the_trailer_is_the_fallback_for_older_rows(self):
        self.assertEqual(self.recipient([
            {"recipient_name": None,
             "message_content": "note\n\nSent to Viren Khatri: https://www.linkedin.com/in/viren"}]),
            "Viren Khatri")
        self.assertEqual(self.recipient([
            {"message_content": "note\n\nSent to Viren Khatri: https://x"}]), "Viren Khatri")

    def test_a_hand_recorded_row_with_neither_invents_nobody(self):
        self.assertEqual(self.recipient([
            {"recipient_name": "", "message_content": "Exact text I pasted, no trailer."}]), "")
        self.assertEqual(self.recipient([]), "")

    def test_the_url_column_is_preferred_over_the_trailer(self):
        """The URL is what the sending step opens to check the invitation was
        accepted, so it has to come back alongside the name."""
        self.assertEqual(self.record([
            {"recipient_name": "Viren Khatri",
             "recipient_profile_url": "https://www.linkedin.com/in/viren/",
             "message_content": "note\n\nSent to Someone Else: https://www.linkedin.com/in/other"}]),
            ("Viren Khatri", "https://www.linkedin.com/in/viren/"))

    def test_the_url_falls_back_to_the_trailer_for_older_rows(self):
        self.assertEqual(self.record([
            {"recipient_name": None, "recipient_profile_url": "",
             "message_content": "note\n\nSent to Viren Khatri: https://www.linkedin.com/in/viren"}]),
            ("Viren Khatri", "https://www.linkedin.com/in/viren"))

    def test_a_row_with_no_url_anywhere_reports_none(self):
        """No URL means the invitation cannot be confirmed, so the prompt has
        to be told that rather than handed an empty-looking link."""
        self.assertEqual(self.record([
            {"recipient_name": "Viren Khatri", "recipient_profile_url": "",
             "message_content": "Exact text I pasted, no trailer."}]),
            ("Viren Khatri", ""))
        self.assertEqual(self.record([]), ("", ""))

    def test_a_url_half_alone_is_still_a_usable_record(self):
        self.assertEqual(self.record([
            {"recipient_name": "", "recipient_profile_url": "",
             "message_content": "note https://www.linkedin.com/in/viren done"}]),
            ("", "https://www.linkedin.com/in/viren"))

    def test_one_query_keeps_each_application_s_own_recipient(self):
        """The Dashboard resolves a recipient for every due follow-up at once,
        so the batched read has to keep them apart — handing one job's contact
        to another would address the message to the wrong person."""
        from unittest.mock import MagicMock
        db = MagicMock()
        (db.table.return_value.select.return_value.eq.return_value.in_.return_value
         .eq.return_value.order.return_value.execute.return_value) = SimpleNamespace(data=[
             {"entity_id": 131, "recipient_name": "Viren Khatri",
              "recipient_profile_url": "https://www.linkedin.com/in/viren/",
              "message_content": "note"},
             {"entity_id": 140, "recipient_name": "Dana Rao",
              "recipient_profile_url": "", "message_content":
              "note\n\nSent to Ignored Trailer: https://www.linkedin.com/in/dana"},
         ])
        tracker = types.ModuleType("tracker")
        tracker.DM_CHANNEL = "LinkedIn connection"
        tracker._get_client = lambda: db
        module = ROOT / "modules/pending_messages.py"
        literal = re.search(r"_PROFILE_URL = re\.compile\((r.[^\n]+?.)\)\n",
                            module.read_text()).group(1)
        env = {"sys": sys, "re": re, "_PROFILE_URL": re.compile(eval(literal))}
        for helper in ("_record_from_rows", "_detail_from_rows", "_connection_details"):
            env[helper] = function(module, helper, env)
        with patch.dict(sys.modules, {"tracker": tracker}):
            got = function(module, "_connection_records", env)([131, 140, 999])
        self.assertEqual(got, {
            131: ("Viren Khatri", "https://www.linkedin.com/in/viren/"),
            140: ("Dana Rao", "https://www.linkedin.com/in/dana"),
        })
        # One query for all three, and an application with nothing recorded is
        # simply absent rather than borrowing a neighbour's contact.
        db.table.assert_called_once_with("follow_up_history")
        self.assertEqual(
            db.table.return_value.select.return_value.eq.return_value.in_.call_args.args,
            ("entity_id", [131, 140, 999]))

    def test_the_latest_record_wins(self):
        self.assertEqual(self.recipient([
            {"recipient_name": "First Person", "message_content": ""},
            {"recipient_name": "Second Person", "message_content": ""}]), "Second Person")

    def detail(self, rows):
        """The full record: who, when, and what became of it."""
        return self._call(rows, "_connection_details")

    def test_the_outcome_and_date_come_from_the_newest_row(self):
        """The Dashboard card says what became of the connection. The outcome
        has to track the newest attempt, even when that row is the one missing
        a name — otherwise a resent invitation reports the old result."""
        got = self._detail_from_rows([
            {"recipient_name": "Viren Khatri", "message_content": "",
             "follow_up_outcome": "no_response", "sent_at": "2026-09-01T00:00:00+00:00"},
            {"recipient_name": "", "recipient_profile_url": "", "message_content": "",
             "follow_up_outcome": "responded", "sent_at": "2026-10-06T00:00:00+00:00"},
        ])
        # Name still falls back to the newest row that carries one...
        self.assertEqual(got["name"], "Viren Khatri")
        # ...while the outcome is the current one, not the superseded one.
        self.assertEqual(got["outcome"], "responded")
        self.assertEqual(got["sent_at"], "2026-10-06T00:00:00+00:00")

    def test_a_missing_outcome_reads_as_none_not_empty_string(self):
        """An older row with no outcome stored must not render as a blank
        badge on the card."""
        got = self._detail_from_rows([
            {"recipient_name": "Dana Rao", "message_content": "",
             "follow_up_outcome": "", "sent_at": None}])
        self.assertIsNone(got["outcome"])
        self.assertIsNone(got["sent_at"])

    def test_records_is_a_view_over_details_so_it_costs_one_query(self):
        """`_connection_records` kept its (name, url) shape for the draft
        builder, but both readers must share the single batched query — the
        Dashboard is the caller and the database is a continent away."""
        import inspect
        src = (ROOT / "modules/pending_messages.py").read_text()
        body = src.split("def _connection_records(", 1)[1].split("def _connection_details(", 1)[0]
        self.assertIn("_connection_details(application_ids)", body)
        # Only the details reader touches the table.
        self.assertNotIn('db.table("follow_up_history")', body)
        details = src.split("def _connection_details(", 1)[1].split("\ndef ", 1)[0]
        self.assertEqual(details.count('db.table("follow_up_history")'), 1)
        self.assertIn("follow_up_outcome,sent_at", details.replace('"\n                            "', ""))

    def _detail_from_rows(self, rows):
        import re as _re
        module = ROOT / "modules/pending_messages.py"
        literal = _re.search(r"_PROFILE_URL = re\.compile\((r.[^\n]+?.)\)\n",
                             module.read_text()).group(1)
        env = {"sys": sys, "re": _re, "_PROFILE_URL": _re.compile(eval(literal))}
        env["_record_from_rows"] = function(module, "_record_from_rows", env)
        return function(module, "_detail_from_rows", env)(rows)


class PriorRoundContextTests(unittest.TestCase):
    """What the follow-up writer is told was already sent.

    Recording a Cold DM appends "Sent to <name>: <profile url>" to the stored
    message so the recipient can be read back. Two things were wrong with
    feeding that straight into the next round: the writer was told not to
    reuse the wording of a trailer that was never sent, and round 1 was
    labelled "Follow-up #1" when it was the connection invitation.
    """

    NOTE = "Hi Viren, I applied for the GenAI role. Demo: https://d/1. Glad to connect."
    RECORDED = f"{NOTE}\n\nSent to Viren Khatri: https://www.linkedin.com/in/viren"

    def setUp(self):
        self.strip = function(ROOT / "modules/pending_messages.py",
                              "_sent_message_text", {"_SENT_TO_TRAILER": _TRAILER})

    def test_the_recording_trailer_is_not_part_of_the_message(self):
        self.assertEqual(self.strip(self.RECORDED), self.NOTE)

    def test_a_message_recorded_by_hand_is_left_alone(self):
        """The UI stores exactly what was pasted, with no trailer to strip."""
        for untouched in (self.NOTE,
                          "I was sent to the team by a friend.",
                          "Line one\n\nSent to Viren: https://a\nand a later real line"):
            with self.subTest(untouched=untouched[:30]):
                self.assertEqual(self.strip(untouched), untouched)

    def test_nothing_recorded_yields_nothing(self):
        self.assertEqual(self.strip(""), "")
        self.assertEqual(self.strip(None), "")

    def test_round_one_is_named_the_connection_note_not_a_follow_up(self):
        prompt = build_follow_up_prompt(
            "Steps AI", "GenAI Engineer", 11, follow_up_number=2,
            previous_messages=[self.strip(self.RECORDED)],
            profile_text="Built a RAG platform", recipient_name="Viren")["prompt"]
        self.assertIn(f'- Connection note: "{self.NOTE}"', prompt)
        # The invitation is round 1, so no round of that name was ever sent.
        self.assertNotIn("Follow-up #1:", prompt)
        # And the bookkeeping must not reach the writer at all.
        self.assertNotIn("Sent to Viren Khatri", prompt)
        self.assertNotIn("linkedin.com/in/viren", prompt)

    def test_a_later_round_keeps_its_own_number(self):
        prompt = build_follow_up_prompt(
            "Steps AI", "GenAI Engineer", 19, follow_up_number=3,
            previous_messages=[self.NOTE, "Following up on the GenAI role."],
            profile_text="Built a RAG platform", recipient_name="Viren")["prompt"]
        self.assertIn('- Connection note: "', prompt)
        self.assertIn('- Follow-up #2: "Following up on the GenAI role."', prompt)
        self.assertNotIn("Follow-up #1:", prompt)
        self.assertNotIn("Follow-up #3:", prompt)


class LinkedInShapeTests(unittest.TestCase):
    """The shape rules have to cover the follow-up, not just the Cold DM.

    They were gated behind `kind in {"cold_dm", "cold-dm"}`, so fifteen
    follow-up drafts written before email was removed reached status "ready"
    carrying a To: line, a Subject:, and "My resume is attached again" — a
    promise a LinkedIn message cannot keep — and five printed a real address.
    Validation returned None for every one of them.
    """

    EMAIL_SHAPED = ("To: hr@caliberfocus.com\n"
                    "Subject: Re: Junior AI Engineer\n\n"
                    "Hello,\n\nFollowing up on my application, submitted 15 days ago. "
                    "My resume is attached again. Could you share where the role stands?")
    GOOD = ("Hi Viren,\n\nFollowing up on my application for the GenAI Engineer role, "
            "sent 11 days ago. In case it's useful, the short demo I built for this "
            "role is here: https://uav-6qe7.vercel.app/api/demo/1.\n\n"
            "Any update on where the role stands?\n\nSubidh Khanal")

    def test_an_email_shaped_follow_up_is_rejected(self):
        self.assertIsNotNone(validate_outreach_draft("follow-up", self.EMAIL_SHAPED))

    def test_a_real_linkedin_follow_up_passes(self):
        self.assertIsNone(validate_outreach_draft("follow-up", self.GOOD))

    def test_each_letter_artefact_is_caught_on_its_own(self):
        for artefact, draft in (
            ("To: line", "To: hr@acme.com\n\nHi Viren, following up."),
            ("subject", "Subject: Re: the role\n\nHi Viren, following up."),
            ("attachment claim", "Hi Viren, following up. My resume is attached again."),
            ("bare address", "Hi Viren, following up — reach me at hr@acme.com."),
        ):
            with self.subTest(artefact=artefact):
                self.assertIsNotNone(validate_outreach_draft("follow-up", draft))

    def test_the_underscore_spelling_is_covered_too(self):
        """create_message_request uses "follow-up"; stored rows elsewhere use
        "follow_up". A rule that only matched one spelling would be a gap."""
        for kind in ("follow-up", "follow_up"):
            with self.subTest(kind=kind):
                self.assertIsNotNone(validate_outreach_draft(kind, self.EMAIL_SHAPED))

    def test_the_cold_dm_keeps_its_own_extra_rules(self):
        """Length and the [FIRST NAME] token are the Cold DM's alone — a
        follow-up greets by real name and is longer than a connection note."""
        self.assertIsNone(validate_outreach_draft("follow-up", self.GOOD))
        self.assertIn("[FIRST NAME]", validate_outreach_draft("cold_dm", "Hi Viren, glad to connect."))
        self.assertIn("300 characters", validate_outreach_draft("cold_dm", "Hi [FIRST NAME], " + "x" * 320))
