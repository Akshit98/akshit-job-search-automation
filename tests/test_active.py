import json
import unittest
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

from job_search.active import (
    MAX_JOB_PAGE_BYTES,
    check_job_active,
    apply_freshness,
    classify_active_response,
    is_stale,
    published_datetime,
    redirected_to_listing_index,
    retain_active_jobs,
    enrich_from_job_page,
)
from job_search.core import Job
from job_search.http_safe import RequestBudgetExhausted, ResponseTooLarge, SafeResponse, UnsafeDestination


VERIFICATION_FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "verification_pages.json").read_text(encoding="utf-8"))


class ActiveStatusTests(unittest.TestCase):
    @staticmethod
    def response(body: str, url: str, code: int = 200):
        return SafeResponse(code, body, url, urlsplit(url).hostname or "", 0)

    def test_full_employer_jd_replaces_thin_aggregator_excerpt(self):
        html = (Path(__file__).parent / "fixtures" / "job_page.html").read_text(encoding="utf-8")
        job = Job("1", "Adzuna", "Example", "Analyst", "India", "", "Thin excerpt", "https://example.test", source_type="aggregator", description_provenance="aggregator_excerpt")
        self.assertTrue(enrich_from_job_page(job, html, "https://employer.test/job"))
        self.assertIn("CRM data maintenance", job.description)
        self.assertEqual(job.description_provenance, "employer_job_page")
        self.assertEqual(job.final_url, "https://employer.test/job")

    def test_aggregator_jsonld_does_not_replace_employer_with_provider(self):
        html = '<script type="application/ld+json">{"@type":"JobPosting","description":"A much fuller responsibilities and requirements section for this operations role.","hiringOrganization":{"name":"Jobgether"}}</script>'
        job = Job("1", "Jobgether", "Undisclosed employer", "Operations Analyst", "India", "Remote", "Thin excerpt", "https://example.test", source_type="aggregator", canonical_employer="Undisclosed employer")
        self.assertTrue(enrich_from_job_page(job, html, "https://example.test"))
        self.assertEqual(job.company, "Undisclosed employer")
        self.assertEqual(job.canonical_employer, "Undisclosed employer")

    @patch("job_search.active.safe_http_get")
    def test_jsonld_after_300kb_is_still_read(self, mock_fetch):
        prefix = "x" * 350_000
        html = prefix + '<script type="application/ld+json">{"@type":"JobPosting","description":"Responsibilities: maintain CRM records. Requirements: Salesforce experience and operational reporting."}</script><a>Apply now</a>'
        mock_fetch.return_value = self.response(html, "https://example.test/job")
        job = Job("late", "test", "Example", "CRM Operations", "India", "", "Thin text", "https://example.test/job")
        self.assertEqual(check_job_active(job), "active")
        self.assertEqual(job.description_retrieval_status, "enriched")
        self.assertIn("Salesforce experience", job.description)
        self.assertEqual(mock_fetch.call_args.kwargs["maximum_bytes"], MAX_JOB_PAGE_BYTES)

    @patch("job_search.active.safe_http_get", side_effect=ResponseTooLarge("large"))
    def test_oversized_page_is_explicitly_unverified(self, _mock_fetch):
        job = Job("large", "test", "Example", "Operations", "India", "", "Original", "https://example.test/job")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.description_retrieval_status, "page_too_large")
        self.assertEqual(job.description, "Original")

    @patch("job_search.active.safe_http_get")
    def test_aggregator_resolves_and_verifies_canonical_employer_page(self, mock_fetch):
        mock_fetch.side_effect = [
            self.response(VERIFICATION_FIXTURES["aggregator_with_employer_link"], "https://aggregator.test/details/1"),
            self.response(VERIFICATION_FIXTURES["canonical_employer"], "https://careers.example.test/jobs/123"),
        ]
        job = Job("1", "Aggregator", "Example", "Operations Analyst", "India", "", "Thin", "https://aggregator.test/details/1", source_type="aggregator")
        self.assertEqual(check_job_active(job), "active")
        self.assertEqual(job.verification_reason, "verified_active")
        self.assertEqual(job.canonical_resolution_status, "resolved_employer_page")
        self.assertEqual(job.canonical_employer_url, "https://careers.example.test/jobs/123")
        self.assertEqual(job.canonical_location, "Pune, India")

    @patch("job_search.active.safe_http_get")
    def test_aggregator_only_page_is_not_verified_active(self, mock_fetch):
        mock_fetch.return_value = self.response(VERIFICATION_FIXTURES["aggregator_only"], "https://aggregator.test/details/1")
        job = Job("1", "Aggregator", "Example", "Analyst", "India", "", "Thin", "https://aggregator.test/details/1", source_type="aggregator")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "aggregator_page_only")
        self.assertEqual(job.canonical_resolution_status, "canonical_employer_url_missing")

    @patch("job_search.active.safe_http_get", return_value=SafeResponse(403, "blocked-token", "https://example.test", "example.test", 0))
    def test_http_blocked_is_sanitized(self, _mock_fetch):
        job = Job("1", "Aggregator", "Example", "Analyst", "India", "", "Thin", "https://example.test", source_type="aggregator")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "http_blocked")
        self.assertNotIn("blocked-token", job.verification_reason)

    @patch("job_search.active.safe_http_get", return_value=SafeResponse(404, "secret", "https://example.test", "example.test", 0))
    def test_404_destination_is_verified_closed_without_raw_error(self, _mock_fetch):
        job = Job("1", "test", "Example", "Analyst", "India", "", "Thin", "https://example.test")
        self.assertEqual(check_job_active(job), "closed")
        self.assertEqual(job.verification_reason, "http_not_found")

    @patch("job_search.active.safe_http_get")
    def test_redirect_to_generic_index_has_specific_outcome(self, mock_fetch):
        mock_fetch.return_value = self.response("Browse jobs", "https://himalayas.app/jobs")
        job = Job("1", "Himalayas", "Example", "Analyst", "India", "", "Thin", "https://himalayas.app/companies/example/jobs/analyst", source_type="job_board")
        self.assertEqual(check_job_active(job), "closed")
        self.assertEqual(job.verification_reason, "redirect_to_generic_index")

    def test_unsupported_destination_is_not_attempted(self):
        job = Job("1", "Aggregator", "Example", "Analyst", "India", "", "Thin", "javascript:secret()", source_type="aggregator")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "unsupported_verification_source")

    @patch("job_search.active.safe_http_get", side_effect=UnsafeDestination("SENTINEL_INTERNAL_URL"))
    def test_unsafe_destination_is_sanitized(self, _mock_fetch):
        job = Job("1", "Aggregator", "Example", "Analyst", "India", "", "Thin", "https://example.test", source_type="aggregator")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "unsafe_destination")
        self.assertEqual(job.verification_attempted_host, "")

    @patch("job_search.active.safe_http_get", side_effect=RequestBudgetExhausted("SENTINEL"))
    def test_request_budget_exhaustion_is_sanitized(self, _mock_fetch):
        job = Job("1", "test", "Example", "Analyst", "India", "", "Thin", "https://example.test")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "verification_budget_exhausted")

    @patch("job_search.active.safe_http_get", side_effect=TimeoutError("SENTINEL_SECRET_TIMEOUT"))
    def test_timeout_is_sanitized(self, _mock_fetch):
        job = Job("1", "Aggregator", "Example", "Analyst", "India", "", "Thin", "https://example.test", source_type="aggregator")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "timeout")

    @patch("job_search.active.safe_http_get")
    def test_empty_page_is_malformed_without_raw_content(self, mock_fetch):
        mock_fetch.return_value = self.response(VERIFICATION_FIXTURES["malformed_empty_page"], "https://example.test")
        job = Job("1", "test", "Example", "Analyst", "India", "", "Thin", "https://example.test")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.verification_reason, "malformed_page")
    def test_404_is_closed(self):
        self.assertEqual(classify_active_response(404, ""), "closed")

    def test_410_is_closed(self):
        self.assertEqual(classify_active_response(410, ""), "closed")

    def test_explicit_closed_message_is_closed(self):
        self.assertEqual(
            classify_active_response(200, "Applications are now closed for this position."),
            "closed",
        )

    def test_job_removed_message_is_closed(self):
        self.assertEqual(classify_active_response(200, "Job removed"), "closed")

    def test_normal_job_page_is_active(self):
        self.assertEqual(classify_active_response(200, "Apply now for this opportunity"), "active")

    def test_generic_page_without_application_signal_is_unverified(self):
        self.assertEqual(classify_active_response(200, "Explore careers at Example"), "unverified")

    def test_blocked_page_is_unverified(self):
        self.assertEqual(classify_active_response(403, "Access denied"), "unverified")

    def test_parses_epoch_seconds_and_milliseconds(self):
        self.assertEqual(published_datetime("1786279881"), published_datetime("1786279881000"))

    def test_listing_older_than_limit_is_stale(self):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2026-06-01T00:00:00Z")
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.assertTrue(is_stale(job, 30, now))

    def test_recent_listing_is_not_stale(self):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2026-08-05T00:00:00Z")
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.assertFalse(is_stale(job, 30, now))

    def test_removed_job_redirect_to_listing_index_is_closed(self):
        self.assertTrue(redirected_to_listing_index(
            "https://himalayas.app/companies/steno/jobs/revenue-operations-coordinator",
            "https://himalayas.app/jobs",
        ))

    def test_redirect_to_employer_application_is_not_closed(self):
        self.assertFalse(redirected_to_listing_index(
            "https://example.test/jobs/analyst",
            "https://apply.example-ats.test/analyst",
        ))

    def test_age_limit_zero_allows_old_listing(self):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2024-01-01T00:00:00Z")
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        self.assertFalse(is_stale(job, 0, now))

    def test_freshness_bands_prefer_recent_without_rejecting_old(self):
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        recent = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2026-08-08T00:00:00Z")
        older = Job("2", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2026-06-01T00:00:00Z")
        apply_freshness(recent, preferred_age_days=7, now=now)
        apply_freshness(older, preferred_age_days=7, now=now)
        self.assertEqual(recent.freshness, "fresh")
        self.assertEqual(older.freshness, "older")

    @patch("job_search.active.check_job_active", return_value="active")
    def test_old_but_verified_active_listing_is_retained(self, _check):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2024-01-01T00:00:00Z")
        retained, closed_count, unverified_count = retain_active_jobs(
            [job], maximum_age_days=0, require_verified_active=True
        )
        self.assertEqual(retained, [job])
        self.assertEqual(closed_count, 0)
        self.assertEqual(unverified_count, 0)

    @patch("job_search.active.check_job_active", return_value="unverified")
    def test_unverified_listing_is_excluded_in_strict_mode(self, _check):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test")
        retained, closed_count, unverified_count = retain_active_jobs(
            [job], maximum_age_days=0, require_verified_active=True
        )
        self.assertEqual(retained, [])
        self.assertEqual(closed_count, 0)
        self.assertEqual(unverified_count, 1)

    @patch("job_search.active.check_job_active", return_value="active")
    def test_job_budget_marks_remaining_candidates_without_dropping_them(self, _check):
        jobs = [
            Job(str(index), "test", "Example", "Analyst", "India", "", "", f"https://example.test/{index}")
            for index in range(3)
        ]
        retained, closed, unresolved, metrics = retain_active_jobs(jobs, maximum_jobs=1, return_metrics=True)
        self.assertEqual(len(retained), 3)
        self.assertEqual(closed, 0)
        self.assertEqual(unresolved, 2)
        self.assertEqual(metrics["jobs_submitted"], 1)
        self.assertEqual(metrics["budget_exhausted"], 2)
        self.assertTrue(all(job.verification_reason == "verification_budget_exhausted" for job in jobs[1:]))
