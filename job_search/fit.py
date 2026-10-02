from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .core import Job, clean_text, minimum_required_experience_years
from .evidence import evidence_items


CATEGORY_MAXIMUMS = {
    "direct_responsibilities": 35,
    "tools_domain": 25,
    "years_seniority_leadership": 15,
    "transferable_evidence": 15,
    "education_certifications": 5,
    "practical_requirements": 5,
}

SCOPE_WEIGHT = {"mandatory": 3.0, "responsibility": 2.0, "preferred": 1.0}

# Requirements that must remain visible even though the candidate has no direct
# evidence for them. transfer_ids identify genuinely adjacent evidence only.
EXTERNAL_REQUIREMENTS = (
    ("survey_sampling", "survey sampling", "responsibility", ("survey sampling", "sample management", "sampling operations"), ()),
    ("participant_recruitment", "participant recruitment", "responsibility", ("participant recruitment", "respondent recruitment", "study recruitment"), ()),
    ("research_fieldwork", "research fieldwork", "responsibility", ("research fieldwork", "fieldwork operations", "manage fieldwork"), ()),
    ("study_programming", "study/project programming", "responsibility", ("study programming", "survey programming", "project programming"), ()),
    ("link_testing", "link testing", "responsibility", ("link testing", "survey link testing"), ("data_validation",)),
    ("project_cost_ownership", "project-cost ownership", "responsibility", ("project cost ownership", "project-cost ownership", "monitor project costs", "cost monitoring"), ()),
    ("recruitment_kpi_ownership", "recruitment-rate/KPI ownership", "responsibility", ("recruitment-rate kpi", "recruitment rate", "recruitment kpi", "monitor kpis"), ("operational_reporting",)),
    ("invoicing_reconciliation", "invoicing reconciliation", "responsibility", ("invoicing reconciliation", "invoice reconciliation"), ()),
    ("campaign_execution", "outbound campaign execution", "responsibility", ("campaign execution", "execute campaigns", "running campaigns"), ("sales_support_research",)),
    ("cold_email_sequencing", "cold-email sequencing", "responsibility", ("cold email campaign", "cold-email campaign", "outbound sequences", "sequence build"), ()),
    ("reply_management", "prospect reply management", "responsibility", ("reply management", "manage prospect replies", "responding to prospect replies"), ("sales_support_research",)),
    ("deliverability", "email deliverability", "responsibility", ("deliverability", "delivery rates"), ()),
    ("email_infrastructure", "email-domain/inbox infrastructure", "tool", ("email infrastructure", "domain purchasing", "inbox provisioning"), ()),
    ("outbound_platform", "outbound campaign platform", "tool", ("outbound platform", "instantly", "email bison", "lemlist", "smartlead"), ()),
    ("api_mcp_integrations", "API/MCP integrations", "tool", ("api integration", "connecting systems via apis", "mcp integration", "model context protocol"), ()),
    ("carrier_operations", "carrier/telecom operations", "domain", ("carrier operations", "carrier partner", "telecommunications experience", "telecom operations"), ()),
    ("phone_number_porting", "phone-number porting", "responsibility", ("phone number porting", "phone-number porting", "port phone numbers", "porting projects"), ()),
    ("incident_triage", "incident triage", "responsibility", ("incident triage", "triage complex issues", "troubleshoot incidents"), ("stakeholder_support",)),
    ("sla_operations", "SLA operations", "responsibility", ("sla operations", "within slas", "service level agreement"), ()),
    ("telecom_escalations", "telecom escalations", "responsibility", ("telecom escalations", "carrier escalations", "escalate incidents"), ("stakeholder_support",)),
    ("customer_service", "customer service", "responsibility", ("customer service", "customer inquiries", "customer support"), ("stakeholder_support",)),
    ("project_management", "project coordination/management", "responsibility", ("manage several projects", "multiple concurrent projects", "project coordination", "project management"), ("stakeholder_support", "documentation")),
    ("fintech_domain", "fintech domain", "domain", ("fintech experience", "fintech domain", "fintech domain experience"), ()),
    ("financial_services_domain", "financial services domain", "domain", ("financial-services experience", "financial services experience", "financial services domain", "financial-services domain"), ()),
    ("cybersecurity_domain", "cybersecurity domain", "domain", ("cybersecurity experience", "security operations"), ()),
)


@dataclass
class Requirement:
    id: str
    label: str
    category: str
    scope: str
    centrality: float
    source_text: str
    evidence_ids: list[str] = field(default_factory=list)
    match_type: str = "none"

    def weight(self) -> float:
        return self.centrality * SCOPE_WEIGHT[self.scope]


@dataclass
class FitAssessment:
    actual_hiring_fit: int | None
    actual_hiring_fit_status: str
    category_scores: dict[str, int] = field(default_factory=dict)
    category_evidence: dict[str, list[str]] = field(default_factory=dict)
    mandatory_requirements: list[str] = field(default_factory=list)
    preferred_requirements: list[str] = field(default_factory=list)
    matched_mandatory_requirements: list[str] = field(default_factory=list)
    missing_mandatory_requirements: list[str] = field(default_factory=list)
    transferable_evidence: list[str] = field(default_factory=list)
    material_gaps: list[str] = field(default_factory=list)
    unknown_material_facts: list[str] = field(default_factory=list)
    raw_total: int | None = None
    applicable_cap: int | None = None
    cap_reasons: list[str] = field(default_factory=list)
    ats_similarity: int | None = None
    used_evidence_ids: list[str] = field(default_factory=list)
    requirement_coverage_confidence: float = 0.0
    unclassified_material_requirements: list[str] = field(default_factory=list)
    structured_requirements: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    text = clean_text(text)
    return [part.strip(" -•") for part in re.split(r"(?<=[.!?;])\s+|\n+", text) if part.strip()]


def _scope(sentence: str) -> str:
    value = sentence.lower()
    if any(marker in value for marker in ("preferred", "desired", "nice to have", "bonus", "optional", "not required")):
        return "preferred"
    if any(marker in value for marker in ("required", "requires", "must", "mandatory", "must-have", "minimum", "need to", "needs to")):
        return "mandatory"
    return "responsibility"


def _centrality(sentence: str, scope: str) -> float:
    value = sentence.lower()
    if scope == "preferred":
        return 0.6
    if any(marker in value for marker in ("central", "core", "day-to-day", "primary responsibility", "must", "required")):
        return 1.0
    return 0.75


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text.lower()) is not None


def _evidence_match(item: dict[str, Any]) -> str:
    if not item.get("actual_hiring_fit", True) or not item.get("ats_similarity", True):
        return "none"
    evidence_type = item.get("evidence_type", "direct")
    return "direct" if evidence_type == "direct" else "transferable" if evidence_type == "transferable" else "none"


def _vocabulary(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    vocabulary: list[dict[str, Any]] = []
    section_category = {
        "professional_responsibilities": "responsibility",
        "tools": "tool",
        "domains": "domain",
        "learning_only": "tool",
        "unsupported_ownership": "responsibility",
    }
    category_overrides = {
        "advanced_excel": "tool", "advanced_crm_ownership": "tool", "netsuite_administration": "tool",
        "revenue_operations_ownership": "domain",
    }
    for section, category in section_category.items():
        for item in evidence_items(evidence, section):
            vocabulary.append({
                "id": str(item["id"]), "label": str(item.get("capability") or item.get("terms", [item["id"]])[0]).lower(),
                "category": category_overrides.get(str(item["id"]), category), "terms": tuple(str(term).lower() for term in item.get("terms", [])),
                "evidence_ids": [str(item["id"])] if _evidence_match(item) != "none" else [],
                "match_type": _evidence_match(item),
            })
    for item_id, label, category, terms, transfer_ids in EXTERNAL_REQUIREMENTS:
        vocabulary.append({"id": item_id, "label": label, "category": category, "terms": terms,
                           "evidence_ids": list(transfer_ids), "match_type": "transferable" if transfer_ids else "none"})
    return vocabulary


def _material_clauses(sentence: str) -> list[str]:
    value = sentence.strip()
    if not any(marker in value.lower() for marker in (
        "responsibilities", "you will", "responsible for", "required", "requires", "must", "qualifications",
        "experience with", "experience in", "proficiency", "knowledge of", "desired", "preferred", "central",
    )):
        return []
    return [part.strip(" :-") for part in re.split(r",|;|\band\b", value, flags=re.I) if len(part.strip().split()) >= 2]


def extract_requirements(job: Job, evidence: dict[str, Any]) -> tuple[list[Requirement], list[str], float]:
    vocabulary = _vocabulary(evidence)
    requirements: dict[str, Requirement] = {}
    unclassified: list[str] = []
    classified_clauses = 0
    material_clauses = 0
    for sentence in _sentences(f"{job.title}. {job.description}"):
        scope = _scope(sentence)
        centrality = _centrality(sentence, scope)
        detected_ids: set[str] = set()
        for entry in vocabulary:
            if entry["terms"] and any(_contains(sentence, term) for term in entry["terms"]):
                detected_ids.add(entry["id"])
                existing = requirements.get(entry["id"])
                candidate = Requirement(entry["id"], entry["label"], entry["category"], scope, centrality,
                                        sentence, list(entry["evidence_ids"]), entry["match_type"])
                if existing is None or candidate.weight() > existing.weight():
                    requirements[entry["id"]] = candidate
        clauses = _material_clauses(sentence)
        for clause in clauses:
            material_clauses += 1
            structural = re.search(
                r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+\s*)?years?\b|\bbachelor(?:'s)?\b|\bdegree\b|\beligib(?:le|ility)\b|\bremote(?:ly)?\b|\bindia\b",
                clause, re.I,
            )
            if structural or any(any(_contains(clause, term) for term in entry["terms"]) for entry in vocabulary if entry["terms"]):
                classified_clauses += 1
            else:
                normalized = re.sub(
                    r"^(?:responsibilities include|you will|responsible for|required|requires|must have|must|desired|preferred|qualifications include)\s*",
                    "", clause, flags=re.I,
                ).strip(" .:")
                if normalized and normalized.lower() not in {"required tools are", "the role"}:
                    label = normalized.lower()
                    unknown_id = "unclassified:" + re.sub(r"[^a-z0-9]+", "_", label)[:80].strip("_")
                    if unknown_id not in requirements:
                        requirements[unknown_id] = Requirement(unknown_id, label, "responsibility", scope, centrality, clause)
                        unclassified.append(label)
    classified_weight = sum(r.weight() for r in requirements.values() if not r.id.startswith("unclassified:"))
    total_weight = sum(r.weight() for r in requirements.values())
    clause_confidence = classified_clauses / material_clauses if material_clauses else (1.0 if requirements else 0.0)
    weight_confidence = classified_weight / total_weight if total_weight else 0.0
    confidence = round(min(clause_confidence, weight_confidence), 3)
    return list(requirements.values()), unclassified, confidence


def _requirement_match(requirement: Requirement, evidence: dict[str, Any]) -> tuple[str, list[str]]:
    if requirement.match_type == "direct":
        return "direct", requirement.evidence_ids
    if requirement.match_type == "transferable":
        return "transferable", requirement.evidence_ids
    return "none", []


def _ats_from_requirements(requirements: list[Requirement], evidence: dict[str, Any], job: Job) -> int:
    weighted_total = 0.0
    weighted_match = 0.0
    for requirement in requirements:
        weight = requirement.weight()
        weighted_total += weight
        match_type, _ = _requirement_match(requirement, evidence)
        weighted_match += weight * (1.0 if match_type == "direct" else 0.55 if match_type == "transferable" else 0.0)
    minimum_years = minimum_required_experience_years(job.description)
    if minimum_years is not None:
        weight = 3.0
        weighted_total += weight
        candidate_years = float(evidence.get("overall_experience_years_date_calculated", 0))
        weighted_match += weight * min(1.0, candidate_years / minimum_years)
    return round(100 * weighted_match / weighted_total) if weighted_total else 0


def calculate_ats_similarity(job: Job, evidence: dict[str, Any]) -> int:
    requirements, _, _ = extract_requirements(job, evidence)
    score = _ats_from_requirements(requirements, evidence, job)
    return score if job.evidence_quality == "sufficient" else round(score * 0.6)


def _weighted_score(requirements: list[Requirement], match_kind: str, maximum: int, evidence: dict[str, Any]) -> int:
    denominator = sum(r.weight() for r in requirements)
    if not denominator:
        return 0
    numerator = 0.0
    for requirement in requirements:
        kind, _ = _requirement_match(requirement, evidence)
        if kind == match_kind:
            numerator += requirement.weight()
    return round(maximum * numerator / denominator)


def assess_job_fit(job: Job, evidence: dict[str, Any]) -> FitAssessment:
    requirements, unclassified, coverage = extract_requirements(job, evidence)
    ats_similarity = _ats_from_requirements(requirements, evidence, job)
    if job.evidence_quality != "sufficient":
        ats_similarity = round(ats_similarity * 0.6)
    nonpreferred = [r for r in requirements if r.scope != "preferred"]
    preferred = [r for r in requirements if r.scope == "preferred"]
    matches = {r.id: _requirement_match(r, evidence) for r in requirements}
    mandatory_labels = [r.label for r in nonpreferred]
    preferred_labels = [r.label for r in preferred]
    missing = [r.label for r in nonpreferred if matches[r.id][0] == "none"]
    matched = [r.label for r in nonpreferred if matches[r.id][0] == "direct"]
    transferable = [r.label for r in requirements if matches[r.id][0] == "transferable"]
    used_ids = list(dict.fromkeys(eid for r in requirements for eid in matches[r.id][1]))

    minimum_years = minimum_required_experience_years(job.description)
    candidate_years = float(evidence.get("overall_experience_years_date_calculated", 0))
    leadership_required = any(term in clean_text(f"{job.title} {job.description}").lower() for term in (
        "direct reports", "people management", "manage a team", "lead a team", "performance management",
        "hiring responsibility", "department strategy",
    ))

    responsibilities = [r for r in nonpreferred if r.category == "responsibility"]
    tools_domains = [r for r in nonpreferred if r.category in ("tool", "domain")]
    direct_score = _weighted_score(responsibilities, "direct", 35, evidence)
    tools_domain_score = _weighted_score(tools_domains, "direct", 25, evidence)
    transferable_pool = responsibilities + tools_domains
    transferable_score = _weighted_score(transferable_pool, "transferable", 15, evidence)

    if minimum_years is None:
        years_score = 10
    elif candidate_years >= minimum_years:
        years_score = 15
    elif minimum_years - candidate_years <= 2:
        years_score = 9
    else:
        years_score = 3
    if leadership_required and not evidence.get("leadership", {}).get("people_management"):
        years_score = min(years_score, 5)

    text = clean_text(job.description).lower()
    education_required = any(term in text for term in ("bachelor", "degree required", "undergraduate degree"))
    education_score = 5 if education_required and evidence.get("education") else 3 if not education_required else 0
    eligible = "india" in f"{job.location} {job.description}".lower() or job.location_tier in (
        "remote_india", "global_work_from_anywhere", "hyderabad", "bengaluru", "other_india",
    )
    practical_score = 5 if eligible else 0
    category_scores = {
        "direct_responsibilities": direct_score,
        "tools_domain": tools_domain_score,
        "years_seniority_leadership": years_score,
        "transferable_evidence": transferable_score,
        "education_certifications": education_score,
        "practical_requirements": practical_score,
    }
    raw_total = sum(category_scores.values())

    central_missing = [r for r in nonpreferred if r.centrality >= 0.75 and r.category == "responsibility" and matches[r.id][0] != "direct"]
    missing_tool = any(r.category == "tool" and matches[r.id][0] == "none" for r in nonpreferred)
    missing_domain = any(r.category == "domain" and matches[r.id][0] != "direct" for r in nonpreferred)
    caps: list[int] = []
    cap_reasons: list[str] = []
    if len(central_missing) >= 2:
        caps.append(59); cap_reasons.append("two_or_more_missing_central_capabilities")
    if missing_domain and missing_tool:
        caps.append(49); cap_reasons.append("missing_required_domain_and_core_system")
    if minimum_years is not None and minimum_years - candidate_years > 2:
        caps.append(59); cap_reasons.append("experience_more_than_two_years_below_minimum")
    if leadership_required and not evidence.get("leadership", {}).get("people_management"):
        caps.append(59); cap_reasons.append("unsupported_substantial_leadership")
    applicable_cap = min(caps) if caps else None
    final_score = min(raw_total, applicable_cap) if applicable_cap is not None else raw_total

    insufficient = job.evidence_quality != "sufficient" or len(clean_text(job.description)) < 80 or coverage < 0.65 or not requirements
    status = "insufficient_evidence" if insufficient else "assessed"
    result = FitAssessment(
        actual_hiring_fit=None if insufficient else final_score,
        actual_hiring_fit_status=status,
        category_scores=category_scores,
        category_evidence={
            "direct_responsibilities": [r.label for r in responsibilities if matches[r.id][0] == "direct"],
            "tools_domain": [r.label for r in tools_domains if matches[r.id][0] == "direct"],
            "years_seniority_leadership": [f"candidate experience: {candidate_years:.2f} years"],
            "transferable_evidence": transferable,
            "education_certifications": [str(item.get("capability", item.get("id"))) for item in evidence.get("education", [])],
            "practical_requirements": ["India/global-India eligibility" if eligible else "eligibility unsupported"],
        },
        mandatory_requirements=mandatory_labels,
        preferred_requirements=preferred_labels,
        matched_mandatory_requirements=matched,
        missing_mandatory_requirements=missing,
        transferable_evidence=transferable,
        material_gaps=missing + (["unsupported substantial people leadership"] if leadership_required and not evidence.get("leadership", {}).get("people_management") else []),
        unknown_material_facts=unclassified + (["Compensation not disclosed"] if not job.compensation else []),
        raw_total=raw_total,
        applicable_cap=applicable_cap,
        cap_reasons=cap_reasons,
        ats_similarity=ats_similarity,
        used_evidence_ids=used_ids,
        requirement_coverage_confidence=coverage,
        unclassified_material_requirements=unclassified,
        structured_requirements=[asdict(r) for r in requirements],
    )
    _apply_to_job(job, result)
    return result


def _apply_to_job(job: Job, assessment: FitAssessment) -> None:
    job.actual_hiring_fit = assessment.actual_hiring_fit
    job.actual_hiring_fit_status = assessment.actual_hiring_fit_status
    job.ats_similarity = assessment.ats_similarity
    job.ats_similarity_status = "assessed" if assessment.ats_similarity is not None else "not_assessed"
    job.mandatory_gaps = assessment.missing_mandatory_requirements
    job.material_gaps = assessment.material_gaps
    job.actual_hiring_fit_raw = assessment.raw_total
    job.actual_hiring_fit_cap = assessment.applicable_cap
    job.actual_hiring_fit_cap_reasons = assessment.cap_reasons
    job.fit_category_scores = assessment.category_scores
    job.requirement_coverage_confidence = assessment.requirement_coverage_confidence
    job.unclassified_material_requirements = assessment.unclassified_material_requirements
    job.fit_assessment = assessment.to_dict()
