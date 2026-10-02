import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

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


class ActiveStatusTests(unittest.TestCase):
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

    @patch("job_search.active.urlopen")
    def test_jsonld_after_300kb_is_still_read(self, mock_urlopen):
        prefix = "x" * 350_000
        html = prefix + '<script type="application/ld+json">{"@type":"JobPosting","description":"Responsibilities: maintain CRM records. Requirements: Salesforce experience and operational reporting."}</script><a>Apply now</a>'
        response = mock_urlopen.return_value.__enter__.return_value
        response.read.return_value = html.encode("utf-8")
        response.geturl.return_value = "https://example.test/job"
        response.getcode.return_value = 200
        job = Job("late", "test", "Example", "CRM Operations", "India", "", "Thin text", "https://example.test/job")
        self.assertEqual(check_job_active(job), "active")
        self.assertEqual(job.description_retrieval_status, "enriched")
        self.assertIn("Salesforce experience", job.description)
        response.read.assert_called_once_with(MAX_JOB_PAGE_BYTES + 1)

    @patch("job_search.active.urlopen")
    def test_oversized_page_is_explicitly_unverified(self, mock_urlopen):
        response = mock_urlopen.return_value.__enter__.return_value
        response.read.return_value = b"x" * (MAX_JOB_PAGE_BYTES + 1)
        response.geturl.return_value = "https://example.test/job"
        job = Job("large", "test", "Example", "Operations", "India", "", "Original", "https://example.test/job")
        self.assertEqual(check_job_active(job), "unverified")
        self.assertEqual(job.description_retrieval_status, "page_too_large")
        self.assertEqual(job.description, "Original")
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
