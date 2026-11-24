import unittest
from unittest.mock import patch

from job_search.core import Job
from job_search.fit import _ats_from_requirements


class AtsExperienceRegressionTests(unittest.TestCase):
    def score(self, description, years=3):
        job = Job(id="test", source="test", company="Example",
                  title="Analyst", location="India", workplace="remote",
                  description=description, url="https://example.com/job")
        return _ats_from_requirements(
            [], {"overall_experience_years_date_calculated": years}, job
        )

    def test_zero_years(self):
        self.assertEqual(self.score("0 years of experience", 0), 100)

    def test_zero_to_two_years(self):
        self.assertEqual(self.score("0-2 years of experience", 0), 100)

    def test_negative_minimum_defensive_guard(self):
        with patch("job_search.fit.minimum_required_experience_years", return_value=-1):
            self.assertEqual(self.score("Experience requirement", 0), 100)

    def test_positive_minimum_partial_match(self):
        self.assertEqual(self.score("4 years of experience", 3), 75)

    def test_positive_minimum_full_match(self):
        self.assertEqual(self.score("2 years of experience", 3), 100)

    def test_unspecified_minimum(self):
        self.assertEqual(self.score("Experience preferred"), 0)


if __name__ == "__main__":
    unittest.main()
