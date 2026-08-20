from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .core import Job, clean_text
from .sources import USER_AGENT


CLOSED_MARKERS = (
    "job removed",
    "job not found",
    "applications are now closed",
    "applications have closed",
    "applications closed",
    "no longer accepting applications",
    "job is no longer available",
    "job is no longer active",
    "this position is no longer available",
    "this role is no longer available",
    "the job you are looking for is no longer available",
    "this job has expired",
    "job posting has expired",
    "position has been filled",
    "role has been filled",
    "vacancy is closed",
    "posting has been removed",
)

ACTIVE_MARKERS = (
    "apply now",
    "apply for this job",
    "apply for this role",
    "apply to this job",
    "submit application",
    "submit your application",
    "application form",
    "start application",
)


def classify_active_response(status_code: int, body: str) -> str:
    """Return active, closed, or unverified from an application-page response."""
    if status_code in (404, 410):
        return "closed"
    if status_code < 200 or status_code >= 400:
        return "unverified"
    text = clean_text(body).lower()
    if any(marker in text for marker in CLOSED_MARKERS):
        return "closed"
    if any(marker in text for marker in ACTIVE_MARKERS):
        return "active"
    return "unverified"


def redirected_to_listing_index(original_url: str, final_url: str) -> bool:
    """Detect removed job pages redirected to the board's general job index."""
    original = urlparse(original_url)
    final = urlparse(final_url)
    if original.netloc.lower() != final.netloc.lower():
        return False
    original_path = original.path.rstrip("/").lower()
    final_path = final.path.rstrip("/").lower()
    return "/jobs/" in original_path and final_path in ("/jobs", "")


def check_job_active(job: Job, timeout: int = 12) -> str:
    if not job.url or not job.url.startswith(("http://", "https://")):
        return "unverified"
    request = Request(
        job.url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(300_000).decode("utf-8", errors="ignore")
            if redirected_to_listing_index(job.url, response.geturl()):
                return "closed"
            return classify_active_response(response.getcode(), body)
    except HTTPError as exc:
        return classify_active_response(exc.code, "")
    except (URLError, TimeoutError, ValueError):
        return "unverified"


def published_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.isdigit():
            timestamp = int(raw)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, timezone.utc)
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def is_stale(job: Job, maximum_age_days: int, now: datetime | None = None) -> bool:
    # Zero disables age-based exclusion. Older vacancies remain eligible when
    # their application page still confirms that applications are open.
    if maximum_age_days <= 0:
        return False
    published = published_datetime(job.published_at)
    if not published:
        return False
    current = now or datetime.now(timezone.utc)
    return published < current - timedelta(days=maximum_age_days)


def retain_active_jobs(
    jobs: list[Job],
    workers: int = 8,
    maximum_age_days: int = 0,
    require_verified_active: bool = False,
) -> tuple[list[Job], int, int]:
    """Live-check jobs and optionally retain only verified-open vacancies."""
    if not jobs:
        return [], 0, 0
    recent = []
    stale = []
    for job in jobs:
        if is_stale(job, maximum_age_days):
            job.active_status = "closed"
            stale.append(job)
        else:
            recent.append(job)
    with ThreadPoolExecutor(max_workers=min(workers, len(recent) or 1)) as executor:
        futures = {executor.submit(check_job_active, job): job for job in recent}
        for future in as_completed(futures):
            job = futures[future]
            try:
                job.active_status = future.result()
            except Exception:
                job.active_status = "unverified"
    unverified_count = sum(job.active_status == "unverified" for job in recent)
    if require_verified_active:
        active = [job for job in recent if job.active_status == "active"]
    else:
        active = [job for job in recent if job.active_status != "closed"]
    closed_count = len(stale) + sum(job.active_status == "closed" for job in recent)
    return active, closed_count, unverified_count
