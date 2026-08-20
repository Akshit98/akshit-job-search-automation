import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from job_search.active import classify_active_response, is_stale, published_datetime, retain_active_jobs
from job_search.core import Job


class ActiveStatusTests(unittest.TestCase):
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

    def test_age_limit_zero_allows_old_listing(self):
        job = Job("1", "test", "Example", "Analyst", "India", "", "", "https://example.test", published_at="2024-01-01T00:00:00Z")
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        self.assertFalse(is_stale(job, 0, now))

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
