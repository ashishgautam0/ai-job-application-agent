import unittest
from unittest.mock import MagicMock

from test_settings_profile import ROOT, function


class OutreachSettingsTests(unittest.TestCase):
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

    def test_the_manual_form_records_the_recipient_as_data(self):
        """The Tracker form had no recipient field, so a hand-recorded note left
        nothing for the follow-up to address: 9 of 62 rows arrived that way."""
        from unittest.mock import MagicMock
        from pydantic import ValidationError
        from app.models.schemas import LogFollowUpRequest
        log, advance = MagicMock(return_value=1), MagicMock()
        endpoint = function(ROOT / "app/routers/follow_ups.py", "log_follow_up_sent", {
            "LogFollowUpRequest": LogFollowUpRequest, "log_follow_up": log, "update_status": advance})
        endpoint(LogFollowUpRequest(
            entity_type="application", entity_id=42, message_content="Exact sent note",
            channel="LinkedIn connection", recipient_name="Viren Khatri",
            recipient_profile_url="https://www.linkedin.com/in/viren"))
        self.assertEqual(log.call_args.kwargs["recipient_name"], "Viren Khatri")
        self.assertEqual(log.call_args.kwargs["recipient_profile_url"],
                         "https://www.linkedin.com/in/viren")
        # Optional, because WhatsApp and Other have no profile behind them.
        bare = LogFollowUpRequest(entity_type="application", entity_id=42)
        self.assertEqual((bare.recipient_name, bare.recipient_profile_url), ("", ""))
        # But a URL that is not a LinkedIn profile is refused, not stored.
        for bad in ("https://example.com/in/x", "notaurl", "http://linkedin.com/in/x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    LogFollowUpRequest(entity_type="application", entity_id=42,
                                       recipient_profile_url=bad)

if __name__ == "__main__":
    unittest.main()
