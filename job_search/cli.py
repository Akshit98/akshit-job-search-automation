from __future__ import annotations

import argparse
import json
from pathlib import Path

from .active import retain_active_jobs
from .core import evaluate, job_fingerprint, sort_jobs
from .report import write_reports
from .sources import collect


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(new_only: bool = False) -> int:
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
    accepted = []
    fingerprints = set()
    for job in unique.values():
        match = evaluate(job, profile)
        fingerprint = job_fingerprint(match) if match else ""
        if match and fingerprint in fingerprints:
            continue
        if match:
            fingerprints.add(fingerprint)
            match.is_new = match.id not in seen and f"fp:{fingerprint}" not in seen
        if match and (not new_only or match.is_new):
            accepted.append(match)
    accepted = sort_jobs(accepted, profile["location_priority"])
    accepted, closed_count, unverified_count = retain_active_jobs(
        accepted,
        maximum_age_days=int(profile.get("maximum_listing_age_days", 0)),
        require_verified_active=bool(profile.get("require_verified_active", False)),
    )
    if closed_count:
        errors.append(f"Removed {closed_count} definitively closed or expired job posting(s).")
    if unverified_count:
        if profile.get("require_verified_active", False):
            errors.append(f"Excluded {unverified_count} application page(s) because active hiring could not be verified.")
        else:
            errors.append(f"Could not independently verify {unverified_count} application page(s); retained as unverified.")
    write_reports(accepted, errors, ROOT / "output")
    seen.update(job.id for job in accepted)
    seen.update(f"fp:{job_fingerprint(job)}" for job in accepted)
    seen_path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
    new_count = sum(job.is_new for job in accepted)
    print(f"Collected {len(collected)} jobs; wrote {len(accepted)} active matches ({new_count} new); removed {closed_count} closed; {len(errors)} warnings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resume-tailored job search automation")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="collect, filter, score, and report jobs")
    run_parser.add_argument("--new-only", action="store_true", help="report only jobs not seen in an earlier run")
    args = parser.parse_args(argv)
    return run(args.new_only)
