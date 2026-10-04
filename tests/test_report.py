import json
import tempfile
import unittest
from pathlib import Path

from job_search.core import Job
from job_search.report import write_reports


class ReportTests(unittest.TestCase):
    def test_public_job_urls_strip_credentials_and_keep_sanitized_outcomes(self):
        sentinel = "SENTINEL_SECRET_VALUE"
        job = Job(
            "1", "Aggregator", "Example", "Operations Analyst", "India", "Remote",
            "Operations role", f"https://example.test/jobs/1?token={sentinel}&ref=public",
            original_source_url=f"https://example.test/jobs/1?api_key={sentinel}",
            canonical_employer_url=f"https://careers.example.test/1?auth={sentinel}",
            verification_reason="http_blocked", canonical_resolution_status="canonical_destination_unresolved",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_reports([], [job], [], output)
            combined = (output / "latest.md").read_text(encoding="utf-8") + (output / "jobs.json").read_text(encoding="utf-8") + (output / "jobs.csv").read_text(encoding="utf-8-sig")
            payload = json.loads((output / "jobs.json").read_text(encoding="utf-8"))
        self.assertNotIn(sentinel, combined)
        self.assertEqual(payload["review_queue"][0]["verification_reason"], "http_blocked")

    def test_public_reports_do_not_repeat_unsanitized_diagnostic_text(self):
        sentinel = "SENTINEL_SECRET_VALUE"
        diagnostics = [{"source_id":"example","status":"http_failure","attempted":True,"jobs_returned":0,"reason":f"https://example.test/?token={sentinel}"}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_reports([], [], [f"request failed: {sentinel}"], output, source_diagnostics=diagnostics)
            combined = (output / "latest.md").read_text(encoding="utf-8") + (output / "jobs.json").read_text(encoding="utf-8")
        self.assertNotIn(sentinel, combined)

    def test_unknown_verification_reason_is_replaced_with_sanitized_class(self):
        sentinel = "SENTINEL_PRIVATE_EXCEPTION_TEXT"
        job = Job("1", "test", "Example", "Analyst", "India", "", "Role", "https://example.test", verification_reason=sentinel)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_reports([], [job], [], output)
            payload = json.loads((output / "jobs.json").read_text(encoding="utf-8"))
            combined = (output / "latest.md").read_text(encoding="utf-8") + json.dumps(payload)
        self.assertNotIn(sentinel, combined)
        self.assertEqual(payload["review_queue"][0]["verification_reason"], "other_sanitized_failure")
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

    def test_source_funnel_and_freshness_diagnostics_are_compact_and_structured(self):
        active = Job("a", "lever", "Example", "Operations Analyst", "India", "Remote", "Role", "https://example.test/a", active_status="active", verification_reason="verified_active", listing_age_days=12)
        unresolved = Job("u", "Adzuna", "Example", "Data Quality Analyst", "India", "", "Role", "https://example.test/u", active_status="unverified", verification_reason="http_blocked", source_type="aggregator", listing_age_days=400)
        diagnostic = {
            "source_id":"adzuna/india", "source_type":"aggregator", "status":"successful", "attempted":True,
            "jobs_returned":10, "jobs_retained":1, "passed_basic_eligibility":4, "rejected_by_eligibility":6,
            "hard_excluded":1, "duplicate_non_canonical":2, "submitted_for_verification":2,
            "verified_active":0, "verified_closed":0, "verification_unresolved":1,
            "strong_shortlist":0, "review_queue":1, "suppressed":9, "final_human_visible":1, "reason":"",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_reports([active], [unresolved], [], output, source_diagnostics=[diagnostic])
            payload = json.loads((output / "jobs.json").read_text(encoding="utf-8"))
            markdown = (output / "latest.md").read_text(encoding="utf-8")
        self.assertEqual(payload["source_diagnostics"][0]["final_human_visible"], 1)
        self.assertEqual(payload["verification_health"]["freshness_by_verification"]["stale_unverified_aggregator"][">365"], 1)
        self.assertIn("eligible 4", markdown)


if __name__ == "__main__":
    unittest.main()
