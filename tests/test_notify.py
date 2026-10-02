import unittest

from job_search.notify import build_slack_message


class SlackNotificationTests(unittest.TestCase):
    def test_skips_when_nothing_new_and_no_source_errors(self):
        payload = {"jobs": [{"is_new": False}], "errors": ["Removed 2 definitively closed or expired job posting(s)."]}
        self.assertIsNone(build_slack_message(payload))

    def test_includes_new_job_without_exposing_secrets(self):
        payload = {"jobs": [{"is_new": True, "title": "Revenue Operations Analyst", "company": "Example", "url": "https://example.test/job/1", "screening_score": 90, "location_tier": "remote_india", "active_status": "active"}], "needs_verification": [], "errors": []}
        message = build_slack_message(payload)
        self.assertIn("Revenue Operations Analyst", message)
        self.assertIn("remote_india", message)

    def test_renders_numerical_actual_hiring_fit(self):
        payload = {"jobs": [{"is_new": True, "active_status": "active", "title": "Operations Analyst", "company": "Example", "screening_score": 72, "actual_hiring_fit": 58, "actual_hiring_fit_status": "assessed"}], "errors": []}
        message = build_slack_message(payload)
        self.assertIn("Hiring fit: 58/100", message)

    def test_renders_insufficient_evidence_status(self):
        payload = {"jobs": [{"is_new": True, "active_status": "active", "title": "Operations Analyst", "company": "Example", "screening_score": 72, "actual_hiring_fit": None, "actual_hiring_fit_status": "insufficient_evidence"}], "errors": []}
        message = build_slack_message(payload)
        self.assertIn("Hiring fit: insufficient evidence", message)

    def test_renders_genuinely_not_assessed_status(self):
        payload = {"jobs": [{"is_new": True, "active_status": "active", "title": "Operations Analyst", "company": "Example", "screening_score": 72, "actual_hiring_fit": None, "actual_hiring_fit_status": "not_assessed"}], "errors": []}
        message = build_slack_message(payload)
        self.assertIn("Hiring fit: not assessed", message)

    def test_private_fields_are_not_rendered(self):
        sentinel = "PRIVATE_APPLICATION_NOTE_SENTINEL"
        payload = {"jobs": [{"is_new": True, "active_status": "active", "title": "Operations Analyst", "company": "Example", "screening_score": 72, "actual_hiring_fit": 58, "private_note": sentinel}], "errors": []}
        self.assertNotIn(sentinel, build_slack_message(payload))

    def test_reports_real_source_errors(self):
        payload = {"jobs": [], "errors": ["adzuna/: HTTP Error 500"]}
        self.assertIn("source warning", build_slack_message(payload))
