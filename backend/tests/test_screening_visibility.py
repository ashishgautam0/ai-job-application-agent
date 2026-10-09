import ast
import contextlib
import io
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

SOURCE = pathlib.Path("backend/modules/pending_messages.py")
TRACKER_SOURCE = pathlib.Path("backend/modules/tracker.py")

def load_function(name, env):
    node = next(n for n in ast.parse(SOURCE.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<actual-cli>", "exec"), env)
    return env[name]

class ScreeningTests(unittest.TestCase):
    def screen(self, decision, success=True):
        fake = types.ModuleType("tracker")
        fake.save_job_message = MagicMock(return_value=success)
        fake.mark_scraped_job = MagicMock()
        fn = load_function("cmd_screen", {"sys": sys})
        with patch.dict(sys.modules, {"tracker": fake}), contextlib.redirect_stdout(io.StringIO()):
            result = fn(types.SimpleNamespace(job_id=7, decision=decision, reason="evidence"))
        return result, fake

    def test_review_restores_visibility(self):
        result, fake = self.screen("review")
        self.assertEqual(result, 0)
        fake.save_job_message.assert_called_once_with(7, "REVIEW: evidence", message_type="screen")
        fake.mark_scraped_job.assert_called_once_with(7, "keep")

    def test_pass_and_fail_visibility(self):
        for decision, action in [("pass", "keep"), ("fail", "dismissed")]:
            _, fake = self.screen(decision)
            fake.mark_scraped_job.assert_called_once_with(7, action)

    def test_failed_save_does_not_change_visibility(self):
        result, fake = self.screen("review", False)
        self.assertEqual(result, 1)
        fake.mark_scraped_job.assert_not_called()

    def test_legacy_review_save_restores_visibility(self):
        fake = types.ModuleType("tracker")
        fake.mark_scraped_job = MagicMock()
        env = {"sys": sys, "save_job_message": MagicMock(return_value=True),
               "get_job_message": MagicMock(return_value={"content": "REVIEW: uncertain"})}
        fn = load_function("cmd_save", env)
        with patch.dict(sys.modules, {"tracker": fake}), contextlib.redirect_stdout(io.StringIO()):
            result = fn(types.SimpleNamespace(job_id=7, type="screen", content="REVIEW: uncertain"))
        self.assertEqual(result, 0)
        fake.mark_scraped_job.assert_called_once_with(7, "keep")

    def test_cli_accepts_review(self):
        # Check the real argparse parser without importing production dependencies.
        import argparse
        env = {"argparse": argparse, "__doc__": "", "sys": sys,
               "DEFAULT_MESSAGE_TYPE": "cold_dm",
               "JOB_MESSAGE_TYPES": ("screen", "cold_dm", "resume_points", "demo_html")}
        tree = ast.parse(SOURCE.read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
                env[node.name] = MagicMock(return_value=0)
        fn = load_function("main", env)
        with patch.object(sys, "argv", ["pending_messages.py", "screen", "--job-id", "7",
                                       "--decision", "review", "--reason", "unclear"]):
            with self.assertRaises(SystemExit) as result:
                fn()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(env["cmd_screen"].call_args.args[0].decision, "review")

    def test_cli_rejects_retired_evaluation_type(self):
        import argparse
        env = {"argparse": argparse, "__doc__": "", "sys": sys,
               "DEFAULT_MESSAGE_TYPE": "cold_dm",
               "JOB_MESSAGE_TYPES": ("screen", "cold_dm", "resume_points", "demo_html")}
        tree = ast.parse(SOURCE.read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
                env[node.name] = MagicMock(return_value=0)
        fn = load_function("main", env)
        with patch.object(sys, "argv", ["pending_messages.py", "save", "--job-id", "7",
                                       "--type", "evaluation", "--content", "retired"]):
            with self.assertRaises(SystemExit) as result:
                fn()
        self.assertEqual(result.exception.code, 2)

    def test_tracker_rejects_retired_evaluation_without_database_write(self):
        node = next(n for n in ast.parse(TRACKER_SOURCE.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "save_job_message")
        database = MagicMock()
        env = {
            "DEFAULT_MESSAGE_TYPE": "cold_dm",
            "JOB_MESSAGE_TYPES": ("screen", "cold_dm", "resume_points", "demo_html"),
            "_get_client": database,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<tracker>", "exec"), env)
        self.assertFalse(env["save_job_message"](7, "retired", message_type="evaluation"))
        database.assert_not_called()

    def test_fit_evaluation_removed_from_job_ui_and_cloud_agent(self):
        page = pathlib.Path("frontend/src/app/(app)/jobs/[id]/page.tsx").read_text()
        agent = pathlib.Path(".claude/agents/job-research.md").read_text()
        self.assertNotIn("A–H Fit Evaluation", page)
        self.assertNotIn("--type evaluation", agent)
        self.assertIn("company research agent", agent)

if __name__ == "__main__":
    unittest.main()
