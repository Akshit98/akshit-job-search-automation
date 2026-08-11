from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


INDIA_TERMS = ("india", "hyderabad", "bengaluru", "bangalore", "chennai", "pune", "mumbai", "delhi", "noida", "gurgaon", "gurugram")
REMOTE_TERMS = ("remote", "work from home", "work-from-home", "distributed")
GLOBAL_TERMS = ("anywhere", "worldwide", "global remote", "work from anywhere", "any location")
INTERNSHIP_TERMS = ("intern", "internship", "trainee")


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    is_new: bool = False
    active_status: str = "unverified"

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
    value = clean_text(text).lower().replace(",", "")
    patterns = [
        (r"(?:inr|₹|rs\.?|rupees?)\s*(\d+(?:\.\d+)?)\s*(k|lakh|lac)?\s*(?:/|per\s*)?(month|monthly|pm)\b", "monthly"),
        (r"(?:inr|₹|rs\.?|rupees?)\s*(\d+(?:\.\d+)?)\s*(k|lakh|lac)?\s*(?:/|per\s*)?(year|annual|annum|pa)\b", "annual"),
        (r"(\d+(?:\.\d+)?)\s*(k|lakh|lac)?\s*(?:inr|₹|rs\.?)\s*(?:/|per\s*)?(month|monthly|pm)\b", "monthly"),
    ]
    for pattern, period in patterns:
        match = re.search(pattern, value, re.I)
        if not match:
            continue
        amount = float(match.group(1))
        unit = (match.group(2) or "").lower()
        if unit == "k":
            amount *= 1_000
        elif unit in ("lakh", "lac"):
            amount *= 100_000
        if period == "annual":
            amount /= 12
        return int(amount)
    return None


def score_job(job: Job, profile: dict[str, Any]) -> tuple[int, list[str]]:
    title = job.title.lower()
    blob = f"{job.title} {job.description}".lower()
    role_hits = [term for term in profile["role_terms"] if term in blob]
    skill_hits = [term for term in profile["skills"] if term in blob]
    score = min(45, len(role_hits) * 15) + min(35, len(skill_hits) * 5)
    if any(term in title for term in profile.get("excluded_titles", [])):
        score -= 30
    if job.location_tier in ("remote_india", "global_work_from_anywhere"):
        score += 15
    elif job.location_tier == "hyderabad":
        score += 10
    elif job.location_tier == "bengaluru":
        score += 7
    else:
        score += 3
    reasons = []
    if role_hits:
        reasons.append("role: " + ", ".join(role_hits[:3]))
    if skill_hits:
        reasons.append("skills: " + ", ".join(skill_hits[:6]))
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
    employment_blob = f"{job.employment_type} {job.title} {job.description[:500]}".lower()
    if internship:
        job.monthly_inr = monthly_compensation_inr(f"{job.compensation} {job.description}")
        if job.monthly_inr is None or job.monthly_inr < int(profile["internship_min_monthly_inr"]):
            return None
    elif any(term in employment_blob for term in ("part-time", "part time", "contract", "freelance", "temporary")):
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
