import unittest

from job_search.notify import build_slack_message


class SlackNotificationTests(unittest.TestCase):
    def test_skips_when_nothing_new_and_no_source_errors(self):
        payload = {"jobs": [{"is_new": False}], "errors": ["Removed 2 definitively closed or expired job posting(s)."]}
        self.assertIsNone(build_slack_message(payload))

    def test_includes_new_job_without_exposing_secrets(self):
        payload = {"jobs": [{"is_new": True, "title": "Revenue Operations Analyst", "company": "Example", "url": "https://example.test/job/1", "score": 90, "location_tier": "remote_india"}], "errors": []}
        message = build_slack_message(payload)
        self.assertIn("Revenue Operations Analyst", message)
        self.assertIn("remote_india", message)

    def test_reports_real_source_errors(self):
        payload = {"jobs": [], "errors": ["adzuna/: HTTP Error 500"]}
        self.assertIn("source warning", build_slack_message(payload))
