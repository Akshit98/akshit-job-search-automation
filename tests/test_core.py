import unittest

from job_search.core import Job, evaluate, job_fingerprint, location_tier, monthly_compensation_inr


PROFILE = {
    "minimum_fit_score": 45,
    "internship_min_monthly_inr": 40000,
    "role_terms": ["sales operations", "data quality", "market research"],
    "skills": ["salesforce", "crm", "excel", "research"],
    "excluded_titles": ["director"],
}


def job(**kwargs):
    base = dict(id="1", source="test", company="Example", title="Sales Operations Analyst", location="Remote - India", workplace="Remote", description="Salesforce CRM data quality and Excel research", url="https://example.test/job", employment_type="Full-time")
    base.update(kwargs)
    return Job(**base)


class LocationTests(unittest.TestCase):
    def test_remote_india_first(self):
        self.assertEqual(location_tier(job()), "remote_india")

    def test_global_anywhere(self):
        self.assertEqual(location_tier(job(location="Anywhere", description="Work from anywhere worldwide")), "global_work_from_anywhere")

    def test_hyderabad_before_other_india(self):
        self.assertEqual(location_tier(job(location="Hyderabad", workplace="Hybrid")), "hyderabad")

    def test_rejects_us_only_remote(self):
        self.assertIsNone(location_tier(job(location="Remote, US", description="Candidates must reside in the United States")))

    def test_rejects_emea_even_if_description_mentions_india(self):
        self.assertIsNone(location_tier(job(location="Remote-EMEA", description="Our company also has employees in India")))


class PayTests(unittest.TestCase):
    def test_monthly_rupees(self):
        self.assertEqual(monthly_compensation_inr("Stipend: INR 40,000 per month"), 40000)

    def test_annual_rupees(self):
        self.assertEqual(monthly_compensation_inr("INR 6 lakh per year"), 50000)

    def test_missing_pay_is_unknown(self):
        self.assertIsNone(monthly_compensation_inr("Competitive stipend"))

    def test_low_paid_internship_rejected(self):
        candidate = job(title="Market Research Intern", employment_type="Internship", compensation="INR 30,000 per month")
        self.assertIsNone(evaluate(candidate, PROFILE))

    def test_qualified_internship_accepted(self):
        candidate = job(title="Market Research Intern", employment_type="Internship", compensation="INR 40,000 per month")
        self.assertIsNotNone(evaluate(candidate, PROFILE))


class FitTests(unittest.TestCase):
    def test_matching_full_time_role_accepted(self):
        self.assertIsNotNone(evaluate(job(), PROFILE))

    def test_contract_rejected(self):
        self.assertIsNone(evaluate(job(employment_type="Contract"), PROFILE))

    def test_director_title_rejected(self):
        self.assertIsNone(evaluate(job(title="Director of Sales Operations"), PROFILE))

    def test_cross_source_fingerprint_normalizes_bangalore(self):
        first = job(company="Example, Inc.", location="Bangalore", title="Sales Operations Analyst")
        second = job(company="Example Inc", location="Bengaluru", title="Sales Operations Analyst")
        first.location_tier = "bengaluru"
        second.location_tier = "bengaluru"
        self.assertEqual(job_fingerprint(first), job_fingerprint(second))

    def test_cross_source_fingerprint_normalizes_common_aliases(self):
        first = job(company="Example Private Limited", title="Sr. RevOps Analyst", location="Worldwide")
        second = job(company="Example", title="Senior Revenue Operations Analyst", location="Remote")
        first.location_tier = "global_work_from_anywhere"
        second.location_tier = "global_work_from_anywhere"
        self.assertEqual(job_fingerprint(first), job_fingerprint(second))

    def test_fingerprint_keeps_seniority_distinct(self):
        junior = job(title="Junior Sales Operations Analyst")
        senior = job(title="Senior Sales Operations Analyst")
        junior.location_tier = senior.location_tier = "remote_india"
        self.assertNotEqual(job_fingerprint(junior), job_fingerprint(senior))


if __name__ == "__main__":
    unittest.main()
