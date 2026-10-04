from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from .core import Job, clean_text, sanitize_public_url
from .http_safe import (
    DEFAULT_TIMEOUT_SECONDS, MAX_RESPONSE_BYTES, MAX_VERIFICATION_JOBS,
    RedirectLimitExceeded, RedirectLoop, RequestBudget,
    RequestBudgetExhausted, ResponseTooLarge, UnsafeDestination, safe_http_get,
)
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

# Lever pages are commonly 700-800 KB because they embed application data near
# the end of the document. Read the complete response up to a deliberate hard
# ceiling, and reject oversized pages rather than parsing a misleading prefix.
MAX_JOB_PAGE_BYTES = MAX_RESPONSE_BYTES


BLOCKED_HTTP_CODES = {401, 403, 407, 429}


def _set_verification(job: Job, status: str, reason: str) -> str:
    job.active_status = status
    job.verification_reason = reason
    return status


def _structured_location(payload: dict) -> str:
    values = payload.get("jobLocation") or payload.get("applicantLocationRequirements") or []
    values = values if isinstance(values, list) else [values]
    locations = []
    for value in values:
        if not isinstance(value, dict):
            continue
        address = value.get("address") or value
        if not isinstance(address, dict):
            continue
        parts = [
            clean_text(address.get("addressLocality")),
            clean_text(address.get("addressRegion")),
            clean_text(address.get("addressCountry")),
        ]
        location = ", ".join(part for part in parts if part)
        if location:
            locations.append(location)
    return "; ".join(dict.fromkeys(locations))


def _candidate_outbound_urls(body: str, base_url: str) -> list[str]:
    candidates: list[str] = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        body, flags=re.I | re.S,
    )
    for script in scripts:
        try:
            payload = json.loads(script.strip())
        except json.JSONDecodeError:
            continue
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if not isinstance(value, dict):
                continue
            graph = value.get("@graph") if isinstance(value.get("@graph"), list) else []
            for item in [value, *graph]:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                for key in ("url", "applicationUrl"):
                    if item.get(key):
                        candidates.append(str(item[key]))
    for match in re.finditer(r'<a\b([^>]*?)href=["\']([^"\']+)["\']([^>]*)>(.*?)</a>', body, re.I | re.S):
        context = clean_text(" ".join((match.group(1), match.group(3), match.group(4)))).lower()
        if any(marker in context for marker in ("apply", "application", "original job", "company site")):
            candidates.append(match.group(2))
    result = []
    for candidate in candidates:
        safe = sanitize_public_url(urljoin(base_url, candidate))
        if safe and safe != sanitize_public_url(base_url) and safe not in result:
            result.append(safe)
    return result


def _prefer_outbound_url(candidates: list[str], provider_url: str) -> str:
    provider_host = urlparse(provider_url).hostname or ""
    external = [url for url in candidates if (urlparse(url).hostname or "") != provider_host]
    if external:
        return external[0]
    redirect_paths = ("/apply", "/application", "/redirect", "/out", "/land/")
    return next((url for url in candidates if any(part in urlparse(url).path.lower() for part in redirect_paths)), "")


def enrich_from_job_page(job: Job, body: str, final_url: str) -> bool:
    """Use a canonical page's JobPosting JSON-LD when it is fuller than the source excerpt."""
    safe_final = sanitize_public_url(final_url)
    job.final_url = safe_final
    job.canonical_url = safe_final or job.canonical_url or job.url
    job.description_retrieval_status = "no_structured_job_description"
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        body, flags=re.I | re.S,
    )
    candidates: list[dict] = []
    for script in scripts:
        try:
            payload = json.loads(script.strip())
        except json.JSONDecodeError:
            continue
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if isinstance(value, dict) and value.get("@type") == "JobPosting":
                candidates.append(value)
            if isinstance(value, dict) and isinstance(value.get("@graph"), list):
                candidates.extend(item for item in value["@graph"] if isinstance(item, dict) and item.get("@type") == "JobPosting")
    descriptions = [clean_text(item.get("description")) for item in candidates]
    fullest = max(descriptions, key=len, default="")
    canonical_location = next((_structured_location(item) for item in candidates if _structured_location(item)), "")
    if canonical_location:
        job.canonical_location = canonical_location
    if fullest and len(fullest) > len(clean_text(job.description)):
        job.description = fullest
        job.description_provenance = "employer_job_page"
        job.description_retrieval_status = "enriched"
        job.description_retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        organization = next((item.get("hiringOrganization") for item in candidates if item.get("hiringOrganization")), None)
        if isinstance(organization, dict) and clean_text(organization.get("name")):
            organization_name = clean_text(organization.get("name"))
            provider_self_attribution = (
                job.source_type == "aggregator"
                and organization_name.casefold() == clean_text(job.source).casefold()
            )
            if not provider_self_attribution:
                job.canonical_employer = organization_name
                job.company = organization_name
        return True
    return False


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


def check_job_active(job: Job, timeout: int = DEFAULT_TIMEOUT_SECONDS, budget: RequestBudget | None = None) -> str:
    budget = budget or RequestBudget()
    if not job.url or not job.url.startswith(("http://", "https://")):
        job.canonical_resolution_status = "unsupported_verification_source"
        return _set_verification(job, "unverified", "unsupported_verification_source")

    def fetch(url: str, stage: str) -> tuple[int, str, str]:
        job.verification_stage = stage
        response = safe_http_get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
            budget=budget,
            timeout=timeout,
            maximum_bytes=MAX_JOB_PAGE_BYTES,
        )
        job.verification_attempted_host = response.final_hostname
        job.verification_http_class = f"{response.status // 100}xx"
        if stage == "source":
            job.verification_original_host = response.final_hostname
        return response.status, response.body, response.final_url

    try:
        status_code, body, final_url = fetch(job.url, "source")
        if status_code in BLOCKED_HTTP_CODES:
            return _set_verification(job, "unverified", "http_blocked")
        if status_code in (404, 410):
            return _set_verification(job, "closed", "http_not_found")
        if status_code < 200 or status_code >= 400:
            return _set_verification(job, "unverified", "page_inaccessible")
        if not clean_text(body):
            job.canonical_resolution_status = "canonical_destination_unresolved"
            return _set_verification(job, "unverified", "malformed_page")
        if redirected_to_listing_index(job.url, final_url):
            job.canonical_resolution_status = "redirect_to_generic_index"
            return _set_verification(job, "closed", "redirect_to_generic_index")

        original_host = urlparse(job.url).hostname or ""
        final_host = urlparse(final_url).hostname or ""
        canonical_destination = final_url if final_host and final_host != original_host else ""
        if job.source_type in ("aggregator", "job_board") and not canonical_destination:
            outbound = _prefer_outbound_url(_candidate_outbound_urls(body, final_url or job.url), final_url or job.url)
            if outbound:
                status_code, body, final_url = fetch(outbound, "canonical")
                if status_code in BLOCKED_HTTP_CODES:
                    job.canonical_resolution_status = "canonical_destination_unresolved"
                    return _set_verification(job, "unverified", "http_blocked")
                if status_code in (404, 410):
                    job.canonical_resolution_status = "resolved_employer_page"
                    job.canonical_employer_url = sanitize_public_url(outbound)
                    return _set_verification(job, "closed", "http_not_found")
                if status_code < 200 or status_code >= 400:
                    job.canonical_resolution_status = "canonical_destination_unresolved"
                    return _set_verification(job, "unverified", "page_inaccessible")
                canonical_destination = final_url
            else:
                job.canonical_resolution_status = "canonical_employer_url_missing"
                job.description_retrieval_status = "no_structured_job_description"
                reason = "aggregator_page_only" if job.source_type == "aggregator" else "canonical_employer_url_missing"
                return _set_verification(job, "unverified", reason)

        if canonical_destination or job.source_type == "direct_employer":
            job.canonical_employer_url = sanitize_public_url(canonical_destination or final_url or job.url)
            job.canonical_resolution_status = "resolved_employer_page"
        else:
            job.canonical_resolution_status = "canonical_destination_unresolved"
        enrich_from_job_page(job, body, final_url)
        classified = classify_active_response(status_code, body)
        if classified == "active":
            return _set_verification(job, "active", "verified_active")
        if classified == "closed":
            return _set_verification(job, "closed", "verified_closed")
        if job.description_retrieval_status == "no_structured_job_description":
            return _set_verification(job, "unverified", "no_structured_job_description")
        return _set_verification(job, "unverified", "canonical_destination_unresolved")
    except UnsafeDestination:
        job.verification_stage = job.verification_stage if job.verification_stage != "not_attempted" else "source"
        return _set_verification(job, "unverified", "unsafe_destination")
    except RequestBudgetExhausted:
        return _set_verification(job, "unverified", "verification_budget_exhausted")
    except (RedirectLimitExceeded, RedirectLoop):
        return _set_verification(job, "unverified", "canonical_destination_unresolved")
    except ResponseTooLarge:
        job.description_retrieval_status = "page_too_large"
        return _set_verification(job, "unverified", "page_too_large")
    except (TimeoutError, socket.timeout):
        return _set_verification(job, "unverified", "timeout")
    except (ValueError, UnicodeError):
        return _set_verification(job, "unverified", "malformed_page")
    except Exception:
        return _set_verification(job, "unverified", "other_sanitized_failure")


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


def apply_freshness(
    job: Job,
    preferred_age_days: int = 7,
    now: datetime | None = None,
) -> None:
    """Label age for ranking without rejecting an older vacancy."""
    published = published_datetime(job.published_at)
    if not published:
        job.listing_age_days = None
        job.freshness = "unknown"
        return
    current = now or datetime.now(timezone.utc)
    job.listing_age_days = max(0, (current - published).days)
    if job.listing_age_days <= preferred_age_days:
        job.freshness = "fresh"
    elif job.listing_age_days <= 30:
        job.freshness = "recent"
    else:
        job.freshness = "older"


def retain_active_jobs(
    jobs: list[Job],
    workers: int = 8,
    maximum_age_days: int = 0,
    require_verified_active: bool = False,
    preferred_age_days: int = 7,
    maximum_jobs: int = MAX_VERIFICATION_JOBS,
    request_budget: int = 300,
    return_metrics: bool = False,
) -> tuple[list[Job], int, int] | tuple[list[Job], int, int, dict[str, int]]:
    """Live-check jobs and optionally retain only verified-open vacancies."""
    if not jobs:
        empty = ([], 0, 0)
        return (*empty, {"jobs_submitted": 0, "requests_attempted": 0, "redirects_followed": 0, "unsafe_destinations": 0, "budget_exhausted": 0}) if return_metrics else empty
    recent = []
    stale = []
    for job in jobs:
        apply_freshness(job, preferred_age_days=preferred_age_days)
        if is_stale(job, maximum_age_days):
            job.active_status = "closed"
            job.verification_reason = "verified_closed"
            job.canonical_resolution_status = "verification_not_attempted"
            job.verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            stale.append(job)
        else:
            recent.append(job)
    submitted, overflow = recent[:maximum_jobs], recent[maximum_jobs:]
    for job in overflow:
        job.active_status = "unverified"
        job.verification_reason = "verification_budget_exhausted"
        job.canonical_resolution_status = "verification_not_attempted"
        job.verification_stage = "not_attempted"
        job.verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    budget = RequestBudget(request_budget)
    with ThreadPoolExecutor(max_workers=min(workers, len(submitted) or 1)) as executor:
        futures = {executor.submit(check_job_active, job, DEFAULT_TIMEOUT_SECONDS, budget): job for job in submitted}
        for future in as_completed(futures):
            job = futures[future]
            try:
                job.active_status = future.result()
            except Exception:
                job.active_status = "unverified"
                job.verification_reason = "other_sanitized_failure"
            if job.verification_reason in ("verification_not_attempted", "not_checked", ""):
                job.verification_reason = {
                    "active": "verified_active",
                    "closed": "verified_closed",
                    "unverified": "other_sanitized_failure",
                }[job.active_status]
            job.verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    unverified_count = sum(job.active_status == "unverified" for job in recent)
    if require_verified_active:
        active = [job for job in recent if job.active_status == "active"]
    else:
        active = [job for job in recent if job.active_status != "closed"]
    closed_count = len(stale) + sum(job.active_status == "closed" for job in recent)
    result = (active, closed_count, unverified_count)
    if not return_metrics:
        return result
    metrics = {
        "jobs_submitted": len(submitted),
        "requests_attempted": budget.requests_attempted,
        "redirects_followed": budget.redirects_followed,
        "unsafe_destinations": budget.unsafe_destinations,
        "budget_exhausted": sum(job.verification_reason == "verification_budget_exhausted" for job in recent),
    }
    return (*result, metrics)
