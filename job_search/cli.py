from __future__ import annotations

import argparse
import json
from pathlib import Path

from .active import retain_active_jobs
from .core import assign_screening_queue, evaluate, job_fingerprint, sort_jobs
from .report import write_reports
from .sources import collect


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(new_only: bool = False, dry_run: bool = False) -> int:
    profile = load_json(ROOT / "config" / "profile.json")
    sources = load_json(ROOT / "config" / "sources.json")
    seen_path = ROOT / "data" / "seen_jobs.json"
    seen = set(load_json(seen_path)) if seen_path.exists() else set()
    collected, errors = collect(sources)
    if not collected:
        print("No jobs were collected; preserving the previous report.")
        for error in errors:
            print(f"Source error: {error}")
        return 2
    unique = {job.id: job for job in collected}
    evaluated = []
    fingerprints = set()
    for job in unique.values():
        match = evaluate(job, profile)
        fingerprint = job_fingerprint(match) if match else ""
        if match and fingerprint in fingerprints:
            continue
        if match:
            fingerprints.add(fingerprint)
            match.is_new = match.id not in seen and f"fp:{fingerprint}" not in seen
        if match:
            evaluated.append(match)
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
    for job in retained:
        assign_screening_queue(job, profile)
    location_preference = profile.get("location_preference", profile.get("location_priority", []))
    strong_shortlist = sort_jobs(
        [job for job in retained if job.screening_queue == "strong_shortlist"],
        location_preference,
    )
    review_queue = sort_jobs(
        [job for job in retained if job.screening_queue == "review_queue"],
        location_preference,
    )
    suppressed_count = prefilter_suppressed + len(suppressed) + closed_count
    if closed_count:
        errors.append(f"Removed {closed_count} definitively closed or expired job posting(s).")
    if unverified_count:
        errors.append(f"Could not independently verify {unverified_count} application page(s); retained only in the review queue.")
    all_reported = strong_shortlist + review_queue
    if dry_run:
        print("DRY RUN: tracked reports and seen state were not written.")
        print(f"Collected {len(collected)} jobs; strong shortlist {len(strong_shortlist)}; review queue {len(review_queue)}; suppressed {suppressed_count}; removed {closed_count} closed.")
        for heading, jobs, limit in (("Strong shortlist", strong_shortlist, 10), ("Review queue", review_queue, 15)):
            print(f"\n{heading}:")
            if not jobs:
                print("- None")
            for job in jobs[:limit]:
                age = f", {job.freshness}" + (f"/{job.listing_age_days}d" if job.listing_age_days is not None else "")
                pay = f", {job.compensation_assessment}"
                print(f"- {job.title} — {job.company} | screening {job.screening_score}/100 | {job.location_tier}{age}{pay} | {job.active_status}, evidence {job.evidence_quality} | Actual Hiring Fit: not assessed")
    else:
        write_reports(strong_shortlist, review_queue, errors, ROOT / "output", suppressed_count=suppressed_count)
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
    args = parser.parse_args(argv)
    return run(args.new_only, args.dry_run)
