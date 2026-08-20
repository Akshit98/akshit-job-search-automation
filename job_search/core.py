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
    published_at: str = ""
    location_tier: str = ""
    score: int = 0
    score_reasons: list[str] | None = None
    monthly_inr: int | None = None
    annual_inr: int | None = None
    minimum_experience_years: int | None = None
    is_new: bool = False
    active_status: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def location_tier(job: Job) -> str | None:
    advertised = f"{job.location} {job.workplace}".lower()
    description = job.description[:1600].lower()
    blob = f"{advertised} {description}"
    is_remote = any(term in advertised for term in REMOTE_TERMS) or any(term in description for term in REMOTE_TERMS)
    is_india = any(term in advertised for term in INDIA_TERMS)
    explicitly_foreign = any(term in advertised for term in (
        "united states", "usa", "u.s.", "north america", "noram", "emea",
        "europe", "uk", "united kingdom", "canada", "latin america", "latam",
        "australia", "apac"
    ))
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


def score_job(job: Job, profile: dict[str, Any]) -> tuple[int, list[str]]:
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
    if job.location_tier in ("remote_india", "global_work_from_anywhere"):
        score += 15
    elif job.location_tier == "hyderabad":
        score += 12
    elif job.location_tier == "bengaluru":
        score += 10
    else:
        score += 6

    preferred_years = int(profile.get("preferred_required_experience_years", 4))
    if job.minimum_experience_years is not None:
        if job.minimum_experience_years <= preferred_years:
            score += 5
        else:
            score -= 6

    if job.annual_inr is not None:
        floor = float(profile.get("salary_floor_lpa", {}).get(job.location_tier, 0)) * 100_000
        target = float(profile.get("salary_target_lpa", {}).get(job.location_tier, 0)) * 100_000
        if target and job.annual_inr >= target:
            score += 6
        elif floor and job.annual_inr >= floor:
            score += 3
        elif floor and job.annual_inr < floor:
            score -= 10

    score -= min(16, len(gap_hits) * 4)
    reasons = []
    if primary_hits:
        reasons.append("primary role: " + ", ".join(primary_hits[:3]))
    elif adjacent_hits:
        reasons.append("adjacent role: " + ", ".join(adjacent_hits[:3]))
    if strong_hits:
        reasons.append("proven skills: " + ", ".join(strong_hits[:6]))
    if supporting_hits:
        reasons.append("supporting skills: " + ", ".join(supporting_hits[:4]))
    if learning_hits:
        reasons.append("learning only: " + ", ".join(learning_hits[:3]))
    if gap_hits:
        reasons.append("experience gaps: " + ", ".join(gap_hits[:3]))
    if job.minimum_experience_years is not None:
        reasons.append(f"minimum experience: {job.minimum_experience_years} years")
    if job.annual_inr is not None:
        reasons.append(f"advertised lower-bound pay: INR {job.annual_inr:,}/year")
    reasons.append("location: " + (job.location_tier or "rejected"))
    return max(0, min(100, score)), reasons


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
    if any(term in title for term in profile.get("excluded_titles", [])):
        return None
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
        if job.monthly_inr is None or job.monthly_inr < int(profile["internship_min_monthly_inr"]):
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
    job.score, job.score_reasons = score_job(job, profile)
    if job.score < int(profile["minimum_fit_score"]):
        return None
    return job


def sort_jobs(jobs: list[Job], priority: list[str]) -> list[Job]:
    rank = {tier: index for index, tier in enumerate(priority)}
    return sorted(jobs, key=lambda j: (rank.get(j.location_tier, 99), -j.score, j.company.lower(), j.title.lower()))


def generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
