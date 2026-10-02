from __future__ import annotations

import argparse
import json
from pathlib import Path

from .active import retain_active_jobs
from .core import assign_screening_queue, evaluate, job_fingerprint, sort_jobs
from .evidence import load_evidence
from .fit import assess_job_fit
from .report import write_reports
from .sources import collect, prefer_source_job
from .tracking import ALLOWED_STATES, TrackingStore, resolve_private_state_path


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


def run(new_only: bool = False, dry_run: bool = False) -> int:
    profile = load_json(ROOT / "config" / "profile.json")
    evidence = load_evidence(ROOT / "config" / "evidence.json")
    sources = load_json(ROOT / "config" / "sources.json")
    seen_path = ROOT / "data" / "seen_jobs.json"
    seen = set(load_json(seen_path)) if seen_path.exists() else set()
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
    unique = {job.id: job for job in collected}
    evaluated_by_fingerprint = {}
    for job in unique.values():
        match = evaluate(job, profile)
        fingerprint = job_fingerprint(match) if match else ""
        if match:
            match.is_new = match.id not in seen and f"fp:{fingerprint}" not in seen
            existing = evaluated_by_fingerprint.get(fingerprint)
            evaluated_by_fingerprint[fingerprint] = prefer_source_job(existing, match) if existing else match
    evaluated = list(evaluated_by_fingerprint.values())
    prefilter_suppressed = len(unique) - len(evaluated)
    suppressed = [job for job in evaluated if job.screening_queue == "suppressed"]
    verification_candidates = [
        job for job in evaluated
        if job.screening_queue != "suppressed" and (not new_only or job.is_new)
    ]
    retained, closed_count, unverified_count = retain_active_jobs(
        verification_candidates,
        maximum_age_days=int(profile.get("maximum_listing_age_days", 0)),
        require_verified_active=False,
        preferred_age_days=int(profile.get("preferred_listing_age_days", 7)),
    )
    retained_before_reassessment = len(retained)
    retained = reassess_after_enrichment(retained, profile, evidence)
    post_enrichment_removed = retained_before_reassessment - len(retained)
    location_preference = profile.get("location_preference", profile.get("location_priority", []))
    strong_shortlist = sort_jobs(
        [job for job in retained if job.screening_queue == "strong_shortlist"],
        location_preference,
    )
    review_queue = sort_jobs(
        [job for job in retained if job.screening_queue == "review_queue"],
        location_preference,
    )
    post_enrichment_suppressed = sum(job.screening_queue == "suppressed" for job in retained)
    suppressed_count = prefilter_suppressed + len(suppressed) + closed_count + post_enrichment_removed + post_enrichment_suppressed
    if closed_count:
        errors.append(f"Removed {closed_count} definitively closed or expired job posting(s).")
    if unverified_count:
        errors.append(f"Could not independently verify {unverified_count} application page(s); retained only in the review queue.")
    all_reported = strong_shortlist + review_queue
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
            print("\nSource health:")
            for item in diagnostics:
                print(f"- {item.source_id}: {item.status}; jobs={item.jobs_returned}" + (f"; {item.reason}" if item.reason else ""))
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
        write_reports(strong_shortlist, review_queue, errors, ROOT / "output", suppressed_count=suppressed_count, source_diagnostics=[item.to_dict() for item in diagnostics])
        seen.update(job.id for job in all_reported)
        seen.update(f"fp:{job_fingerprint(job)}" for job in all_reported)
        seen_path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
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
