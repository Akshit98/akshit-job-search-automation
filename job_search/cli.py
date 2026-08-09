from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import evaluate, sort_jobs
from .report import write_reports
from .sources import collect


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(include_seen: bool = False) -> int:
    profile = load_json(ROOT / "config" / "profile.json")
    sources = load_json(ROOT / "config" / "sources.json")
    seen_path = ROOT / "data" / "seen_jobs.json"
    seen = set(load_json(seen_path)) if seen_path.exists() else set()
    collected, errors = collect(sources)
    unique = {job.id: job for job in collected}
    accepted = []
    for job in unique.values():
        match = evaluate(job, profile)
        if match and (include_seen or match.id not in seen):
            accepted.append(match)
    accepted = sort_jobs(accepted, profile["location_priority"])
    write_reports(accepted, errors, ROOT / "output")
    seen.update(job.id for job in accepted)
    seen_path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
    print(f"Collected {len(collected)} jobs; wrote {len(accepted)} new matches; {len(errors)} source warnings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resume-tailored job search automation")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="collect, filter, score, and report jobs")
    run_parser.add_argument("--include-seen", action="store_true", help="include jobs already reported")
    args = parser.parse_args(argv)
    return run(args.include_seen)
