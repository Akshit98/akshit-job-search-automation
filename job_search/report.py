from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .core import Job, generated_at, sanitize_public_url


FIELDS = [
    "id", "is_new", "company", "title", "location", "workplace",
    "employment_type", "compensation", "monthly_inr", "annual_inr",
    "minimum_experience_years", "location_tier", "screening_score",
    "ats_similarity", "actual_hiring_fit", "actual_hiring_fit_status",
    "actual_hiring_fit_raw", "actual_hiring_fit_cap", "actual_hiring_fit_cap_reasons",
    "fit_category_scores", "requirement_coverage_confidence", "unclassified_material_requirements",
    "career_value", "compensation_assessment", "active_status",
    "verification_reason", "verified_at", "listing_age_days", "freshness",
    "screening_queue", "review_disposition", "verification_schedule_status",
    "hard_excluded", "evidence_quality", "domain_compatibility",
    "domain_conflicts", "exclusion_signals", "seniority_assessment", "title_relevance",
    "skill_overlap",
    "canonical_employer", "ats_board_id", "source_type", "source_priority",
    "original_source_url", "canonical_url", "description_provenance",
    "description_retrieval_status", "description_retrieved_at", "source_quality_confidence",
    "canonical_employer_url", "canonical_resolution_status", "canonical_location",
    "verification_original_host", "verification_attempted_host", "verification_stage", "verification_http_class",
    "source_id", "url", "source", "published_at",
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

PUBLIC_VERIFICATION_REASONS = {
    "verified_active", "verified_closed", "canonical_employer_url_missing",
    "canonical_destination_unresolved", "aggregator_page_only",
    "redirect_to_generic_index", "http_blocked", "http_not_found", "timeout",
    "page_inaccessible", "no_structured_job_description", "page_too_large",
    "malformed_page", "unsupported_verification_source",
    "verification_not_attempted", "verification_budget_exhausted",
    "unsafe_destination", "other_sanitized_failure", "cached_verified_active",
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


def _public_job_dict(job: Job) -> dict:
    payload = job.to_dict()
    for field in ("url", "original_source_url", "final_url", "canonical_url", "canonical_employer_url"):
        payload[field] = sanitize_public_url(str(payload.get(field) or ""))
    if payload.get("verification_reason") not in PUBLIC_VERIFICATION_REASONS:
        payload["verification_reason"] = "other_sanitized_failure"
    return payload


def _public_verification_reason(job: Job) -> str:
    return job.verification_reason if job.verification_reason in PUBLIC_VERIFICATION_REASONS else "other_sanitized_failure"


def write_reports(
    jobs: list[Job],
    needs_verification: list[Job],
    errors: list[str],
    output_dir: Path,
    suppressed_count: int = 0,
    source_diagnostics: list[dict] | None = None,
    human_review: list[Job] | None = None,
    verification_backlog: list[Job] | None = None,
    cold_verification_backlog: list[Job] | None = None,
    operational_metrics: dict | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_reported = jobs + needs_verification
    source_diagnostics = _public_diagnostics(source_diagnostics)
    errors = _public_errors(errors)
    source_counts = dict(sorted(Counter(job.source for job in all_reported).items()))
    verification_outcomes = dict(sorted(Counter(_public_verification_reason(job) for job in all_reported).items()))
    if human_review is None:
        human_review = list(needs_verification)
    if verification_backlog is None:
        verification_backlog = []
    if cold_verification_backlog is None:
        cold_verification_backlog = []
    operational_metrics = dict(operational_metrics or {})
    def age_band(job: Job) -> str:
        days = job.listing_age_days
        if days is None: return "unknown"
        if days <= 30: return "0-30"
        if days <= 60: return "31-60"
        if days <= 90: return "61-90"
        if days <= 365: return "91-365"
        return ">365"
    freshness_by_verification = {
        "verified_active": dict(Counter(age_band(job) for job in all_reported if job.active_status == "active")),
        "unverified": dict(Counter(age_band(job) for job in all_reported if job.active_status == "unverified")),
        "stale_unverified_aggregator": dict(Counter(
            age_band(job) for job in all_reported
            if job.source_type == "aggregator" and job.active_status == "unverified"
        )),
    }
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
        "verification_health": {
            "verified_active": sum(job.active_status == "active" for job in all_reported),
            "verified_closed": sum(job.active_status == "closed" for job in all_reported),
            "verification_unresolved": sum(job.active_status == "unverified" for job in all_reported),
            "outcomes": verification_outcomes,
            "freshness_by_verification": freshness_by_verification,
        },
        "operational_metrics": operational_metrics,
        "queue_health": operational_metrics.get("queue_health", {}),
        "jobs": [_public_job_dict(job) for job in jobs],
        "needs_verification": [_public_job_dict(job) for job in needs_verification],
        "strong_shortlist": [_public_job_dict(job) for job in jobs],
        "review_queue": [_public_job_dict(job) for job in needs_verification],
        "human_review": [_public_job_dict(job) for job in human_review],
        "verification_backlog": [_public_job_dict(job) for job in verification_backlog],
        "cold_verification_backlog": [_public_job_dict(job) for job in cold_verification_backlog],
    }
    (output_dir / "jobs.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output_dir / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_public_job_dict(job) for job in all_reported)
    lines = [
        "# Latest matching jobs",
        "",
        f"Generated: {payload['generated_at']}",
        f"Strong shortlist: {len(jobs)} ({payload['new_count']} NEW)",
        f"Review queue: {len(needs_verification)}",
        f"Human review: {len(human_review)}",
        f"Verification backlog: {len(verification_backlog)}",
        f"Cold verification backlog: {len(cold_verification_backlog)}",
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
        lines += [
            f"- {item.get('source_id')}: {item.get('status')} — "
            f"collection(raw {item.get('jobs_returned', 0)}, exact-ID duplicates {item.get('exact_id_duplicates', 0)}); "
            f"eligibility(eligible {item.get('passed_basic_eligibility', 0)}, rejected {item.get('rejected_by_eligibility', 0)}, hard exclusions subset {item.get('hard_excluded', 0)}); "
            f"canonical dedup(non-canonical {item.get('duplicate_non_canonical', 0)}); "
            f"verification(submitted {item.get('submitted_for_verification', 0)}, active {item.get('verified_active', 0)}, closed {item.get('verified_closed', 0)}, unresolved {item.get('verification_unresolved', 0)}); "
            f"final(strong {item.get('strong_shortlist', 0)}, review {item.get('review_queue', 0)}, visible {item.get('final_human_visible', 0)}, suppressed raw-minus-visible {item.get('suppressed', 0)})"
            + (f" ({item.get('reason')})" if item.get('reason') else "")
            for item in source_diagnostics
        ]
        lines += [""]
    if verification_outcomes:
        lines += ["## Verification health", ""]
        lines += [f"- {reason}: {count}" for reason, count in verification_outcomes.items()]
        for label, counts in freshness_by_verification.items():
            lines += [f"- {label.replace('_', ' ').title()} freshness: " + ", ".join(f"{band}: {count}" for band, count in sorted(counts.items()))]
        lines += [""]
    if operational_metrics:
        verification = operational_metrics.get("verification", {})
        deduplication = operational_metrics.get("deduplication", {})
        lines += ["## Production metrics", ""]
        lines += [
            "- Verification: " + ", ".join(
                f"{label} {verification.get(key, 0)}" for key, label in (
                    ("verification_candidates_total", "candidates"),
                    ("jobs_submitted", "submitted"),
                    ("jobs_deferred_by_cap", "deferred by cap"),
                    ("jobs_skipped_by_backoff", "skipped by backoff"),
                    ("cached_verified_active", "cached active"),
                    ("requests_attempted", "HTTP requests"),
                    ("redirects_followed", "redirects"),
                    ("unsafe_destinations_rejected", "unsafe destinations rejected"),
                )
            ),
            "- Deduplication: " + ", ".join(
                f"{label} {deduplication.get(key, 0)}" for key, label in (
                    ("raw_records", "raw"),
                    ("exact_id_duplicates_removed", "exact-ID removed"),
                    ("canonical_duplicates_removed", "canonical removed"),
                    ("distinct_duplicate_groups", "groups"),
                )
            ),
            "",
        ]
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
                f"### {marker}[{job.title}]({sanitize_public_url(job.url)})",
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
    append_jobs("Human review", human_review[:20])
    backlog_all = verification_backlog + cold_verification_backlog
    if backlog_all:
        backlog_freshness = Counter(age_band(job) for job in backlog_all)
        lines += ["## Verification backlog", ""]
        lines += [
            f"- Total: {len(backlog_all)}",
            f"- NEW: {sum(job.is_new for job in backlog_all)}",
            f"- Awaiting retry: {sum(job.verification_schedule_status == 'backoff' for job in backlog_all)}",
            f"- Budget deferred: {sum(job.verification_reason == 'verification_budget_exhausted' for job in backlog_all)}",
            f"- Cold stale aggregator: {len(cold_verification_backlog)}",
            "- Freshness: " + ", ".join(f"{band}: {count}" for band, count in sorted(backlog_freshness.items())),
            "",
            "Full backlog records remain available in JSON and CSV.",
            "",
        ]
    (output_dir / "latest.md").write_text("\n".join(lines), encoding="utf-8")
