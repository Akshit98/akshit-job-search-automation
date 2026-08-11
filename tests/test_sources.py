import unittest
from unittest.mock import patch

from job_search.sources import himalayas


class HimalayasLocationTests(unittest.TestCase):
    def test_string_location_restrictions_are_preserved(self):
        payload = {"jobs": [{
            "guid": "https://himalayas.app/companies/steno/jobs/revenue-operations-coordinator",
            "title": "Revenue Operations Coordinator",
            "companyName": "Steno",
            "locationRestrictions": ["United States"],
            "description": "Remote revenue operations role",
            "applicationLink": "https://himalayas.app/companies/steno/jobs/revenue-operations-coordinator",
            "employmentType": "Full Time",
        }]}
        with patch("job_search.sources.get_json", return_value=payload):
            jobs = himalayas("")
        self.assertTrue(jobs)
        self.assertTrue(all(job.location == "United States" for job in jobs))

    def test_missing_location_restrictions_are_not_called_worldwide(self):
        payload = {"jobs": [{
            "guid": "job-1", "title": "Operations Analyst", "companyName": "Example",
            "description": "Remote role", "applicationLink": "https://example.test/job-1",
            "employmentType": "Full Time",
        }]}
        with patch("job_search.sources.get_json", return_value=payload):
            jobs = himalayas("")
        self.assertTrue(all(job.location == "" for job in jobs))
