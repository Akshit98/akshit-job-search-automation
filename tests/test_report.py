import json
import tempfile
import unittest
from pathlib import Path

from job_search.core import Job
from job_search.report import write_reports


class ReportTests(unittest.TestCase):
    def test_assessed_fit_is_shown_with_raw_score_cap_and_categories(self):
        assessed = Job(
            "fit-1", "test", "Example", "Data Operations Analyst", "India", "Remote",
            "Detailed role", "https://example.test/fit", active_status="active",
            actual_hiring_fit=49, actual_hiring_fit_status="assessed",
            actual_hiring_fit_raw=74, actual_hiring_fit_cap=49,
            actual_hiring_fit_cap_reasons=["missing_required_domain_and_core_system"],
            fit_category_scores={
                "direct_responsibilities": 30, "tools_domain": 10,
                "years_seniority_leadership": 15, "transferable_evidence": 11,
                "education_certifications": 5, "practical_requirements": 3,
            }, ats_similarity=42,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_reports([assessed], [], [], output)
            markdown = (output / "latest.md").read_text(encoding="utf-8")
            payload = json.loads((output / "jobs.json").read_text(encoding="utf-8"))
        self.assertIn("Actual Hiring Fit: 49/100 (raw 74; capped at 49)", markdown)
        self.assertIn("missing_required_domain_and_core_system", markdown)
        self.assertIn("ATS/Resume Similarity: 42/100", markdown)
        self.assertEqual(payload["jobs"][0]["actual_hiring_fit_raw"], 74)

    def test_verified_and_unverified_jobs_are_separated(self):
        active = Job(
            "active-1", "test", "Example", "Operations Analyst", "India", "Remote",
            "Operations role", "https://example.test/active", active_status="active",
            screening_score=72, screening_reasons=["primary role"],
        )
        unverified = Job(
            "unknown-1", "test", "Example", "Data Operations Associate", "India", "Remote",
            "Data role", "https://example.test/unknown", active_status="unverified",
            screening_score=68, screening_reasons=["primary role"],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            active.screening_queue = "strong_shortlist"
            unverified.screening_queue = "review_queue"
            write_reports([active], [unverified], [], output, suppressed_count=2)
            markdown = (output / "latest.md").read_text(encoding="utf-8")
            payload = json.loads((output / "jobs.json").read_text(encoding="utf-8"))
        self.assertIn("Strong shortlist", markdown)
        self.assertIn("Review queue", markdown)
        self.assertEqual(payload["suppressed_count"], 2)
        self.assertIn("Actual Hiring Fit: Not assessed", markdown)
        self.assertEqual(payload["jobs"][0]["actual_hiring_fit"], None)
        self.assertEqual(payload["needs_verification"][0]["id"], "unknown-1")


if __name__ == "__main__":
    unittest.main()
