from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .core import Job, generated_at


FIELDS = ["id", "is_new", "company", "title", "location", "workplace", "employment_type", "compensation", "monthly_inr", "location_tier", "score", "active_status", "url", "source", "published_at"]


def write_reports(jobs: list[Job], errors: list[str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_counts = dict(sorted(Counter(job.source for job in jobs).items()))
    payload = {"generated_at": generated_at(), "count": len(jobs), "new_count": sum(job.is_new for job in jobs), "source_counts": source_counts, "errors": errors, "jobs": [job.to_dict() for job in jobs]}
    (output_dir / "jobs.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output_dir / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(job.to_dict() for job in jobs)
    lines = ["# Latest matching jobs", "", f"Generated: {payload['generated_at']}", f"Matches: {len(jobs)} ({payload['new_count']} NEW)", ""]
    if source_counts:
        lines += ["## Source coverage", "", " | ".join(f"{source}: {count}" for source, count in source_counts.items()), ""]
    if errors:
        lines += ["## Run notes", ""] + [f"- {error}" for error in errors] + [""]
    for job in jobs:
        pay = f" | INR {job.monthly_inr:,}/month" if job.monthly_inr else (f" | {job.compensation}" if job.compensation else "")
        reasons = "; ".join(job.score_reasons or [])
        marker = "NEW - " if job.is_new else ""
        status = "Verified active" if job.active_status == "active" else "Activity unverified"
        lines += [f"## {marker}[{job.title}]({job.url})", "", f"{job.company} | Source: {job.source} | {job.location} | {job.location_tier} | Fit {job.score}/100 | {status}{pay}", "", f"Why: {reasons}", ""]
    (output_dir / "latest.md").write_text("\n".join(lines), encoding="utf-8")
