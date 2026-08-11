import unittest
from datetime import datetime, timezone

from job_search.active import classify_active_response, is_stale, published_datetime, redirected_to_listing_index
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

    def test_normal_job_page_is_active(self):
        self.assertEqual(classify_active_response(200, "Apply now for this opportunity"), "active")

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
