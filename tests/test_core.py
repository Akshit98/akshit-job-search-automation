import unittest

from job_search.core import (
    Job,
    assign_screening_queue,
    annual_compensation_inr,
    canonicalize_jobs,
    contains_term,
    evaluate,
    job_fingerprint,
    legacy_job_fingerprint,
    location_tier,
    minimum_required_experience_years,
    monthly_compensation_inr,
    normalize_company_name,
    same_vacancy,
    sanitize_public_url,
    seen_identity_keys,
)


PROFILE = {
    "minimum_fit_score": 45,
    "internship_min_monthly_inr": 40000,
    "maximum_required_experience_years": 5,
    "role_terms": ["sales operations", "data quality", "market research"],
    "skills": ["salesforce", "crm", "excel", "research"],
    "excluded_titles": ["director"],
    "screening_exclusion_terms": ["cold calling", "sdr", "meeting setting"],
    "mandatory_advanced_skill_terms": ["advanced sql", "advanced python", "advanced power bi", "advanced excel"],
    "salary_target_lpa": {"remote_india": 10, "hyderabad": 10, "bengaluru": 10, "other_india": 10},
}


def job(**kwargs):
    base = dict(id="1", source="test", company="Example", title="Sales Operations Analyst", location="Remote - India", workplace="Remote", description="Salesforce CRM data quality and Excel research", url="https://example.test/job", employment_type="Full-time")
    base.update(kwargs)
    return Job(**base)


class LocationTests(unittest.TestCase):
    def test_explicit_coimbatore_is_not_overridden_by_bangalore_in_description(self):
        listing = job(
            location="Coimbatore, Tamil Nadu",
            workplace="On-site",
            description="The company also has offices in Chennai and Bangalore.",
        )
        self.assertEqual(location_tier(listing), "other_india")

    def test_canonical_location_is_used_only_when_source_location_is_missing(self):
        listing = job(location="", workplace="", canonical_location="Hyderabad, India", description="Office list: Bangalore")
        self.assertEqual(location_tier(listing), "hyderabad")

    def test_remote_role_limited_to_non_india_regions_is_rejected(self):
        job = Job(
            "restricted", "test", "Example", "Operations Coordinator",
            "Worldwide", "Remote",
            "This remote role is open only to candidates in Latin America and the Philippines.",
            "https://example.test/restricted",
        )
        self.assertIsNone(location_tier(job))

    def test_remote_india_first(self):
        self.assertEqual(location_tier(job()), "remote_india")

    def test_global_anywhere(self):
        self.assertEqual(location_tier(job(location="Anywhere", description="Work from anywhere worldwide")), "global_work_from_anywhere")


class IdentityTests(unittest.TestCase):
    def test_legal_suffixes_and_safe_typo_normalize_together(self):
        variants = (
            "Alphacom Systems and Solutions Priavte Limited",
            "Alphacom Systems & Solutions Pvt. Ltd.",
            "Alphacom Systems and Solutions Private Limited",
        )
        self.assertEqual(len({normalize_company_name(value) for value in variants}), 1)

    def test_different_employers_do_not_collapse(self):
        self.assertNotEqual(normalize_company_name("Alpha Systems Pvt Ltd"), normalize_company_name("Alphacom Systems Pvt Ltd"))

    def test_sensitive_url_parameters_are_removed(self):
        safe = sanitize_public_url("https://jobs.example.test/opening?id=7&api_key=SENTINEL&token=SECRET&utm_source=test")
        self.assertEqual(safe, "https://jobs.example.test/opening?id=7")
        self.assertNotIn("SENTINEL", safe)

    def test_meaningful_job_query_is_preserved_and_sorted(self):
        safe = sanitize_public_url("https://jobs.example.test/opening?team=ops&job_id=7&utm_source=board")
        self.assertEqual(safe, "https://jobs.example.test/opening?job_id=7&team=ops")

    def test_tracking_differences_share_canonical_url_identity(self):
        left = job(id="a", canonical_employer_url="https://jobs.example.test/opening/7?utm_source=one")
        right = job(id="b", canonical_employer_url="https://jobs.example.test/opening/7?utm_medium=two")
        self.assertTrue(same_vacancy(left, right))

    def test_equivalent_official_urls_merge(self):
        left = job(id="a", canonical_employer_url="https://jobs.example.test:443/opening/7/?utm_source=one")
        right = job(id="b", canonical_employer_url="https://jobs.example.test/opening/7")
        self.assertTrue(same_vacancy(left, right))

    def test_different_query_requisition_ids_do_not_share_url_identity(self):
        left = job(id="a", source_type="direct_employer", canonical_employer_url="https://jobs.example.test/opening?job_id=7")
        right = job(id="b", source_type="direct_employer", canonical_employer_url="https://jobs.example.test/opening?job_id=8")
        self.assertFalse(same_vacancy(left, right))

    def test_conflicting_official_urls_override_highly_similar_content(self):
        description = "Responsibilities include CRM data quality reporting and operational support for partner teams." * 4
        left = job(id="a", canonical_employer_url="https://jobs.example.test/opening/7", description=description)
        right = job(id="b", canonical_employer_url="https://jobs.example.test/opening/8", description=description)
        self.assertFalse(same_vacancy(left, right))

    def test_conflicting_official_urls_override_shared_boilerplate(self):
        shared = "we are an equal opportunity employer committed to an inclusive workplace for everyone"
        left = job(id="a", canonical_employer_url="https://jobs.example.test/opening/7", description=f"{shared}. Maintain CRM data quality.")
        right = job(id="b", canonical_employer_url="https://jobs.example.test/opening/8", description=f"{shared}. Own billing and quota forecasts.")
        self.assertFalse(same_vacancy(left, right))

    def test_direct_and_aggregator_conflicting_official_requisitions_stay_separate(self):
        description = "Responsibilities include CRM data quality reporting and operational support for partner teams." * 3
        direct = job(id="lever:7", source_type="direct_employer", canonical_employer_url="https://jobs.example.test/opening/7", description=description)
        aggregator = job(id="adzuna:8", source_type="aggregator", canonical_employer_url="https://jobs.example.test/opening/8", description=description)
        self.assertFalse(same_vacancy(direct, aggregator))

    def test_cross_location_aggregator_copies_cluster_when_content_matches(self):
        description = "Responsibilities include CRM data quality reporting and operational support for partner teams. " * 6
        left = job(id="a", company="Example Pvt Ltd", location="Bengaluru", location_tier="bengaluru", description=description, source_type="aggregator", source_priority=20)
        right = job(id="b", company="Example Private Limited", location="Hyderabad", location_tier="hyderabad", description=description, source_type="aggregator", source_priority=20)
        self.assertTrue(same_vacancy(left, right))
        representatives, duplicates = canonicalize_jobs([left, right])
        self.assertEqual(len(representatives), 1)
        self.assertEqual(len(duplicates), 1)

    def test_shifted_aggregator_excerpts_with_shared_jd_passage_cluster(self):
        shared = "responsible for managing key aspects of process governance and operations for the global professional services operations team including reporting analytics ownership maintenance of CRM systems stakeholder coordination recurring quality reviews issue tracking process documentation and tactical support"
        left = job(id="a", company="Example Ltd", title="Services Operations Analyst", location="India", source_type="aggregator", description=f"Company introduction and benefits. {shared}. Reporting and CRM support with documented weekly reviews and partner coordination.")
        right = job(id="b", company="Example", title="Services Operations Analyst", location="Hyderabad", source_type="aggregator", description=f"Inclusive employer notice. Please contact us. {shared}. Partner support with documented weekly reviews and customer coordination.")
        self.assertTrue(same_vacancy(left, right))

    def test_single_company_introduction_passage_does_not_merge_jobs(self):
        shared = "our company builds trusted software products for customers around the world every day"
        left = job(id="a", source_type="aggregator", description=f"{shared}. Maintain CRM records and prepare operations reports.")
        right = job(id="b", source_type="aggregator", description=f"{shared}. Manage billing forecasts and sales commissions.")
        self.assertFalse(same_vacancy(left, right))

    def test_shared_eeo_and_benefits_boilerplate_does_not_merge_jobs(self):
        shared = "equal opportunity employer offering health insurance paid leave and employee wellness benefits"
        left = job(id="a", source_type="aggregator", description=f"Maintain customer data and validate CRM records. {shared}.")
        right = job(id="b", source_type="aggregator", description=f"Develop software services and own production deployments. {shared}.")
        self.assertFalse(same_vacancy(left, right))

    def test_direct_employer_outranks_aggregator_copy(self):
        description = "Responsibilities include CRM data quality reporting and operational support for partner teams. " * 6
        direct = job(id="lever:1", company="Example", source="lever", source_type="direct_employer", source_priority=100, canonical_employer_url="https://jobs.example.test/1", description=description)
        aggregator = job(id="adzuna:1", company="Example", source="Adzuna", source_type="aggregator", source_priority=20, description=description)
        representatives, _ = canonicalize_jobs([aggregator, direct])
        self.assertEqual(representatives[0].id, "lever:1")

    def test_direct_and_aggregator_same_title_need_corroborating_content(self):
        direct = job(id="lever:1", company="Example", source_type="direct_employer", description="Maintain CRM records and customer operations reporting for enterprise teams.")
        aggregator = job(id="adzuna:1", company="Example", source_type="aggregator", description="Own quota plans, billing forecasts, and sales commissions for regional leaders.")
        self.assertFalse(same_vacancy(direct, aggregator))

    def test_distinct_direct_requisition_ids_are_preserved(self):
        left = job(id="lever:1", source_type="direct_employer", source_priority=100, description="CRM reporting for customer operations")
        right = job(id="lever:2", source_type="direct_employer", source_priority=100, description="CRM reporting for partner operations")
        self.assertFalse(same_vacancy(left, right))

    def test_same_employer_title_tier_separate_direct_openings_are_preserved(self):
        left = job(id="lever:1", source_type="direct_employer", location_tier="remote_india", description="Customer operations reporting and CRM data maintenance.")
        right = job(id="lever:2", source_type="direct_employer", location_tier="remote_india", description="Partner onboarding, implementation planning, and service delivery.")
        representatives, duplicates = canonicalize_jobs([left, right])
        self.assertEqual(len(representatives), 2)
        self.assertEqual(duplicates, [])

    def test_same_title_with_materially_different_descriptions_is_preserved(self):
        left = job(id="a", source_type="aggregator", description="Maintain CRM records and prepare weekly operations reports for partner teams.", location="Pune", location_tier="other_india")
        right = job(id="b", source_type="aggregator", description="Own billing forecasts, revenue accounting, quota administration, and commission plans.", location="Pune", location_tier="other_india")
        self.assertFalse(same_vacancy(left, right))

    def test_seen_identity_accepts_source_legacy_and_versioned_fingerprints(self):
        candidate = job(id="lever:7", location_tier="remote_india")
        keys = seen_identity_keys(candidate)
        self.assertIn("lever:7", keys)
        self.assertIn(f"fp:{legacy_job_fingerprint(candidate)}", keys)
        self.assertIn(f"fp:v2:{job_fingerprint(candidate)}", keys)

    def test_canonical_representative_inherits_seen_status(self):
        description = "Responsibilities include CRM data quality reporting and operational support for partner teams. " * 6
        aggregator = job(id="adzuna:1", source_type="aggregator", source_priority=20, description=description, is_new=False)
        direct = job(id="lever:1", source_type="direct_employer", source_priority=100, description=description, is_new=True)
        representatives, _ = canonicalize_jobs([aggregator, direct])
        self.assertEqual(representatives[0].id, "lever:1")
        self.assertFalse(representatives[0].is_new)

    def test_stable_source_id_protects_normalization_and_location_migrations(self):
        changed = job(id="adzuna:7", company="Example Private Limited", location_tier="other_india")
        self.assertFalse(seen_identity_keys(changed).isdisjoint({"adzuna:7"}))

    def test_genuinely_new_vacancy_has_no_seen_identity(self):
        candidate = job(id="lever:new", location_tier="remote_india")
        self.assertTrue(seen_identity_keys(candidate).isdisjoint({"lever:old", "fp:unrelated"}))

    def test_hyderabad_before_other_india(self):
        self.assertEqual(location_tier(job(location="Hyderabad", workplace="Hybrid")), "hyderabad")

    def test_rejects_us_only_remote(self):
        self.assertIsNone(location_tier(job(location="Remote, US", description="Candidates must reside in the United States")))

    def test_rejects_emea_even_if_description_mentions_india(self):
        self.assertIsNone(location_tier(job(location="Remote-EMEA", description="Our company also has employees in India")))

    def test_rejects_us_only_himalayas_role(self):
        self.assertIsNone(location_tier(job(location="United States", workplace="Remote")))

    def test_does_not_treat_indiana_as_india(self):
        self.assertIsNone(location_tier(job(location="Indiana, United States", workplace="Remote")))

    def test_india_inclusive_apac_role_is_eligible(self):
        self.assertEqual(
            location_tier(job(location="Remote - APAC (India eligible)", workplace="Remote")),
            "remote_india",
        )


class PayTests(unittest.TestCase):
    def test_monthly_rupees(self):
        self.assertEqual(monthly_compensation_inr("Stipend: INR 40,000 per month"), 40000)

    def test_annual_rupees(self):
        self.assertEqual(monthly_compensation_inr("INR 6 lakh per year"), 50000)

    def test_annual_range_uses_lower_bound(self):
        self.assertEqual(
            annual_compensation_inr("₹ 9.5-10 Lacs P.A."),
            950000,
        )

    def test_monthly_salary_converts_to_annual(self):
        self.assertEqual(
            annual_compensation_inr("From ₹1,43,600 a month"),
            1723200,
        )

    def test_missing_pay_is_unknown(self):
        self.assertIsNone(monthly_compensation_inr("Competitive stipend"))

    def test_low_paid_internship_rejected(self):
        candidate = job(title="Market Research Intern", employment_type="Internship", compensation="INR 30,000 per month")
        self.assertIsNone(evaluate(candidate, PROFILE))

    def test_qualified_internship_accepted(self):
        candidate = job(title="Market Research Intern", employment_type="Internship", compensation="INR 40,000 per month")
        self.assertIsNotNone(evaluate(candidate, PROFILE))

    def test_onsite_internship_at_exact_threshold_is_rejected(self):
        candidate = job(
            title="Market Research Intern",
            location="Bengaluru",
            workplace="On-site",
            employment_type="Internship",
            compensation="INR 40,000 per month",
        )
        self.assertIsNone(evaluate(candidate, PROFILE))

    def test_onsite_internship_above_threshold_is_accepted(self):
        candidate = job(
            title="Market Research Intern",
            location="Bengaluru",
            workplace="On-site",
            employment_type="Internship",
            compensation="INR 40,001 per month",
        )
        self.assertIsNotNone(evaluate(candidate, PROFILE))


class FitTests(unittest.TestCase):
    def test_sales_operations_title_with_central_outbound_campaigns_is_excluded(self):
        candidate = job(
            title="Sales Operations Coordinator",
            description=(
                "Responsibilities include sourcing 1,000 prospects weekly, executing cold-email campaigns, "
                "building outbound sequences, managing prospect replies, and monitoring deliverability."
            ),
        )
        result = evaluate(candidate, PROFILE)
        self.assertIsNotNone(result)
        self.assertIn("outbound_sales", result.exclusion_signals)

    def test_matching_full_time_role_accepted(self):
        self.assertIsNotNone(evaluate(job(), PROFILE))

    def test_contract_rejected(self):
        self.assertIsNone(evaluate(job(employment_type="Contract"), PROFILE))

    def test_full_time_contract_onboarding_work_is_not_misread_as_contract_job(self):
        candidate = job(
            employment_type="Full-time",
            description="Salesforce CRM data quality, Excel research, and contract onboarding support",
        )
        self.assertIsNotNone(evaluate(candidate, PROFILE))

    def test_role_requiring_more_than_five_years_is_rejected(self):
        candidate = job(description="Requires 6+ years of experience in sales operations")
        self.assertIsNone(evaluate(candidate, PROFILE))

    def test_extracts_minimum_from_experience_range(self):
        self.assertEqual(
            minimum_required_experience_years("Candidates need 3-5 years of experience."),
            3,
        )

    def test_short_acronym_gap_does_not_match_inside_normal_word(self):
        self.assertFalse(contains_term("investigate operational situations", "uat"))
        self.assertTrue(contains_term("support UAT execution", "uat"))

    def test_director_title_rejected(self):
        result = evaluate(job(title="Director of Sales Operations"), PROFILE)
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertTrue(result.hard_excluded)

    def test_engineering_title_is_rejected_for_non_coding_targets(self):
        profile = {**PROFILE, "excluded_titles": [*PROFILE["excluded_titles"], "engineer", "developer"]}
        result = evaluate(job(title="Informatica MDM Senior Engineer"), profile)
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertTrue(result.hard_excluded)

    def test_actual_hiring_fit_is_not_assessed_in_phase_one(self):
        candidate = evaluate(job(), PROFILE)
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate.actual_hiring_fit)
        self.assertEqual(candidate.actual_hiring_fit_status, "not_assessed")

    def test_score_is_named_screening_score(self):
        candidate = evaluate(job(), PROFILE)
        self.assertGreater(candidate.screening_score, 0)
        self.assertFalse(hasattr(candidate, "score"))

    def test_location_does_not_change_screening_score(self):
        remote = evaluate(job(location="Remote - India", workplace="Remote"), PROFILE)
        bengaluru = evaluate(job(location="Bengaluru", workplace="Hybrid"), PROFILE)
        self.assertEqual(remote.screening_score, bengaluru.screening_score)

    def test_primary_role_can_clear_screen_without_location_bonus(self):
        profile = {**PROFILE, "minimum_fit_score": 40}
        candidate = evaluate(job(description="Sales operations using Salesforce and Excel reporting"), profile)
        self.assertIsNotNone(candidate)

    def test_disclosed_below_target_salary_remains_visible(self):
        candidate = evaluate(job(compensation="INR 8 lakh per year"), PROFILE)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.compensation_assessment, "below_target")
        self.assertIn("below INR 10.0 LPA target", " ".join(candidate.screening_reasons))

    def test_unpublished_salary_is_not_rejected(self):
        candidate = evaluate(job(compensation=""), PROFILE)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.compensation_assessment, "not_disclosed")

    def test_aggregator_salary_does_not_increase_screening_score(self):
        undisclosed = evaluate(job(compensation=""), PROFILE)
        estimate = evaluate(job(compensation="INR 20 lakh per year", compensation_source="aggregator_estimate"), PROFILE)
        self.assertEqual(undisclosed.screening_score, estimate.screening_score)
        self.assertEqual(estimate.compensation_assessment, "estimate_only")

    def test_career_value_does_not_change_screening_score(self):
        baseline = evaluate(job(), PROFILE)
        flagged = evaluate(job(career_value="high"), PROFILE)
        self.assertEqual(baseline.screening_score, flagged.screening_score)

    def test_cold_calling_role_is_rejected(self):
        candidate = job(description="Salesforce CRM data quality and Excel research plus mandatory cold calling")
        result = evaluate(candidate, PROFILE)
        self.assertTrue(result.hard_excluded)
        self.assertEqual(result.screening_queue, "suppressed")

    def test_market_research_title_with_outbound_responsibilities_is_excluded(self):
        candidate = job(
            title="Market Research Analyst",
            description=(
                "Responsibilities include outbound lead generation, cold calling prospects, "
                "cold email outreach, and setting meetings for the sales team."
            ),
        )
        result = evaluate(candidate, PROFILE)
        self.assertTrue(result.hard_excluded)
        self.assertIn("outbound_sales", result.exclusion_signals)

    def test_business_development_with_prospecting_is_excluded(self):
        candidate = job(
            title="Business Development Executive",
            description="You will prospect outbound accounts, contact decision makers, and set sales meetings.",
        )
        result = evaluate(candidate, PROFILE)
        self.assertTrue(result.hard_excluded)

    def test_company_sales_team_mention_does_not_exclude_operations_role(self):
        candidate = job(description="This company has a sales team. The role maintains CRM data quality and Excel reports.")
        result = evaluate(candidate, PROFILE)
        self.assertFalse(result.hard_excluded)

    def test_security_operations_is_domain_suppressed(self):
        result = evaluate(job(title="Security Operations Analyst", description="Monitor cyber threats and security incidents."), PROFILE)
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertIn("cybersecurity", result.domain_conflicts)

    def test_thin_security_operations_title_is_still_domain_suppressed(self):
        result = evaluate(
            job(
                source="lever",
                company="jobgether",
                title="ICC - Security Operations Analyst - Cyber Defense",
                description="How Jobgether works: applications are shared with the hiring company.",
            ),
            PROFILE,
        )
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertIn("cybersecurity", result.domain_conflicts)

    def test_digital_assets_operations_is_domain_suppressed(self):
        result = evaluate(job(title="Digital Assets Operations Analyst", description="Cryptocurrency market operations."), PROFILE)
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertIn("crypto_digital_assets", result.domain_conflicts)

    def test_finance_ar_bill_to_pay_is_domain_suppressed(self):
        for title in ("Accounts Receivable Operations Specialist", "Bill to Pay Operations Analyst"):
            with self.subTest(title=title):
                result = evaluate(job(title=title, description="Own billing, payment reconciliation, and collections."), PROFILE)
                self.assertEqual(result.screening_queue, "suppressed")
                self.assertIn("finance_accounting", result.domain_conflicts)

    def test_people_operations_is_outside_target(self):
        result = evaluate(job(title="People Operations Coordinator", description="Support HR policies and employee programs."), PROFILE)
        self.assertIn(result.screening_queue, ("review_queue", "suppressed"))
        self.assertIn("people_hr", result.domain_conflicts)

    def test_unsupported_leadership_role_is_suppressed(self):
        result = evaluate(
            job(title="Lead Data Operations Analyst", description="Lead a team of eight analysts with direct reports and hiring responsibility."),
            PROFILE,
        )
        self.assertEqual(result.screening_queue, "suppressed")
        self.assertIn("unsupported_people_leadership", result.exclusion_signals)

    def test_senior_title_without_people_leadership_is_penalized_not_automatically_rejected(self):
        base = evaluate(job(title="Sales Operations Analyst"), PROFILE)
        senior = evaluate(job(title="Senior Sales Operations Analyst"), PROFILE)
        self.assertFalse(senior.hard_excluded)
        self.assertLess(senior.screening_score, base.screening_score)

    def test_thin_aggregator_description_cannot_enter_strong_shortlist(self):
        candidate = job(
            source="lever",
            company="jobgether",
            title="Data Operations Analyst",
            description="How Jobgether works: applications are matched and shared with the hiring company.",
        )
        result = evaluate(candidate, {**PROFILE, "minimum_fit_score": 0})
        result.screening_score = 60
        result.active_status = "active"
        assign_screening_queue(result)
        self.assertEqual(result.evidence_quality, "insufficient")
        self.assertEqual(result.screening_queue, "review_queue")

    def test_sparse_relevant_jd_goes_to_review_queue(self):
        result = evaluate(job(title="Data Operations Analyst", description="Maintain operational data."), {**PROFILE, "minimum_fit_score": 0})
        result.screening_score = 42
        result.active_status = "active"
        assign_screening_queue(result)
        self.assertEqual(result.screening_queue, "review_queue")

    def test_verified_45_plus_role_enters_strong_shortlist(self):
        result = evaluate(job(description="Responsibilities include CRM data quality, Salesforce maintenance, Excel reporting, and documented quality review. Requirements include two years of operations experience."), PROFILE)
        result.screening_score = 50
        result.active_status = "active"
        result.evidence_quality = "sufficient"
        assign_screening_queue(result)
        self.assertEqual(result.screening_queue, "strong_shortlist")

    def test_unverified_45_plus_role_goes_to_review_queue(self):
        result = evaluate(job(), PROFILE)
        result.screening_score = 50
        result.active_status = "unverified"
        result.evidence_quality = "sufficient"
        assign_screening_queue(result)
        self.assertEqual(result.screening_queue, "review_queue")

    def test_30_to_44_role_goes_to_review_queue(self):
        result = evaluate(job(), PROFILE)
        result.screening_score = 40
        result.active_status = "active"
        assign_screening_queue(result)
        self.assertEqual(result.screening_queue, "review_queue")

    def test_below_30_role_is_suppressed(self):
        result = evaluate(job(), PROFILE)
        result.screening_score = 29
        result.active_status = "active"
        assign_screening_queue(result)
        self.assertEqual(result.screening_queue, "suppressed")

    def test_mandatory_advanced_sql_role_is_rejected(self):
        candidate = job(description="Salesforce operations. Advanced SQL is required for this role.")
        result = evaluate(candidate, PROFILE)
        self.assertTrue(result.hard_excluded)
        self.assertEqual(result.screening_queue, "suppressed")

    def test_preferred_advanced_sql_is_not_treated_as_mandatory(self):
        candidate = job(description="Salesforce CRM data quality. Advanced SQL is preferred but not required.")
        self.assertIsNotNone(evaluate(candidate, PROFILE))

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
