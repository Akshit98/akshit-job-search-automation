from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .active import retain_active_jobs
from .core import (
    assign_screening_queue, canonicalize_jobs, evaluate,
    normalize_company_name, seen_identity_keys, sort_jobs,
)
from .evidence import load_evidence
from .fit import assess_job_fit
from .report import write_reports
from .sources import collect, prefer_source_job
from .http_safe import MAX_VERIFICATION_JOBS, MAX_HTTP_REQUESTS
from .tracking import ALLOWED_STATES, TrackingStore, resolve_private_state_path
from .verification_state import (
    VerificationStateError, assign_review_dispositions, atomic_write_json,
    load_verification_state, plan_verification, register_duplicate_aliases,
    stable_identity_hash, strong_capable, update_verification_state,
    verification_priority_key, write_verification_state,
)


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def reassess_after_enrichment(jobs, profile, evidence):
    """Recompute every description-dependent decision after page enrichment."""
    reassessed = []
    for job in jobs:
        match = evaluate(job, profile)
        if match is None:
            continue
        assess_job_fit(match, evidence)
        reassessed.append(match)
    return reassessed


def _diagnostic_map(diagnostics):
    return {item.source_id: item for item in diagnostics}


def _increment(diagnostic_by_source, job, field: str, amount: int = 1) -> None:
    diagnostic = diagnostic_by_source.get(job.source_id)
    if diagnostic is not None:
        setattr(diagnostic, field, int(getattr(diagnostic, field, 0)) + amount)


def _freshness_bucket(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 365:
        return "91-365"
    return "over-365"


def run(new_only: bool = False, dry_run: bool = False) -> int:
    profile = load_json(ROOT / "config" / "profile.json")
    evidence = load_evidence(ROOT / "config" / "evidence.json")
    sources = load_json(ROOT / "config" / "sources.json")
    seen_path = ROOT / "data" / "seen_jobs.json"
    verification_state_path = ROOT / "data" / "verification_state.json"
    seen = set(load_json(seen_path)) if seen_path.exists() else set()
    try:
        verification_state = load_verification_state(verification_state_path)
    except VerificationStateError:
        print("Verification state is invalid; preserving reports and persistent state.")
        return 3
    collection = collect(sources, include_diagnostics=True)
    if len(collection) == 2:  # compatibility for mocked callers
        collected, errors = collection
        diagnostics = []
    else:
        collected, errors, diagnostics = collection
    if not collected:
        print("No jobs were collected; preserving the previous report.")
        for error in errors:
            print(f"Source error: {error}")
        return 2
    diagnostic_by_source = _diagnostic_map(diagnostics)
    unique = {}
    duplicate_pairs = []
    for job in collected:
        existing = unique.get(job.id)
        if existing is None:
            unique[job.id] = job
        else:
            winner = prefer_source_job(existing, job)
            loser = job if winner is existing else existing
            _increment(diagnostic_by_source, loser, "exact_id_duplicates")
            duplicate_pairs.append((loser, winner))
            unique[job.id] = winner
    evaluated = []
    for job in unique.values():
        match = evaluate(job, profile)
        if match:
            _increment(diagnostic_by_source, match, "passed_basic_eligibility")
            match.is_new = seen_identity_keys(match).isdisjoint(seen)
            evaluated.append(match)
        else:
            _increment(diagnostic_by_source, job, "rejected_by_eligibility")
    # Candidate grouping never authorizes a merge. Every non-ID merge passes
    # through same_vacancy() inside canonicalize_jobs().
    evaluated, preverification_duplicates = canonicalize_jobs(evaluated)
    duplicate_pairs.extend(preverification_duplicates)
    register_duplicate_aliases(verification_state, duplicate_pairs)
    for loser, _winner in preverification_duplicates:
        _increment(diagnostic_by_source, loser, "duplicate_non_canonical")
    suppressed = [job for job in evaluated if job.screening_queue == "suppressed"]
    verification_candidates = [
        job for job in evaluated
        if job.screening_queue != "suppressed" and (not new_only or job.is_new)
    ]
    for job in suppressed:
        if job.hard_excluded:
            _increment(diagnostic_by_source, job, "hard_excluded")
    schedule = plan_verification(
        verification_candidates,
        verification_state,
        maximum_jobs=MAX_VERIFICATION_JOBS,
        preferred_age_days=int(profile.get("preferred_listing_age_days", 7)),
    )
    retry_submission_count = sum(
        stable_identity_hash(job, verification_state) in verification_state.get("jobs", {})
        for job in schedule["selected"]
    )
    for job in schedule["selected"]:
        _increment(diagnostic_by_source, job, "submitted_for_verification")
    active_result = retain_active_jobs(
        schedule["selected"],
        maximum_age_days=int(profile.get("maximum_listing_age_days", 0)),
        require_verified_active=False,
        preferred_age_days=int(profile.get("preferred_listing_age_days", 7)),
        maximum_jobs=MAX_VERIFICATION_JOBS,
        request_budget=MAX_HTTP_REQUESTS,
        return_metrics=True,
    )
    if len(active_result) == 3:  # compatibility for mocked tests
        retained, closed_count, unverified_count = active_result
        verification_metrics = {"jobs_submitted": len(schedule["selected"]), "requests_attempted": 0, "redirects_followed": 0, "unsafe_destinations": 0, "budget_exhausted": 0}
    else:
        retained, closed_count, unverified_count, verification_metrics = active_result
    closed_count += len(schedule["closed_dormant"])
    retained = retained + schedule["cached_active"] + schedule["backoff"] + schedule["deferred"] + schedule["cold"]
    unverified_count += len(schedule["backoff"]) + len(schedule["deferred"]) + len(schedule["cold"])
    update_verification_state(
        verification_state,
        attempted=schedule["selected"],
        deferred=schedule["deferred"],
    )
    for job in verification_candidates:
        field = {
            "active": "verified_active",
            "closed": "verified_closed",
            "unverified": "verification_unresolved",
        }.get(job.active_status, "verification_unresolved")
        _increment(diagnostic_by_source, job, field)
    retained_before_reassessment = len(retained)
    retained = reassess_after_enrichment(retained, profile, evidence)
    post_enrichment_removed = retained_before_reassessment - len(retained)
    retained, canonical_duplicates = canonicalize_jobs(retained)
    duplicate_pairs.extend(canonical_duplicates)
    register_duplicate_aliases(verification_state, canonical_duplicates)
    for loser, _winner in canonical_duplicates:
        _increment(diagnostic_by_source, loser, "duplicate_non_canonical")
    location_preference = profile.get("location_preference", profile.get("location_priority", []))
    strong_shortlist = sort_jobs(
        [job for job in retained if job.screening_queue == "strong_shortlist"],
        location_preference,
    )
    review_queue = sort_jobs(
        [job for job in retained if job.screening_queue == "review_queue"],
        location_preference,
    )
    human_review, verification_backlog, cold_verification_backlog = assign_review_dispositions(
        review_queue, verification_state, human_limit=20,
    )
    post_enrichment_suppressed = sum(job.screening_queue == "suppressed" for job in retained)
    if closed_count:
        errors.append(f"Removed {closed_count} definitively closed or expired job posting(s).")
    if unverified_count:
        errors.append(f"Could not independently verify {unverified_count} application page(s); retained only in the review queue.")
    all_reported = strong_shortlist + review_queue
    # Suppressed is the terminal complement of human-visible records. This
    # includes eligibility rejection, every dedup stage, hard exclusions,
    # closed jobs, and any post-enrichment removals exactly once.
    suppressed_count = len(collected) - len(all_reported)
    for job in strong_shortlist:
        _increment(diagnostic_by_source, job, "strong_shortlist")
        _increment(diagnostic_by_source, job, "final_human_visible")
    for job in review_queue:
        _increment(diagnostic_by_source, job, "review_queue")
        _increment(diagnostic_by_source, job, "final_human_visible")
    for diagnostic in diagnostics:
        diagnostic.suppressed = max(0, diagnostic.jobs_returned - diagnostic.final_human_visible)
        diagnostic.jobs_retained = diagnostic.final_human_visible
    exact_duplicates = sum(item.exact_id_duplicates for item in diagnostics)
    canonical_duplicates_count = len(preverification_duplicates) + len(canonical_duplicates)
    duplicate_groups = {
        (normalize_company_name(winner.company), winner.title.casefold())
        for _loser, winner in duplicate_pairs
    }
    company_merges = sum(
        normalize_company_name(loser.company) == normalize_company_name(winner.company)
        and loser.company.casefold() != winner.company.casefold()
        for loser, winner in duplicate_pairs
    )
    selected = schedule["selected"]
    source_submission_mix = Counter(job.source_type for job in selected)
    verification_outcomes = Counter(job.verification_reason for job in verification_candidates)
    def disposition_freshness(jobs):
        return dict(Counter(_freshness_bucket(job.listing_age_days) for job in jobs))
    operational_metrics = {
        "verification": {
            "verification_candidates_total": len(verification_candidates),
            "verification_job_cap": MAX_VERIFICATION_JOBS,
            "verification_request_cap": MAX_HTTP_REQUESTS,
            "jobs_submitted": len(selected),
            "jobs_deferred_by_cap": len(schedule["deferred"]),
            "jobs_skipped_by_backoff": len(schedule["backoff"]),
            "cached_verified_active": len(schedule["cached_active"]),
            "scheduled_this_run": len(selected),
            "never_scheduled_candidates": sum(
                int(verification_state.get("jobs", {}).get(stable_identity_hash(job, verification_state), {}).get("times_scheduled", 0)) == 0
                for job in schedule["priority_pool"]
            ),
            "closed_rechecks_due": schedule["closed_rechecks_due"],
            "invalid_state_entries_discarded": int(verification_state.get("invalid_entries_discarded", 0)),
            "requests_attempted": verification_metrics["requests_attempted"],
            "redirects_followed": verification_metrics["redirects_followed"],
            "unsafe_destinations_rejected": verification_metrics["unsafe_destinations"],
            "verification_budget_exhausted": sum(job.verification_reason == "verification_budget_exhausted" for job in verification_candidates),
            "outcomes": dict(sorted(verification_outcomes.items())),
            "direct_employer_submissions": source_submission_mix.get("direct_employer", 0),
            "public_board_submissions": source_submission_mix.get("job_board", 0),
            "aggregator_submissions": source_submission_mix.get("aggregator", 0),
            "new_submissions": sum(job.is_new for job in selected),
            "strong_capable_submissions": sum(strong_capable(job) for job in selected),
            "retry_submissions": retry_submission_count,
        },
        "deduplication": {
            "raw_records": len(collected),
            "exact_id_duplicates_removed": exact_duplicates,
            "canonical_duplicates_removed": canonical_duplicates_count,
            "distinct_duplicate_groups": len(duplicate_groups),
            "company_normalization_merges": company_merges,
        },
        "queue_health": {
            "strong_shortlist": len(strong_shortlist),
            "human_review": len(human_review),
            "verification_backlog": len(verification_backlog),
            "cold_verification_backlog": len(cold_verification_backlog),
            "suppressed": suppressed_count,
            "new_seen": {
                "strong_shortlist": {"new": sum(job.is_new for job in strong_shortlist), "seen": sum(not job.is_new for job in strong_shortlist)},
                "human_review": {"new": sum(job.is_new for job in human_review), "seen": sum(not job.is_new for job in human_review)},
                "verification_backlog": {"new": sum(job.is_new for job in verification_backlog), "seen": sum(not job.is_new for job in verification_backlog)},
                "cold_verification_backlog": {"new": sum(job.is_new for job in cold_verification_backlog), "seen": sum(not job.is_new for job in cold_verification_backlog)},
            },
            "freshness": {
                "human_review": disposition_freshness(human_review),
                "verification_backlog": disposition_freshness(verification_backlog),
                "cold_verification_backlog": disposition_freshness(cold_verification_backlog),
            },
            "stale_unverified_aggregator": len(cold_verification_backlog),
        },
    }
    if dry_run:
        print("DRY RUN: tracked reports and seen state were not written.")
        print(f"Collected {len(collected)} jobs; strong shortlist {len(strong_shortlist)}; review queue {len(review_queue)}; suppressed {suppressed_count}; removed {closed_count} closed.")
        direct_count = sum(job.source_type == "direct_employer" for job in collected)
        aggregator_count = sum(job.source_type == "aggregator" for job in collected)
        sufficient_count = sum(job.evidence_quality == "sufficient" for job in all_reported)
        incomplete_count = len(all_reported) - sufficient_count
        assessed_count = sum(job.actual_hiring_fit_status == "assessed" for job in all_reported)
        print(f"Source mix: direct-employer {direct_count}; aggregator {aggregator_count}; other public boards {len(collected) - direct_count - aggregator_count}.")
        print(f"Evidence quality after eligibility: sufficient {sufficient_count}; partial/insufficient {incomplete_count}; numerical hiring fit {assessed_count}; insufficient fit {len(all_reported) - assessed_count}.")
        if diagnostics:
            print("\nStage-aware source funnel:")
            for item in diagnostics:
                print(
                    f"- {item.source_id}\n"
                    f"  collection: raw={item.jobs_returned}, exact_id_duplicates={item.exact_id_duplicates}\n"
                    f"  eligibility: eligible={item.passed_basic_eligibility}, rejected={item.rejected_by_eligibility}, hard_exclusions_subset={item.hard_excluded}\n"
                    f"  canonical_dedup: non_canonical_copies={item.duplicate_non_canonical}\n"
                    f"  verification: submitted={item.submitted_for_verification}, active={item.verified_active}, closed={item.verified_closed}, unresolved={item.verification_unresolved}\n"
                    f"  final: strong={item.strong_shortlist}, review={item.review_queue}, visible={item.final_human_visible}, suppressed_raw_minus_visible={item.suppressed}"
                )
        print(
            "Verification workload: "
            f"jobs_submitted={verification_metrics['jobs_submitted']}, requests_attempted={verification_metrics['requests_attempted']}, "
            f"redirects={verification_metrics['redirects_followed']}, unsafe_destinations={verification_metrics['unsafe_destinations']}, "
            f"budget_exhausted={verification_metrics['budget_exhausted']}."
        )
        verification_counts = {}
        for job in verification_candidates:
            verification_counts[job.verification_reason] = verification_counts.get(job.verification_reason, 0) + 1
        print("Verification outcomes: " + ", ".join(f"{key}={value}" for key, value in sorted(verification_counts.items())))
        print(f"Duplicate groups detected: {len(duplicate_groups)}; duplicate records removed from human-facing output: {len(duplicate_pairs)}.")
        print(f"Company-normalization merges: {company_merges}.")
        if duplicate_pairs:
            print("Duplicate examples (removed -> retained):")
            for loser, winner in duplicate_pairs[:15]:
                print(
                    f"- {loser.company} / {loser.title} / {loser.location or 'unknown'} / {loser.source}"
                    f" -> {winner.company} / {winner.title} / {winner.location or 'unknown'} / {winner.source}"
                )
        for label, freshness_jobs in (
            ("Verified active", [job for job in all_reported if job.active_status == "active"]),
            ("Unverified", [job for job in all_reported if job.active_status == "unverified"]),
            ("Stale unverified aggregator", [job for job in all_reported if job.source_type == "aggregator" and job.active_status == "unverified"]),
        ):
            freshness_counts = {bucket: 0 for bucket in ("0-30", "31-60", "61-90", "91-365", "over-365", "unknown")}
            for job in freshness_jobs:
                freshness_counts[_freshness_bucket(job.listing_age_days)] += 1
            print(label + " freshness: " + ", ".join(f"{key}={value}" for key, value in freshness_counts.items()))
        explicit_location_conflicts = sum(
            job.location_tier == "other_india"
            and any(city in job.description.lower() for city in ("bangalore", "bengaluru", "hyderabad"))
            for job in all_reported if job.location
        )
        print(f"Explicit source locations protected from incidental description cities: {explicit_location_conflicts}.")
        print(f"Seen-state: seen={sum(not job.is_new for job in all_reported)}, NEW={sum(job.is_new for job in all_reported)}.")
        print(
            f"Review dispositions: human_review={len(human_review)}, verification_backlog={len(verification_backlog)}, "
            f"cold_verification_backlog={len(cold_verification_backlog)}."
        )
        print(
            f"Scheduling: candidates={len(verification_candidates)}, submitted={len(selected)}, "
            f"deferred_by_cap={len(schedule['deferred'])}, skipped_by_backoff={len(schedule['backoff'])}, "
            f"cached_active={len(schedule['cached_active'])}."
        )
        print(
            f"Priority regression counts: score45+ attempted={sum(strong_capable(job) for job in selected)}, "
            f"score45+ deferred={sum(strong_capable(job) for job in schedule['deferred'])}; "
            f"fresh0-7 attempted={sum((job.listing_age_days or 10**9) <= 7 for job in selected)}, "
            f"fresh0-7 deferred={sum((job.listing_age_days or 10**9) <= 7 for job in schedule['deferred'])}."
        )
        print("Source submission mix: " + ", ".join(f"{key}={value}" for key, value in sorted(source_submission_mix.items())))
        print("Evidence quality in submitted set: " + ", ".join(f"{key}={value}" for key, value in sorted(Counter(job.evidence_quality for job in selected).items())))
        for heading, priority_rows in (
            ("Top 25 verification priorities", schedule["priority_snapshot"][:25]),
            ("First 10 below verification cutoff", schedule["priority_snapshot"][MAX_VERIFICATION_JOBS:MAX_VERIFICATION_JOBS + 10]),
        ):
            print(f"\n{heading}:")
            if not priority_rows:
                print("- None")
            for index, row in enumerate(priority_rows, 1):
                print(f"- {index}. {row['title']} — {row['company']} | score {row['screening_score']}, age {row['listing_age_days']}, NEW {row['is_new']}, {row['source_type']}, {row['evidence_quality']}")
        for heading, jobs, limit in (("Strong shortlist", strong_shortlist, 10), ("Review queue", review_queue, 15)):
            print(f"\n{heading}:")
            if not jobs:
                print("- None")
            for job in jobs[:limit]:
                age = f", {job.freshness}" + (f"/{job.listing_age_days}d" if job.listing_age_days is not None else "")
                pay = f", {job.compensation_assessment}"
                fit = (
                    f"{job.actual_hiring_fit}/100 (raw {job.actual_hiring_fit_raw}"
                    + (f", cap {job.actual_hiring_fit_cap}" if job.actual_hiring_fit_cap is not None else "")
                    + ")"
                    if job.actual_hiring_fit_status == "assessed"
                    else "not assessed: insufficient evidence"
                )
                print(f"- {job.title} — {job.company} | {job.source}/{job.source_type}, {job.description_provenance} | screening {job.screening_score}/100 | {job.location_tier}{age}{pay} | {job.active_status}, evidence {job.evidence_quality} | Actual Hiring Fit: {fit} | ATS similarity: {job.ats_similarity}/100")
    else:
        write_reports(
            strong_shortlist, review_queue, errors, ROOT / "output",
            suppressed_count=suppressed_count,
            source_diagnostics=[item.to_dict() for item in diagnostics],
            human_review=human_review,
            verification_backlog=verification_backlog,
            cold_verification_backlog=cold_verification_backlog,
            operational_metrics=operational_metrics,
        )
        for job in all_reported:
            seen.update(seen_identity_keys(job))
        atomic_write_json(seen_path, sorted(seen))
        write_verification_state(verification_state_path, verification_state)
    new_count = sum(job.is_new for job in strong_shortlist)
    print(f"Collected {len(collected)} jobs; strong shortlist {len(strong_shortlist)} ({new_count} new); review queue {len(review_queue)}; suppressed {suppressed_count}; removed {closed_count} closed; {len(errors)} warnings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resume-tailored job search automation")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="collect, filter, score, and report jobs")
    run_parser.add_argument("--new-only", action="store_true", help="report only jobs not seen in an earlier run")
    run_parser.add_argument("--dry-run", action="store_true", help="collect and print a diagnostic without changing reports or seen state")
    status_parser = sub.add_parser("status", help="update private local application state")
    status_parser.add_argument("job_id", help="stable job ID or fingerprint")
    status_parser.add_argument("state", choices=sorted(ALLOWED_STATES))
    status_parser.add_argument("--note", help="private note stored only in the local tracking file")
    args = parser.parse_args(argv)
    if args.command == "status":
        store = TrackingStore(resolve_private_state_path(ROOT), ROOT)
        record = store.set_status(args.job_id, args.state, args.note)
        print(f"Saved private status for {args.job_id}: {record['state']}")
        return 0
    return run(args.new_only, args.dry_run)
