from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


INDIA_TERMS = (
    "india", "hyderabad", "bengaluru", "bangalore", "chennai", "pune",
    "mumbai", "delhi", "new delhi", "noida", "gurgaon", "gurugram",
    "kolkata", "ahmedabad", "kochi", "coimbatore", "jaipur", "chandigarh",
)
REMOTE_TERMS = ("remote", "work from home", "work-from-home", "distributed")
GLOBAL_TERMS = ("anywhere", "worldwide", "global remote", "work from anywhere", "any location")
INTERNSHIP_TERMS = ("intern", "internship", "trainee")


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_term(text: str, term: str) -> bool:
    """Match short acronyms as complete tokens and longer phrases literally."""
    if term.isalnum() and len(term) <= 4:
        return re.search(rf"\b{re.escape(term)}\b", text, re.I) is not None
    return term in text


@dataclass
class Job:
    id: str
    source: str
    company: str
    title: str
    location: str
    workplace: str
    description: str
    url: str
    employment_type: str = ""
    compensation: str = ""
    compensation_source: str = "employer_posted"
    published_at: str = ""
    location_tier: str = ""
    screening_score: int = 0
    screening_reasons: list[str] | None = None
    ats_similarity: int | None = None
    ats_similarity_status: str = "not_assessed"
    actual_hiring_fit: int | None = None
    actual_hiring_fit_status: str = "not_assessed"
    actual_hiring_fit_raw: int | None = None
    actual_hiring_fit_cap: int | None = None
    actual_hiring_fit_cap_reasons: list[str] | None = None
    fit_category_scores: dict[str, int] | None = None
    fit_assessment: dict[str, Any] | None = None
    requirement_coverage_confidence: float | None = None
    unclassified_material_requirements: list[str] | None = None
    mandatory_gaps: list[str] | None = None
    material_gaps: list[str] | None = None
    career_value: str = "not_assessed"
    compensation_assessment: str = "not_disclosed"
    monthly_inr: int | None = None
    annual_inr: int | None = None
    minimum_experience_years: int | None = None
    is_new: bool = False
    active_status: str = "unverified"
    verification_reason: str = "not_checked"
    verified_at: str = ""
    final_url: str = ""
    listing_age_days: int | None = None
    freshness: str = "unknown"
    screening_queue: str = "suppressed"
    hard_excluded: bool = False
    exclusion_signals: list[str] | None = None
    domain_compatibility: str = "unknown"
    domain_conflicts: list[str] | None = None
    seniority_assessment: str = "individual_contributor_or_unknown"
    evidence_quality: str = "insufficient"
    title_relevance: list[str] | None = None
    skill_overlap: list[str] | None = None
    canonical_employer: str = ""
    ats_board_id: str = ""
    source_type: str = "unknown"
    source_priority: int = 0
    original_source_url: str = ""
    canonical_url: str = ""
    description_provenance: str = "source_payload"
    description_retrieval_status: str = "not_attempted"
    description_retrieved_at: str = ""
    source_quality_confidence: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def location_tier(job: Job) -> str | None:
    advertised = f"{job.location} {job.workplace}".lower()
    description = job.description[:1600].lower()
    blob = f"{advertised} {description}"
    is_remote = any(term in advertised for term in REMOTE_TERMS) or any(term in description for term in REMOTE_TERMS)
    is_india = any(re.search(rf"\b{re.escape(term)}\b", advertised) for term in INDIA_TERMS)
    explicitly_foreign = any(term in advertised for term in (
        "united states", "usa", "u.s.", "north america", "noram", "emea",
        "europe", "uk", "united kingdom", "canada", "latin america", "latam",
        "australia", "apac"
    ))
    non_india_only = any(term in blob for term in (
        "latin america and the philippines", "latam and the philippines", "latam or the philippines",
        "philippines and latin america", "philippines or latin america", "philippines only",
        "hiring from latin america", "open only to candidates in latin america",
    )) and not re.search(r"\bindia\b", advertised)
    if non_india_only:
        return None
    if is_remote and is_india:
        return "remote_india"
    if explicitly_foreign and not any(term in advertised for term in GLOBAL_TERMS):
        return None
    if is_remote and any(term in blob for term in GLOBAL_TERMS):
        return "global_work_from_anywhere"
    if "hyderabad" in blob:
        return "hyderabad"
    if "bengaluru" in blob or "bangalore" in blob:
        return "bengaluru"
    if is_india:
        return "other_india"
    return None


def is_internship(job: Job) -> bool:
    blob = f"{job.title} {job.employment_type}".lower()
    return any(term in blob for term in INTERNSHIP_TERMS)


def monthly_compensation_inr(text: str) -> int | None:
    """Return a conservative monthly INR value from explicit advertised pay."""
    annual = annual_compensation_inr(text)
    return int(annual / 12) if annual is not None else None


def annual_compensation_inr(text: str) -> int | None:
    """Return the lower-bound annual INR amount when pay is explicitly stated."""
    value = clean_text(text).lower().replace(",", "").replace("p.a.", "pa").replace("p.a", "pa")
    currency = r"(?:inr|₹|rs\.?|rupees?)"
    unit = r"(k|lakh|lakhs|lac|lacs)?"
    period = r"(month|monthly|pm|year|annual|annum|pa|p\.a\.|lpa)"
    patterns = [
        rf"{currency}\s*(\d+(?:\.\d+)?)\s*[-–]\s*\d+(?:\.\d+)?\s*(k|lakh|lakhs|lac|lacs)\s*(?:/|per|a)?\s*{period}\b",
        rf"{currency}\s*(\d+(?:\.\d+)?)\s*{unit}(?:\s*[-–]\s*{currency}?\s*\d+(?:\.\d+)?\s*(?:k|lakh|lakhs|lac|lacs)?)?\s*(?:/|per|a)?\s*{period}\b",
        rf"(\d+(?:\.\d+)?)\s*{unit}(?:\s*[-–]\s*\d+(?:\.\d+)?\s*(?:k|lakh|lakhs|lac|lacs)?)?\s*{currency}\s*(?:/|per|a)?\s*{period}\b",
        rf"(\d+(?:\.\d+)?)\s*(lakh|lakhs|lac|lacs)(?:\s*[-–]\s*\d+(?:\.\d+)?\s*(?:lakh|lakhs|lac|lacs)?)?\s*(?:/|per|a)?\s*{period}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if not match:
            continue
        amount = float(match.group(1))
        amount_unit = (match.group(2) or "").lower()
        pay_period = match.group(3).lower()
        if amount_unit == "k":
            amount *= 1_000
        elif amount_unit in ("lakh", "lakhs", "lac", "lacs"):
            amount *= 100_000
        if pay_period in ("month", "monthly", "pm"):
            amount *= 12
        return int(amount)
    return None


def minimum_required_experience_years(text: str) -> int | None:
    """Extract the advertised minimum years without treating all numbers as experience."""
    value = clean_text(text).lower()
    patterns = (
        r"(\d+)\s*(?:-|–|to)\s*\d+\s+years?(?:\s+of)?\s+experience",
        r"(\d+)\s*\+\s*years?(?:\s+of)?\s+experience",
        r"(?:minimum|min\.?|at least)\s+(?:of\s+)?(\d+)\s+years?",
        r"(\d+)\s+years?(?:\s+of)?\s+(?:relevant\s+|professional\s+|work\s+)?experience",
    )
    matches = [int(match.group(1)) for pattern in patterns if (match := re.search(pattern, value, re.I))]
    return min(matches) if matches else None


def screening_score(job: Job, profile: dict[str, Any]) -> tuple[int, list[str]]:
    title = job.title.lower()
    blob = f"{job.title} {job.description}".lower()
    primary_terms = profile.get("primary_role_terms", profile.get("role_terms", []))
    adjacent_terms = profile.get("adjacent_role_terms", [])
    strong_skills = profile.get("strong_skills", profile.get("skills", []))
    supporting_skills = profile.get("supporting_skills", [])
    learning_skills = profile.get("learning_skills", [])
    primary_hits = [term for term in primary_terms if contains_term(blob, term)]
    adjacent_hits = [term for term in adjacent_terms if contains_term(blob, term)]
    strong_hits = [term for term in strong_skills if contains_term(blob, term)]
    supporting_hits = [term for term in supporting_skills if contains_term(blob, term)]
    learning_hits = [term for term in learning_skills if contains_term(blob, term)]
    gap_hits = [
        term for term in profile.get("experience_gap_terms", [])
        if contains_term(blob, term)
    ]
    title_primary_hits = [term for term in primary_terms if contains_term(title, term)]
    title_adjacent_hits = [term for term in adjacent_terms if contains_term(title, term)]
    job.title_relevance = title_primary_hits or title_adjacent_hits
    job.skill_overlap = strong_hits + supporting_hits

    if title_primary_hits:
        role_score = 34 + min(8, (len(primary_hits) - 1) * 4)
    elif title_adjacent_hits:
        role_score = 25 + min(10, len(primary_hits) * 5)
    else:
        role_score = min(28, len(primary_hits) * 10 + len(adjacent_hits) * 5)

    score = role_score
    score += min(28, len(strong_hits) * 5)
    score += min(10, len(supporting_hits) * 2)
    score += min(4, len(learning_hits))
    if any(term in title for term in profile.get("excluded_titles", [])):
        score -= 30
    seniority_penalties = {"senior": 8, "lead": 12, "manager": 18, "director_or_head": 25}
    score -= seniority_penalties.get(job.seniority_assessment, 0)
    if job.domain_conflicts:
        score -= 15
    preferred_years = int(profile.get("preferred_required_experience_years", 4))
    if job.minimum_experience_years is not None:
        if job.minimum_experience_years <= preferred_years:
            score += 5
        else:
            score -= 6

    if job.annual_inr is None:
        job.compensation_assessment = "not_disclosed"
    elif job.compensation_source == "aggregator_estimate":
        job.compensation_assessment = "estimate_only"
    else:
        target = float(profile.get("full_time_salary_target_lpa", 10)) * 100_000
        if job.annual_inr >= target:
            job.compensation_assessment = "meets_target"
            score += 6
        else:
            job.compensation_assessment = "below_target"
            score -= 8

    score -= min(16, len(gap_hits) * 4)
    reasons = []
    if title_primary_hits:
        reasons.append("title/function relevance: primary — " + ", ".join(title_primary_hits[:3]))
    elif title_adjacent_hits:
        reasons.append("title/function relevance: adjacent — " + ", ".join(title_adjacent_hits[:3]))
    elif primary_hits:
        reasons.append("description relevance: primary — " + ", ".join(primary_hits[:3]))
    elif adjacent_hits:
        reasons.append("description relevance: adjacent — " + ", ".join(adjacent_hits[:3]))
    if strong_hits:
        reasons.append("verified candidate-skill overlap: " + ", ".join(strong_hits[:6]))
    if supporting_hits:
        reasons.append("supporting candidate-skill overlap: " + ", ".join(supporting_hits[:4]))
    if learning_hits:
        reasons.append("learning only: " + ", ".join(learning_hits[:3]))
    if gap_hits:
        reasons.append("experience gaps: " + ", ".join(gap_hits[:3]))
    reasons.append("domain compatibility: " + ("conflict — " + ", ".join(job.domain_conflicts) if job.domain_conflicts else "compatible or unknown"))
    reasons.append("seniority: " + job.seniority_assessment)
    reasons.append("evidence quality: " + job.evidence_quality)
    if job.exclusion_signals:
        reasons.append("exclusion signals: " + ", ".join(job.exclusion_signals))
    if job.minimum_experience_years is not None:
        reasons.append(f"minimum experience: {job.minimum_experience_years} years")
    if job.compensation_assessment == "meets_target":
        reasons.append(f"employer-posted pay meets INR {float(profile.get('full_time_salary_target_lpa', 10)):.1f} LPA target")
    elif job.compensation_assessment == "below_target":
        reasons.append(f"employer-posted pay is below INR {float(profile.get('full_time_salary_target_lpa', 10)):.1f} LPA target")
    elif job.compensation_assessment == "estimate_only":
        reasons.append("aggregator pay estimate excluded from screening score")
    else:
        reasons.append("pay not disclosed; no score penalty")
    reasons.append("eligible location: " + (job.location_tier or "rejected"))
    return max(0, min(100, score)), reasons


def term_is_mandatory(text: str, term: str) -> bool:
    """Conservatively identify a mandatory term without treating preferences as requirements."""
    value = clean_text(text).lower()
    for match in re.finditer(re.escape(term.lower()), value):
        context = value[max(0, match.start() - 90):match.end() + 90]
        if re.search(r"(?:preferred|nice to have|bonus|optional|not required).{0,50}" + re.escape(term.lower()), context):
            continue
        if re.search(re.escape(term.lower()) + r".{0,50}(?:preferred|nice to have|bonus|optional|not required)", context):
            continue
        if re.search(r"\b(?:required|must|mandatory|need(?:ed|s)?|minimum)\b", context):
            return True
    return False


def evidence_quality(job: Job) -> str:
    text = clean_text(job.description).lower()
    if not text or len(text) < 120:
        return "insufficient"
    aggregator_boilerplate = (
        "how jobgether works",
        "our system identifies the top-fitting candidates",
        "shortlist is then shared directly with the hiring company",
    )
    if any(marker in text for marker in aggregator_boilerplate):
        return "insufficient"
    responsibility_markers = (
        "responsibilities", "you will", "your role", "what you'll do", "what you will do",
        "responsible for", "duties", "day-to-day",
    )
    requirement_markers = (
        "requirements", "qualifications", "experience", "required", "you have", "must have",
    )
    has_responsibilities = any(marker in text for marker in responsibility_markers)
    has_requirements = any(marker in text for marker in requirement_markers)
    if len(text) >= 500 and has_responsibilities and has_requirements:
        return "sufficient"
    return "partial"


def outbound_responsibility_signals(job: Job) -> list[str]:
    title = job.title.lower()
    text = clean_text(job.description).lower()
    title_terms = (
        "sales development representative", "business development representative", "sdr", "bdr",
        "outbound sales", "appointment setter", "business development executive",
    )
    if any(contains_term(title, term) for term in title_terms):
        return ["outbound_sales"]
    outbound_terms = (
        "cold calling", "cold calls", "cold email", "outbound prospecting", "outbound lead generation",
        "meeting setting", "setting meetings", "appointment setting", "quota-carrying",
        "sales quota", "prospect conversion", "convert prospects", "recruiter outreach",
        "candidate sourcing", "contacting candidates", "direct outreach",
        "cold email campaign", "cold-email campaign", "outbound sequence", "outbound campaign execution",
        "prospect reply management", "managing prospect replies", "monitoring deliverability",
        "email deliverability", "inbox provisioning",
    )
    hits = {term for term in outbound_terms if term in text}
    responsibility_cues = (
        "responsibilities", "you will", "your role", "responsible for", "duties", "focus on",
        "role involves", "work includes", "conduct", "perform", "execute", "direct outreach",
    )
    if len(hits) >= 2 or (hits and any(cue in text for cue in responsibility_cues)):
        return ["outbound_sales"]
    return []


def domain_conflict_signals(job: Job) -> list[str]:
    title = job.title.lower()
    text = clean_text(job.description).lower()
    conflicts = []
    title_patterns = {
        "cybersecurity": ("security operations", "cyber defense", "cybersecurity", "soc analyst"),
        "crypto_digital_assets": ("digital assets", "crypto operations", "cryptocurrency", "blockchain operations"),
        "finance_accounting": (
            "accounts receivable", "accounts payable", "bill to pay", "billing operations",
            "payment operations", "finance operations", "accounting operations", "margin recovery",
        ),
        "people_hr": ("people operations", "human resources", "hr operations", "talent operations"),
    }
    evidence_terms = {
        "cybersecurity": ("cyber threat", "security incident", "incident response", "security monitoring"),
        "crypto_digital_assets": ("cryptocurrency", "digital assets", "crypto market", "blockchain"),
        "finance_accounting": ("billing", "payments", "reconciliation", "collections", "accounts receivable", "bookkeeping"),
        "people_hr": ("employee programs", "hr policies", "human resources", "people programs", "employee lifecycle"),
    }
    for name, patterns in title_patterns.items():
        title_hits = [pattern for pattern in patterns if pattern in title]
        evidence_hits = sum(term in text for term in evidence_terms[name])
        if title_hits:
            conflicts.append(name)
        elif evidence_hits >= 2:
            conflicts.append(name)
    return conflicts


def seniority_signals(job: Job) -> tuple[str, list[str]]:
    title = job.title.lower()
    text = clean_text(job.description).lower()
    level = "individual_contributor_or_unknown"
    if re.search(r"\b(?:director|head|vice president|vp)\b", title):
        level = "director_or_head"
    elif re.search(r"\bmanager\b", title):
        level = "manager"
    elif re.search(r"\blead\b", title):
        level = "lead"
    elif re.search(r"\b(?:senior|sr\.?)(?:\s|$)", title):
        level = "senior"
    leadership_terms = (
        "direct reports", "people management", "manage a team", "manage the team", "lead a team",
        "hiring responsibility", "performance management", "team of ", "build and lead",
    )
    exclusions = ["unsupported_people_leadership"] if any(term in text for term in leadership_terms) else []
    if level == "director_or_head":
        exclusions.append("unsupported_executive_seniority")
    return level, exclusions


def assign_screening_queue(job: Job, profile: dict[str, Any] | None = None) -> str:
    thresholds = (profile or {}).get("screening_queue_thresholds", {})
    strong_threshold = int(thresholds.get("strong_shortlist", 45))
    review_threshold = int(thresholds.get("review_queue", 30))
    if job.hard_excluded or job.exclusion_signals:
        job.screening_queue = "suppressed"
    elif job.screening_score < review_threshold:
        job.screening_queue = "suppressed"
    elif job.screening_score >= strong_threshold and job.active_status == "active" and job.evidence_quality != "insufficient":
        job.screening_queue = "strong_shortlist"
    else:
        job.screening_queue = "review_queue"
    dynamic_prefixes = ("freshness:", "verification:", "screening queue:")
    job.screening_reasons = [
        reason for reason in (job.screening_reasons or [])
        if not reason.startswith(dynamic_prefixes)
    ]
    job.screening_reasons.extend([
        f"freshness: {job.freshness}",
        f"verification: {job.active_status}",
        f"screening queue: {job.screening_queue}",
    ])
    return job.screening_queue


def job_fingerprint(job: Job) -> str:
    """Cross-source deduplication key for the same advertised opening."""
    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    company = normalize(job.company)
    company = re.sub(
        r"\b(?:private limited|pvt ltd|limited|ltd|incorporated|inc|corporation|corp|llc|plc)\b",
        " ",
        company,
    )
    company = re.sub(r"\s+", " ", company).strip()

    title = f" {normalize(job.title)} "
    title_aliases = {
        " sr ": " senior ",
        " jr ": " junior ",
        " ops ": " operations ",
        " revops ": " revenue operations ",
        " assoc ": " associate ",
        " coord ": " coordinator ",
    }
    for alias, canonical in title_aliases.items():
        title = title.replace(alias, canonical)
    title = re.sub(r"\s+", " ", title).strip()

    # Boards frequently advertise the same remote opening as "Worldwide",
    # "Remote", or a city. The evaluated tier is a safer cross-source key.
    location = job.location_tier or normalize(job.location).replace("bangalore", "bengaluru")
    return "|".join((company, title, location))


def evaluate(job: Job, profile: dict[str, Any]) -> Job | None:
    job.location_tier = location_tier(job) or ""
    if not job.location_tier:
        return None
    internship = is_internship(job)
    title = job.title.lower()
    full_text = f"{job.title} {job.description}"
    job.evidence_quality = evidence_quality(job)
    job.exclusion_signals = []
    job.domain_conflicts = domain_conflict_signals(job)
    job.domain_compatibility = "conflict" if job.domain_conflicts else "compatible_or_unknown"
    job.seniority_assessment, leadership_exclusions = seniority_signals(job)
    job.exclusion_signals.extend(leadership_exclusions)
    if any(term in title for term in profile.get("excluded_titles", [])):
        job.exclusion_signals.append("excluded_title")
    job.exclusion_signals.extend(outbound_responsibility_signals(job))
    for term in profile.get("screening_exclusion_terms", []):
        if contains_term(title, term) or term_is_mandatory(full_text, term):
            job.exclusion_signals.append("excluded_responsibility")
    for term in profile.get("mandatory_advanced_skill_terms", []):
        if term_is_mandatory(full_text, term):
            job.exclusion_signals.append("mandatory_unsupported_capability")
    hard_domain_conflicts = {"cybersecurity", "crypto_digital_assets", "finance_accounting"}
    if hard_domain_conflicts.intersection(job.domain_conflicts):
        job.exclusion_signals.append("outside_target_domain")
    job.exclusion_signals = sorted(set(job.exclusion_signals))
    job.hard_excluded = bool(job.exclusion_signals)
    employment_type = f"{job.employment_type} {job.title}".lower()
    description_start = job.description[:700].lower()
    excluded_employment = profile.get(
        "excluded_employment_terms",
        ("part-time", "part time", "freelance", "temporary", "fixed-term", "fixed term"),
    )
    job.minimum_experience_years = minimum_required_experience_years(
        f"{job.title} {job.description[:3000]}"
    )
    maximum_years = int(profile.get("maximum_required_experience_years", 5))
    if job.minimum_experience_years is not None and job.minimum_experience_years > maximum_years:
        return None
    job.annual_inr = annual_compensation_inr(f"{job.compensation} {job.description[:1200]}")
    if internship:
        job.monthly_inr = monthly_compensation_inr(f"{job.compensation} {job.description}")
        legacy_threshold = int(profile.get("internship_min_monthly_inr", 40000))
        remote_threshold = int(profile.get("internship_min_monthly_inr_remote", legacy_threshold))
        onsite_threshold = int(profile.get("internship_min_monthly_inr_onsite", legacy_threshold))
        is_remote = job.location_tier in ("remote_india", "global_work_from_anywhere")
        if job.monthly_inr is None:
            return None
        if is_remote and job.monthly_inr < remote_threshold:
            return None
        if not is_remote and job.monthly_inr <= onsite_threshold:
            return None
    else:
        if any(term in employment_type for term in excluded_employment):
            return None
        contract_markers = (
            "employment type: contract",
            "job type: contract",
            "contract position",
            "contract role",
            "independent contractor",
        )
        if "contract" in employment_type or any(term in description_start for term in contract_markers):
            return None
    job.screening_score, job.screening_reasons = screening_score(job, profile)
    assign_screening_queue(job, profile)
    return job


def sort_jobs(jobs: list[Job], priority: list[str]) -> list[Job]:
    rank = {tier: index for index, tier in enumerate(priority)}
    freshness_rank = {"fresh": 0, "recent": 1, "older": 2, "unknown": 3}
    return sorted(
        jobs,
        key=lambda j: (
            -j.screening_score,
            freshness_rank.get(j.freshness, 3),
            rank.get(j.location_tier, 99),
            j.company.lower(),
            j.title.lower(),
        ),
    )


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
