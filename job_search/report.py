from __future__ import annotations

import csv
import json
from pathlib import Path

from .core import Job, generated_at


FIELDS = ["id", "is_new", "company", "title", "location", "workplace", "employment_type", "compensation", "monthly_inr", "location_tier", "score", "url", "source", "published_at"]


def write_reports(jobs: list[Job], errors: list[str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": generated_at(), "count": len(jobs), "errors": errors, "jobs": [job.to_dict() for job in jobs]}
    (output_dir / "jobs.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output_dir / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(job.to_dict() for job in jobs)
    lines = ["# Latest matching jobs", "", f"Generated: {payload['generated_at']}", f"Matches: {len(jobs)}", ""]
    if errors:
        lines += ["## Source warnings", ""] + [f"- {error}" for error in errors] + [""]
    for job in jobs:
        pay = f" | INR {job.monthly_inr:,}/month" if job.monthly_inr else (f" | {job.compensation}" if job.compensation else "")
        reasons = "; ".join(job.score_reasons or [])
        marker = "NEW - " if job.is_new else ""
        lines += [f"## {marker}[{job.title}]({job.url})", "", f"{job.company} | Source: {job.source} | {job.location} | {job.location_tier} | Fit {job.score}/100{pay}", "", f"Why: {reasons}", ""]
    (output_dir / "latest.md").write_text("\n".join(lines), encoding="utf-8")
