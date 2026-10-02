import json
import tempfile
import unittest
from pathlib import Path

from job_search.core import Job
from job_search.report import write_reports


class ReportTests(unittest.TestCase):
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
