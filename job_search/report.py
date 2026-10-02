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
    "canonical_employer", "ats_board_id", "source_type", "source_priority",
    "original_source_url", "canonical_url", "description_provenance",
    "description_retrieval_status", "description_retrieved_at", "source_quality_confidence",
    "url", "source", "published_at",
]


PUBLIC_DIAGNOSTIC_REASONS = {
    "successful": "",
    "zero_results": "",
    "disabled": "disabled by configuration",
    "missing_credential": "required credentials unavailable",
    "timeout": "request timed out",
    "http_failure": "network request failed",
    "malformed_response": "response was not valid JSON",
    "schema_parser_error": "response schema was incompatible",
}


def _public_diagnostics(items: list[dict] | None) -> list[dict]:
    public = []
    for item in items or []:
        safe = dict(item)
        status = str(safe.get("status") or "schema_parser_error")
        reason = PUBLIC_DIAGNOSTIC_REASONS.get(status, "source processing failed")
        if status == "http_failure" and str(safe.get("reason", "")).startswith("HTTP "):
            code = str(safe["reason"])[5:8]
            reason = f"HTTP {code}" if code.isdigit() else "network request failed"
        safe["reason"] = reason
        public.append(safe)
    return public


def _public_errors(errors: list[str]) -> list[str]:
    safe = []
    for error in errors:
        if error.startswith("Removed ") or error.startswith("Could not independently verify "):
            safe.append(error)
        else:
            safe.append("A configured source failed; see the categorized source-health entry.")
    return list(dict.fromkeys(safe))


def write_reports(
    jobs: list[Job],
    needs_verification: list[Job],
    errors: list[str],
    output_dir: Path,
    suppressed_count: int = 0,
    source_diagnostics: list[dict] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_reported = jobs + needs_verification
    source_diagnostics = _public_diagnostics(source_diagnostics)
    errors = _public_errors(errors)
    source_counts = dict(sorted(Counter(job.source for job in all_reported).items()))
    payload = {
        "generated_at": generated_at(),
        "count": len(jobs),
        "new_count": sum(job.is_new for job in jobs),
        "needs_verification_count": len(needs_verification),
        "suppressed_count": suppressed_count,
        "source_counts": source_counts,
        "errors": errors,
        "source_diagnostics": source_diagnostics,
        "source_health": {
            "attempted": sum(bool(item.get("attempted")) for item in source_diagnostics),
            "successful": sum(item.get("status") in ("successful", "zero_results") for item in source_diagnostics),
            "failed": sum(item.get("status") in ("http_failure", "timeout", "malformed_response", "schema_parser_error") for item in source_diagnostics),
            "missing_credential": sum(item.get("status") == "missing_credential" for item in source_diagnostics),
            "raw_jobs": sum(int(item.get("jobs_returned", 0)) for item in source_diagnostics),
            "strong_shortlist": len(jobs),
            "review_queue": len(needs_verification),
            "suppressed": suppressed_count,
            "sufficient_fit_evidence": sum(job.evidence_quality == "sufficient" for job in all_reported),
            "numerical_hiring_fit": sum(job.actual_hiring_fit_status == "assessed" for job in all_reported),
        },
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
    if source_diagnostics:
        lines += ["## Source health", ""]
        lines += [f"- {item.get('source_id')}: {item.get('status')} — {item.get('jobs_returned', 0)} jobs" + (f" ({item.get('reason')})" if item.get('reason') else "") for item in source_diagnostics]
        lines += [""]
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
