import ast
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
from intake_policy import excluded_employer, experience_exclusion, filter_jobs

class IntakePolicyTests(unittest.TestCase):
    def test_no_hardcoded_exclusions(self):
        for company in ["TCS", "Infosys", "Amazon", "Google", "Microsoft", "HCLTech"]:
            with self.subTest(company=company):
                self.assertFalse(excluded_employer(company))

    def test_unknown_and_similar_companies_are_kept(self):
        for company in ["Global AI Startup", "VisaBuddy", "Amazonia AI", "TCS Labs Startup", "", None]:
            with self.subTest(company=company):
                self.assertFalse(excluded_employer(company))

    def test_settings_company_names_match_exact_employer_and_legal_suffixes(self):
        names = ["Blue Harbor AI", "ReveonAI Lifesciences Solutions"]
        for company in ["Blue Harbor AI Pvt. Ltd.", "BLUE-HARBOR AI", "ReveonAI Lifesciences Solutions LLP"]:
            with self.subTest(company=company):
                self.assertTrue(excluded_employer(company, names))
        for company in ["Blue Harbor AI Labs", "Blue Harbor Analytics", "ReveonAI Partner", ""]:
            with self.subTest(company=company):
                self.assertFalse(excluded_employer(company, names))
        kept, removed = filter_jobs([
            {"company": "Blue Harbor AI Ltd", "description": ""},
            {"company": "Small Startup", "description": "Blue Harbor AI Python experience"},
        ], names)
        self.assertEqual([job["company"] for job in kept], ["Small Startup"])
        self.assertEqual(removed[0][1], "excluded by Settings company list")

    def test_required_experience(self):
        for text in ["3+ years of experience required", "2.5 years experience",
                     "Experience: 2-4 years", "30 months of relevant experience",
                     "More than two years of experience", ">2 years experience",
                     "Minimum three years of professional experience",
                     "Required qualifications:\n3 years experience",
                     "3 years experience required, Python preferred"]:
            with self.subTest(text=text):
                self.assertIsNotNone(experience_exclusion(text))

    def test_any_figure_past_one_year_is_excluded(self):
        """Freshers and 0-1 years only, so the highest figure a posting prints
        is what decides it -- floor, ceiling or top of a range alike.

        Each of these used to survive intake. A band was judged by its bottom,
        so "0-2 years" and "1-2 years" passed on a floor a fresher fits; an
        upper bound was treated as no demand at all, so "up to 2 years"
        passed; and "1+ years" scored its floor of exactly 12 months and tied
        rather than exceeded.
        """
        for text in ["0-2 years of experience", "1-2 years experience",
                     "1–2 years experience", "0-3 years experience",
                     "Experience: 1 to 3 years",
                     "Up to 2 years experience", "No more than 2 years experience",
                     "less than 2 years experience",
                     "1+ years experience", "Experience: 1+ years",
                     "minimum 1 year of experience", "at least 1 year experience",
                     "more than 1 year of experience",
                     "2+ years experience", "2 years experience",
                     "24 months experience", "18 months experience"]:
            with self.subTest(text=text):
                self.assertIsNotNone(experience_exclusion(text))

    def test_a_heading_word_does_not_swallow_a_requirement_line(self):
        """A short line opening with a heading word was read as a section
        heading and skipped whole, so "mandatory 5 years experience" on its own
        line asked for five years and passed intake. A heading names no span of
        time, so a line carrying one is never a heading.

        The headings themselves must still work: they set whether the figures
        under them are required or merely preferred.
        """
        for text in ["mandatory 5 years experience", "required 2 years of experience",
                     "minimum 3 years experience", "minimum 1 year of experience"]:
            with self.subTest(text=text):
                self.assertIsNotNone(experience_exclusion(text))
        self.assertIsNone(experience_exclusion("Preferred qualifications:\n3 years experience"))
        self.assertIsNotNone(experience_exclusion("Required qualifications:\n3 years experience"))

    def test_eligible_or_unknown_experience(self):
        # Silence is not a requirement, and an explicit 0-1 band stays inside
        # the ceiling. Both remain eligible.
        for text in ["0-1 years of experience", "0-1 year experience",
                     "Experience: 0-1 years", "1 year of experience",
                     "up to 1 year of experience", "0 years experience",
                     "12 months experience", "6 months experience",
                     "Fresher", "freshers welcome",
                     "3 years experience preferred",
                     "Company founded 10 years ago. Python required.",
                     "Preferred qualifications:\n3 years experience",
                     "Required qualifications:", "Minimum qualifications",
                     "", None]:
            with self.subTest(text=text):
                self.assertIsNone(experience_exclusion(text))

    def test_required_section_resets_preferred(self):
        self.assertIsNotNone(experience_exclusion(
            "Preferred qualifications:\n3 years experience\nRequired qualifications:\n3 years experience"))

    def test_filter_uses_employer_not_technologies(self):
        jobs = [{"company": "Small Startup", "description": "Experience with Amazon Web Services"},
                {"company": "TCS", "description": ""},
                {"company": "Small Startup", "description": "3 years experience"}]
        kept, removed = filter_jobs(jobs)
        self.assertEqual(kept, jobs[:2])
        self.assertEqual(len(removed), 1)

    def test_the_job_scrapers_are_gone_and_nothing_imports_them(self):
        """Job discovery moved to the Claude Desktop agent entirely.

        The LinkedIn/Indeed scrapers and the hourly pipeline that drove them
        were removed; the exclusion list they used now reaches the agent
        through the desktop prompt instead. Nothing may import them back.
        """
        for gone in ("scraper.py", "hourly.py"):
            with self.subTest(gone=gone):
                self.assertFalse((ROOT / "modules" / gone).exists())
        for source in (ROOT / "modules").glob("*.py"):
            text = source.read_text()
            for banned in ("from scraper import", "import scraper",
                           "from hourly import", "import hourly", "jobspy"):
                with self.subTest(source=source.name, banned=banned):
                    self.assertNotIn(banned, text)
        requirements = (ROOT / "requirements.txt").read_text()
        self.assertNotIn("jobspy", requirements)

    def test_company_exclusions_still_reach_the_desktop_agent(self):
        """Removing the scraper must not orphan the Settings exclusion list."""
        prompt = (ROOT.parent / "prompts" / "job-agent-desktop.md").read_text()
        self.assertIn("{{excluded_companies}}", prompt)

    def test_persistence_guard_before_database_access(self):
        source = (ROOT / "modules" / "tracker.py").read_text()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == "save_scraped_job")
        def fail():
            raise AssertionError("Excluded job reached database")
        env = {"_get_client": fail}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<persistence>", "exec"), env)
        env["save_scraped_job"]("AI Engineer", "Startup", "", "", "https://example.test",
                                description="3 years experience")
        env["save_scraped_job"]("AI Engineer", "Blue Harbor AI Ltd", "", "", "https://example.test",
                                company_exclusions=["Blue Harbor AI"])
        with patch("profile.get_company_exclusions", return_value=["Blue Harbor AI"]):
            env["save_scraped_job"]("AI Engineer", "Blue Harbor AI Ltd", "", "", "https://example.test")

if __name__ == "__main__":
    unittest.main()
