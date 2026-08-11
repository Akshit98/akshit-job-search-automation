from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output" / "jobs.json"


def build_slack_message(payload: dict) -> str | None:
    new_jobs = [job for job in payload.get("jobs", []) if job.get("is_new")]
    source_errors = [
        error for error in payload.get("errors", [])
        if not error.startswith(("Removed ", "Could not independently verify "))
    ]
    if not new_jobs and not source_errors:
        return None
    lines = [f":briefcase: *Job search update*: {len(new_jobs)} new match(es)"]
    for job in new_jobs[:5]:
        title = str(job.get("title") or "Untitled role").replace("<", "").replace(">", "")
        company = str(job.get("company") or "Unknown company").replace("<", "").replace(">", "")
        url = str(job.get("url") or "")
        score = job.get("score", "?")
        tier = str(job.get("location_tier") or "location unclassified")
        label = f"<{url}|{title}>" if url.startswith(("https://", "http://")) else title
        lines.append(f"• {label} — {company} ({tier}, {score}/100)")
    if len(new_jobs) > 5:
        lines.append(f"• Plus {len(new_jobs) - 5} more in the GitHub report")
    if source_errors:
        lines.append(f":warning: {len(source_errors)} source warning(s); check the Actions summary.")
    return "\n".join(lines)


def main() -> int:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        print("SLACK_WEBHOOK_URL is not configured; skipping Slack notification.")
        return 0
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    message = build_slack_message(payload)
    if not message:
        print("No new jobs or source warnings; skipping Slack notification.")
        return 0
    request = Request(
        webhook,
        data=json.dumps({"text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        if response.getcode() >= 300:
            raise RuntimeError(f"Slack returned HTTP {response.getcode()}")
    print("Slack notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
