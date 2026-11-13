import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
from intake_policy import experience_exclusion, filter_jobs

class IntakePolicyTests(unittest.TestCase):
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

    def test_no_employer_is_filtered_out_any_more(self):
        """Employers used to be excluded by a Settings list; that list is gone,
        so the only thing that can drop a posting is its experience band.

        The big Indian employers are the test: each was on the user's list at
        some point, and each is eligible now."""
        jobs = [{"company": "Small Startup", "description": "Experience with Amazon Web Services"},
                {"company": "TCS", "description": ""},
                {"company": "Infosys", "description": "0-1 years"},
                {"company": "Accenture", "description": ""},
                {"company": "Small Startup", "description": "3 years experience"}]
        kept, removed = filter_jobs(jobs)
        self.assertEqual(kept, jobs[:4])
        self.assertEqual(len(removed), 1)
        self.assertIn("experience band", removed[0][1])

    def test_the_job_scrapers_are_gone_and_nothing_imports_them(self):
        """Job discovery moved to the Claude Desktop agent entirely.

        The LinkedIn/Indeed scrapers and the hourly pipeline that drove them
        were removed. Nothing may import them back.
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

if __name__ == "__main__":
    unittest.main()
