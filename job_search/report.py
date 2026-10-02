from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .core import Job, generated_at


FIELDS = [
    "id", "is_new", "company", "title", "location", "workplace",
    "employment_type", "compensation", "monthly_inr", "annual_inr",
    "minimum_experience_years", "location_tier", "screening_score",
    "ats_similarity", "actual_hiring_fit", "actual_hiring_fit_status",
    "actual_hiring_fit_raw", "actual_hiring_fit_cap", "actual_hiring_fit_cap_reasons",
    "fit_category_scores", "requirement_coverage_confidence", "unclassified_material_requirements",
    "career_value", "compensation_assessment", "active_status",
    "verification_reason", "verified_at", "listing_age_days", "freshness",
    "screening_queue", "hard_excluded", "evidence_quality", "domain_compatibility",
    "domain_conflicts", "exclusion_signals", "seniority_assessment", "title_relevance",
    "skill_overlap",
    "url", "source", "published_at",
]


def write_reports(
    jobs: list[Job],
    needs_verification: list[Job],
    errors: list[str],
    output_dir: Path,
    suppressed_count: int = 0,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_reported = jobs + needs_verification
    source_counts = dict(sorted(Counter(job.source for job in all_reported).items()))
    payload = {
        "generated_at": generated_at(),
        "count": len(jobs),
        "new_count": sum(job.is_new for job in jobs),
        "needs_verification_count": len(needs_verification),
        "suppressed_count": suppressed_count,
        "source_counts": source_counts,
        "errors": errors,
        "jobs": [job.to_dict() for job in jobs],
        "needs_verification": [job.to_dict() for job in needs_verification],
        "strong_shortlist": [job.to_dict() for job in jobs],
        "review_queue": [job.to_dict() for job in needs_verification],
    }
    (output_dir / "jobs.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output_dir / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(job.to_dict() for job in all_reported)
    lines = [
        "# Latest matching jobs",
        "",
        f"Generated: {payload['generated_at']}",
        f"Strong shortlist: {len(jobs)} ({payload['new_count']} NEW)",
        f"Review queue: {len(needs_verification)}",
        f"Suppressed: {suppressed_count}",
        "",
        "Screening scores are automated prioritization signals. Actual Hiring Fit is a separate evidence-based assessment and is shown only when the job description contains sufficient detail.",
        "",
    ]
    if source_counts:
        lines += ["## Source coverage", "", " | ".join(f"{source}: {count}" for source, count in source_counts.items()), ""]
    if errors:
        lines += ["## Run notes", ""] + [f"- {error}" for error in errors] + [""]
    def append_jobs(title: str, section_jobs: list[Job]) -> None:
        lines.extend([f"## {title}", ""])
        if not section_jobs:
            lines.extend(["No jobs in this section.", ""])
            return
        for job in section_jobs:
            pay = (
                f"INR {job.monthly_inr:,}/month"
                if job.monthly_inr
                else (job.compensation if job.compensation else "Pay not disclosed")
            )
            if job.compensation_assessment == "below_target":
                pay += " (below INR 10 LPA target)"
            elif job.compensation_assessment == "estimate_only":
                pay += " (aggregator estimate; excluded from score)"
            experience = (
                f" | Minimum experience: {job.minimum_experience_years} years"
                if job.minimum_experience_years is not None
                else ""
            )
            reasons = "; ".join(job.screening_reasons or [])
            marker = "NEW - " if job.is_new else ""
            age = f" | {job.freshness} ({job.listing_age_days} days old)" if job.listing_age_days is not None else " | age unknown"
            if job.actual_hiring_fit_status == "assessed":
                cap_text = (
                    f"; capped at {job.actual_hiring_fit_cap}"
                    if job.actual_hiring_fit_cap is not None else ""
                )
                fit_line = f"Actual Hiring Fit: {job.actual_hiring_fit}/100 (raw {job.actual_hiring_fit_raw}{cap_text})"
                categories = "; ".join(
                    f"{name}: {score}" for name, score in (job.fit_category_scores or {}).items()
                )
                cap_reasons = "; ".join(job.actual_hiring_fit_cap_reasons or []) or "none"
                fit_details = [
                    fit_line,
                    f"Requirement coverage confidence: {job.requirement_coverage_confidence:.0%}" if job.requirement_coverage_confidence is not None else "Requirement coverage confidence: unknown",
                    f"Fit categories: {categories}",
                    f"Mandatory-gap caps: {cap_reasons}",
                ]
            else:
                coverage = (
                    f"; requirement coverage {job.requirement_coverage_confidence:.0%}"
                    if job.requirement_coverage_confidence is not None else ""
                )
                fit_details = [f"Actual Hiring Fit: Not assessed (insufficient reliable job-description evidence{coverage})"]
            ats_line = (
                f"ATS/Resume Similarity: {job.ats_similarity}/100"
                if job.ats_similarity is not None else "ATS/Resume Similarity: Not assessed"
            )
            lines.extend([
                f"### {marker}[{job.title}]({job.url})",
                "",
                f"{job.company} | Source: {job.source} | {job.location} | {job.location_tier} | Screening {job.screening_score}/100 | {job.active_status}{age} | {pay}{experience}",
                "",
                f"Why: {reasons}",
                "",
                *fit_details,
                ats_line,
                "",
            ])

    append_jobs("Strong shortlist", jobs)
    append_jobs("Review queue", needs_verification)
    (output_dir / "latest.md").write_text("\n".join(lines), encoding="utf-8")
