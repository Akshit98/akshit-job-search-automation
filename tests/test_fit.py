import json
import unittest
from pathlib import Path

from job_search.core import Job
from job_search.evidence import load_evidence
from job_search.fit import assess_job_fit, calculate_ats_similarity


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = load_evidence(ROOT / "config" / "evidence.json")


def role(description: str, title: str = "Data Operations Analyst") -> Job:
    return Job(
        id="fit-1", source="test", company="Example", title=title,
        location="Remote - India", workplace="Remote", description=description,
        url="https://example.test/job", employment_type="Full-time",
        evidence_quality="sufficient", career_value="high",
    )


class ActualHiringFitTests(unittest.TestCase):
    def test_one_matched_responsibility_cannot_receive_full_direct_credit(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation, survey sampling, participant recruitment, "
            "research fieldwork, study programming, and link testing. Salesforce is required."
        ), EVIDENCE)
        self.assertLess(assessment.category_scores["direct_responsibilities"], 35)

    def test_one_matched_tool_cannot_receive_full_tools_credit(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation and reporting. Required tools are Salesforce, "
            "NetSuite administration, advanced SQL, and Power BI."
        ), EVIDENCE)
        self.assertLess(assessment.category_scores["tools_domain"], 25)

    def test_participant_recruitment_is_not_contact_research(self):
        assessment = assess_job_fit(role(
            "Central responsibilities include participant recruitment, respondent recruitment, survey "
            "sampling, research fieldwork, and link testing for quantitative studies."
        ), EVIDENCE)
        self.assertIn("participant recruitment", assessment.missing_mandatory_requirements)
        self.assertNotIn("contact_research", assessment.used_evidence_ids)

    def test_m3_style_subset_cannot_generate_inflated_fit(self):
        assessment = assess_job_fit(role(
            "Responsibilities include market research, participant recruitment, survey sampling, research "
            "fieldwork, study programming, link testing, project-cost ownership, recruitment-rate KPI "
            "ownership, invoicing reconciliation, and reporting. Excel is required."
        ), EVIDENCE)
        self.assertTrue(
            assessment.actual_hiring_fit is None or assessment.actual_hiring_fit < 70,
            assessment.to_dict(),
        )

    def test_unmatched_mandatory_requirements_remain_in_denominator(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation and carrier operations. Phone-number porting, incident "
            "triage, SLA operations, and telecom escalations are mandatory. Salesforce is required."
        ), EVIDENCE)
        self.assertLess(assessment.ats_similarity, 60)
        self.assertIn("phone-number porting", assessment.missing_mandatory_requirements)

    def test_unclassified_material_requirements_reduce_coverage_confidence(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation. You will calibrate orbital flux manifolds, govern "
            "cryogenic relay lattices, and certify quantum containment protocols every day."
        ), EVIDENCE)
        self.assertLess(assessment.requirement_coverage_confidence, 0.65)
        self.assertIsNone(assessment.actual_hiring_fit)
        self.assertTrue(assessment.unclassified_material_requirements)

    def test_ats_100_requires_complete_supported_material_requirements(self):
        complete = assess_job_fit(role(
            "Responsibilities include data validation, CRM data maintenance, and documentation. "
            "Salesforce and Excel are required."
        ), EVIDENCE)
        incomplete = assess_job_fit(role(
            "Responsibilities include data validation, CRM data maintenance, and survey sampling. "
            "Salesforce and Excel are required."
        ), EVIDENCE)
        self.assertEqual(complete.ats_similarity, 100)
        self.assertLess(incomplete.ats_similarity, 100)

    def test_twilio_style_desired_requirements_stay_preferred(self):
        assessment = assess_job_fit(role(
            "Responsibilities include customer inquiries, issue triage, SLA operations, escalations, and "
            "cross-functional support. Required: three years of operations experience. Desired: previous "
            "telecommunications or financial-services experience and knowledge of SQL."
        ), EVIDENCE)
        mandatory = " ".join(assessment.missing_mandatory_requirements)
        preferred = " ".join(assessment.preferred_requirements)
        self.assertNotIn("sql", mandatory)
        self.assertNotIn("financial services", mandatory)
        self.assertIn("sql", preferred)
        self.assertGreater(assessment.category_scores["transferable_evidence"], 0)
        self.assertNotIn("telecom operations", assessment.matched_mandatory_requirements)

    def test_all_six_categories_and_raw_total_are_retained(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation, CRM data maintenance, duplicate resolution, "
            "market research, reporting, and documentation. Required: Salesforce, Excel, two years "
            "of operations experience, a bachelor's degree, and eligibility to work remotely in India."
        ), EVIDENCE)
        self.assertEqual(set(assessment.category_scores), {
            "direct_responsibilities", "tools_domain", "years_seniority_leadership",
            "transferable_evidence", "education_certifications", "practical_requirements",
        })
        self.assertEqual(assessment.raw_total, sum(assessment.category_scores.values()))
        self.assertEqual(assessment.actual_hiring_fit_status, "assessed")
        self.assertIsNotNone(assessment.actual_hiring_fit)

    def test_two_missing_central_capabilities_apply_59_cap(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation and CRM data maintenance. Candidates must own "
            "financial forecasting and quota administration as central daily responsibilities. "
            "Salesforce and Excel are required."
        ), EVIDENCE)
        self.assertIn("two_or_more_missing_central_capabilities", assessment.cap_reasons)
        self.assertEqual(assessment.applicable_cap, 59)
        self.assertLessEqual(assessment.actual_hiring_fit, 59)

    def test_missing_required_domain_and_core_system_apply_49_cap(self):
        assessment = assess_job_fit(role(
            "This role requires professional fintech domain experience and NetSuite administration. "
            "Responsibilities include data validation and reporting."
        ), EVIDENCE)
        self.assertIn("missing_required_domain_and_core_system", assessment.cap_reasons)
        self.assertEqual(assessment.applicable_cap, 49)

    def test_experience_shortfall_applies_59_cap(self):
        assessment = assess_job_fit(role(
            "Requires at least 6 years of professional data operations experience. Responsibilities "
            "include data validation, reporting, and CRM data maintenance."
        ), EVIDENCE)
        self.assertIn("experience_more_than_two_years_below_minimum", assessment.cap_reasons)
        self.assertEqual(assessment.applicable_cap, 59)

    def test_unsupported_substantial_leadership_applies_59_cap(self):
        assessment = assess_job_fit(role(
            "The manager must lead a team of eight analysts, own hiring, performance management, and "
            "department strategy. Salesforce reporting is also required.",
            title="Data Operations Manager",
        ), EVIDENCE)
        self.assertIn("unsupported_substantial_leadership", assessment.cap_reasons)
        self.assertEqual(assessment.applicable_cap, 59)

    def test_multiple_caps_use_strictest_ceiling(self):
        assessment = assess_job_fit(role(
            "Requires 7 years of experience, financial-services domain expertise, NetSuite administration, "
            "financial forecasting, and quota administration. Lead a team with direct reports."
        ), EVIDENCE)
        self.assertEqual(assessment.applicable_cap, 49)
        self.assertGreaterEqual(len(assessment.cap_reasons), 2)

    def test_preferred_skill_is_not_a_mandatory_gap(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation and reporting. Salesforce and Excel are required. "
            "Advanced SQL is preferred but not required."
        ), EVIDENCE)
        self.assertNotIn("sql", assessment.missing_mandatory_requirements)
        self.assertIn("sql", " ".join(assessment.preferred_requirements))

    def test_learning_only_tools_do_not_count_as_professional_proficiency(self):
        assessment = assess_job_fit(role(
            "Advanced SQL, Python, and Power BI are mandatory core tools. Responsibilities include reporting."
        ), EVIDENCE)
        missing = " ".join(assessment.missing_mandatory_requirements)
        self.assertIn("sql", missing)
        self.assertIn("python", missing)
        self.assertIn("power bi", missing)

    def test_revops_ownership_is_not_inferred_from_crm_support(self):
        assessment = assess_job_fit(role(
            "Formal Revenue Operations ownership is required, including forecasting ownership, quota "
            "administration, SaaS metrics, and advanced CRM systems ownership."
        ), EVIDENCE)
        missing = " ".join(assessment.missing_mandatory_requirements)
        self.assertIn("revenue operations ownership", missing)
        self.assertIn("forecasting ownership", missing)

    def test_insufficient_jd_returns_no_fit_score(self):
        job = role("Great company with an exciting opportunity.")
        job.evidence_quality = "insufficient"
        assessment = assess_job_fit(job, EVIDENCE)
        self.assertIsNone(assessment.actual_hiring_fit)
        self.assertEqual(assessment.actual_hiring_fit_status, "insufficient_evidence")

    def test_partial_evidence_returns_no_fit_score(self):
        job = role(
            "Responsibilities include data validation, CRM data maintenance, reporting, documentation, "
            "and account research. Salesforce and Excel are required."
        )
        job.evidence_quality = "partial"
        assessment = assess_job_fit(job, EVIDENCE)
        self.assertIsNone(assessment.actual_hiring_fit)
        self.assertEqual(assessment.actual_hiring_fit_status, "insufficient_evidence")
        self.assertLess(assessment.ats_similarity, 100)

    def test_evidence_ids_are_not_double_counted(self):
        assessment = assess_job_fit(role(
            "Responsibilities include data validation, duplicate resolution, CRM data maintenance, reporting, "
            "and documentation. Salesforce and Excel are required."
        ), EVIDENCE)
        used = assessment.used_evidence_ids
        self.assertEqual(len(used), len(set(used)))

    def test_ats_similarity_is_separate_and_excludes_learning_only(self):
        job = role(
            "Responsibilities include data validation, reporting, and CRM data maintenance. "
            "Salesforce and Excel are required. Advanced SQL and Python are mandatory."
        )
        assessment = assess_job_fit(job, EVIDENCE)
        similarity = calculate_ats_similarity(job, EVIDENCE)
        self.assertEqual(assessment.ats_similarity, similarity)
        self.assertNotEqual(similarity, assessment.actual_hiring_fit)
        self.assertIn("sql", " ".join(assessment.missing_mandatory_requirements))

    def test_career_value_does_not_change_fit(self):
        job = role("Responsibilities include data validation and reporting. Salesforce is required.")
        first = assess_job_fit(job, EVIDENCE)
        job.career_value = "low"
        second = assess_job_fit(job, EVIDENCE)
        self.assertEqual(first.actual_hiring_fit, second.actual_hiring_fit)
