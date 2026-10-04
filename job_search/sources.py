from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .core import Job, clean_text, prefer_canonical_job, sanitize_public_url


USER_AGENT = "AkshitJobSearch/2.0 (+personal job research)"
DEFAULT_QUERIES = ("operations analyst", "data quality", "market research", "sales operations", "gtm operations")


@dataclass
class SourceDiagnostic:
    source_id: str
    source_type: str
    status: str
    attempted: bool = False
    jobs_returned: int = 0
    jobs_retained: int = 0
    reason: str = ""
    exact_id_duplicates: int = 0
    passed_basic_eligibility: int = 0
    rejected_by_eligibility: int = 0
    hard_excluded: int = 0
    duplicate_non_canonical: int = 0
    submitted_for_verification: int = 0
    verified_active: int = 0
    verified_closed: int = 0
    verification_unresolved: int = 0
    strong_shortlist: int = 0
    review_queue: int = 0
    suppressed: int = 0
    final_human_visible: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _identifier(value: str | dict[str, Any], key: str = "board_id") -> str:
    if isinstance(value, dict):
        return str(value.get(key) or value.get("identifier") or value.get("id") or "")
    return str(value)


def _queries(value: str | dict[str, Any]) -> tuple[str, ...]:
    if isinstance(value, dict) and value.get("queries"):
        return tuple(value["queries"])
    return DEFAULT_QUERIES


def _apply_metadata(jobs: list[Job], metadata: str | dict[str, Any], source_name: str) -> list[Job]:
    meta = metadata if isinstance(metadata, dict) else {}
    board_id = _identifier(metadata)
    source_id = str(meta.get("id") or f"{source_name}/{board_id or 'public'}")
    for job in jobs:
        default_type = "direct_employer" if source_name in ("greenhouse", "lever", "ashby") else "aggregator" if source_name in ("adzuna", "jooble") else "job_board"
        job.source_type = str(meta.get("source_type") or default_type)
        employer = clean_text(meta.get("employer_name"))
        if employer and job.source_type != "aggregator":
            job.company = employer
        provider = clean_text(meta.get("provider_name"))
        if provider:
            job.source = provider
        job.canonical_employer = job.company
        job.source_id = source_id
        job.ats_board_id = board_id if source_name in ("greenhouse", "lever", "ashby") else ""
        job.source_priority = int(meta.get("source_priority", 100 if job.source_type == "direct_employer" else 50 if job.source_type == "job_board" else 20))
        if meta.get("compensation_provenance"):
            job.compensation_source = str(meta["compensation_provenance"])
        job.url = sanitize_public_url(job.url)
        job.original_source_url = job.url
        job.canonical_url = job.url
        if job.source_type == "direct_employer":
            job.canonical_employer_url = job.url
            job.canonical_resolution_status = "resolved_employer_page"
        elif job.source_type == "aggregator":
            job.canonical_resolution_status = "aggregator_page_only"
        else:
            job.canonical_resolution_status = "canonical_destination_unresolved"
        job.description_provenance = "employer_payload" if job.source_type == "direct_employer" else "aggregator_excerpt" if job.source_type == "aggregator" else "job_board_payload"
        job.source_quality_confidence = "high" if job.source_type == "direct_employer" else "medium" if job.source_type == "job_board" else "low"
    return jobs


def _expect_dict(value: Any, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"unexpected {source} response schema")
    return value


def _expect_list(value: Any, source: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"unexpected {source} response schema")
    return value


def _expect_items(value: Any, source: str) -> list[dict[str, Any]]:
    return [_expect_dict(item, source) for item in _expect_list(value, source)]


def _is_timeout_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: Any = exc
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if isinstance(current, URLError) and isinstance(current.reason, BaseException):
            current = current.reason
            continue
        current = current.__cause__ or current.__context__
    return False


def _lever_description(item: dict[str, Any]) -> str:
    sections = [clean_text(item.get("descriptionPlain"))]
    for section in _expect_items(item.get("lists") or [], "Lever"):
        heading = clean_text(section.get("text"))
        content = clean_text(section.get("content"))
        if heading and content:
            sections.append(f"{heading}: {content}")
        elif content:
            sections.append(content)
    sections.append(clean_text(item.get("additionalPlain")))
    return clean_text(" ".join(section for section in sections if section))


def _lever_advertised_employer(item: dict[str, Any], source_type: str, fallback: str) -> str:
    if source_type != "aggregator":
        return fallback
    organization = item.get("hiringOrganization") or {}
    if organization and not isinstance(organization, dict):
        raise TypeError("unexpected Lever hiring organization schema")
    explicit = clean_text(
        item.get("companyName")
        or item.get("company")
        or organization.get("name")
    )
    if explicit:
        return explicit
    searchable = clean_text(" ".join(
        str(value or "") for value in (
            item.get("descriptionPlain"), item.get("descriptionBodyPlain"),
            item.get("openingPlain"),
            " ".join(str(section.get("content") or "") for section in _expect_items(item.get("lists") or [], "Lever")),
        )
    ))
    match = re.search(r"\bon behalf of\s+([A-Z][A-Za-z0-9&.'() -]{1,80}?)(?=\.|,|;|\s+is\b|\s+seeks\b)", searchable)
    return clean_text(match.group(1)) if match else "Undisclosed employer"


def get_json(url: str, retries: int = 1) -> object:
    for attempt in range(retries + 1):
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, socket.timeout) as exc:
            if attempt >= retries:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise RuntimeError("unreachable")


def post_json(url: str, payload: dict) -> object:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def greenhouse(board: str | dict[str, Any]) -> list[Job]:
    board_id = _identifier(board)
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs?content=true")
    data = _expect_dict(data, "Greenhouse")
    items = _expect_items(data.get("jobs"), "Greenhouse")
    jobs = []
    for item in items:
        location = item.get("location") or {}
        location = _expect_dict(location, "Greenhouse location")
        jobs.append(Job(
            id=f"greenhouse:{board_id}:{item.get('id')}", source="greenhouse", company=board_id,
            title=clean_text(item.get("title")), location=clean_text(location.get("name")),
            workplace="", description=clean_text(item.get("content")), url=item.get("absolute_url", ""),
            published_at=item.get("updated_at", "")
        ))
    return _apply_metadata(jobs, board, "greenhouse")


def lever(site: str | dict[str, Any]) -> list[Job]:
    site_id = _identifier(site)
    data = get_json(f"https://api.lever.co/v0/postings/{site_id}?mode=json")
    items = _expect_items(data, "Lever")
    jobs = []
    meta = site if isinstance(site, dict) else {}
    source_type = str(meta.get("source_type") or "direct_employer")
    for item in items:
        categories = item.get("categories") or {}
        categories = _expect_dict(categories, "Lever categories")
        description = _lever_description(item)
        salary = item.get("salaryRange") or {}
        compensation = clean_text(item.get("salaryDescriptionPlain"))
        if not compensation and salary:
            compensation = f"{salary.get('currency', '')} {salary.get('min', '')}-{salary.get('max', '')} {salary.get('interval', '')}"
        jobs.append(Job(
            id=f"lever:{site_id}:{item.get('id')}", source="lever",
            company=_lever_advertised_employer(item, source_type, site_id),
            title=clean_text(item.get("text")), location=clean_text(categories.get("location")),
            workplace=clean_text(item.get("workplaceType")), description=description,
            url=item.get("hostedUrl", ""), employment_type=clean_text(categories.get("commitment")),
            compensation=compensation, published_at=str(item.get("createdAt", ""))
        ))
    return _apply_metadata(jobs, site, "lever")


def ashby(board: str | dict[str, Any]) -> list[Job]:
    board_id = _identifier(board)
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board_id}")
    data = _expect_dict(data, "Ashby")
    items = _expect_items(data.get("jobs"), "Ashby")
    jobs = []
    for item in items:
        jobs.append(Job(
            id=f"ashby:{board_id}:{item.get('jobUrl') or item.get('title')}", source="ashby", company=board_id,
            title=clean_text(item.get("title")), location=clean_text(item.get("location")),
            workplace=clean_text(item.get("workplaceType")), description=clean_text(item.get("descriptionPlain")),
            url=item.get("jobUrl", ""), employment_type=clean_text(item.get("employmentType")),
            compensation=clean_text(item.get("compensationTierSummary")), published_at=item.get("publishedAt", "")
        ))
    return _apply_metadata(jobs, board, "ashby")


def remoteok(_: str) -> list[Job]:
    data = get_json("https://remoteok.com/api")
    jobs = []
    for item in _expect_items(data, "Remote OK"):
        if not item.get("id"):
            continue
        location = clean_text(item.get("location") or "Worldwide")
        jobs.append(Job(
            id=f"remoteok:{item.get('id')}", source="Remote OK", company=clean_text(item.get("company")),
            title=clean_text(item.get("position")), location=location, workplace="Remote",
            description=clean_text(item.get("description")), url=item.get("url", ""),
            employment_type="Full-time", compensation=clean_text(item.get("salary")),
            published_at=clean_text(item.get("date")),
        ))
    return _apply_metadata(jobs, _, "remoteok")


def remotive(_: str) -> list[Job]:
    data = get_json("https://remotive.com/api/remote-jobs")
    data = _expect_dict(data, "Remotive")
    jobs = []
    for item in _expect_items(data.get("jobs"), "Remotive"):
        jobs.append(Job(
            id=f"remotive:{item.get('id')}", source="Remotive", company=clean_text(item.get("company_name")),
            title=clean_text(item.get("title")), location=clean_text(item.get("candidate_required_location") or "Worldwide"),
            workplace="Remote", description=clean_text(item.get("description")), url=item.get("url", ""),
            employment_type=clean_text(item.get("job_type")), compensation=clean_text(item.get("salary")),
            published_at=clean_text(item.get("publication_date")),
        ))
    return _apply_metadata(jobs, _, "remotive")


def arbeitnow(_: str) -> list[Job]:
    jobs = []
    for page in range(1, 4):
        data = get_json(f"https://arbeitnow.com/api/job-board-api?page={page}")
        data = _expect_dict(data, "Arbeitnow")
        for item in _expect_items(data.get("data"), "Arbeitnow"):
            remote = bool(item.get("remote"))
            jobs.append(Job(
                id=f"arbeitnow:{item.get('slug')}", source="Arbeitnow", company=clean_text(item.get("company_name")),
                title=clean_text(item.get("title")), location=clean_text(item.get("location") or ("Worldwide" if remote else "")),
                workplace="Remote" if remote else "On-site", description=clean_text(item.get("description")),
                url=item.get("url", ""), employment_type="Full-time", published_at=clean_text(item.get("created_at")),
            ))
        if not (data.get("links") or {}).get("next"):
            break
    return _apply_metadata(jobs, _, "arbeitnow")


def himalayas(_: str) -> list[Job]:
    jobs = []
    for query in _queries(_):
        data = get_json("https://himalayas.app/jobs/api/search?" + urlencode({"q": query, "sort": "recent", "page": 1}))
        data = _expect_dict(data, "Himalayas")
        for item in _expect_items(data.get("jobs"), "Himalayas"):
            restrictions = item.get("locationRestrictions") or []
            location_parts = []
            for restriction in restrictions:
                if isinstance(restriction, dict):
                    value = clean_text(restriction.get("name"))
                else:
                    value = clean_text(restriction)
                if value:
                    location_parts.append(value)
            # Missing restrictions are unknown, not worldwide. The evaluator
            # may still accept the role when its description explicitly says
            # work from anywhere or otherwise confirms global eligibility.
            location = ", ".join(location_parts)
            salary = ""
            if item.get("minSalary") is not None or item.get("maxSalary") is not None:
                salary = f"{item.get('currency', '')} {item.get('minSalary', '')}-{item.get('maxSalary', '')} per {item.get('salaryPeriod', 'annual')}"
            jobs.append(Job(
                id=f"himalayas:{item.get('guid')}", source="Himalayas", company=clean_text(item.get("companyName")),
                title=clean_text(item.get("title")), location=location, workplace="Remote",
                description=clean_text(item.get("description") or item.get("excerpt")),
                url=item.get("applicationLink", ""), employment_type=clean_text(item.get("employmentType")),
                compensation=salary, published_at=clean_text(item.get("pubDate")),
            ))
    return _apply_metadata(jobs, _, "himalayas")


def themuse(_: str) -> list[Job]:
    jobs = []
    api_key = os.getenv("THEMUSE_API_KEY", "")
    for page in range(3):
        params = {"page": page, "descending": "true"}
        if api_key:
            params["api_key"] = api_key
        data = get_json("https://www.themuse.com/api/public/jobs?" + urlencode(params))
        data = _expect_dict(data, "The Muse")
        for item in _expect_items(data.get("results"), "The Muse"):
            locations = ", ".join(clean_text(x.get("name")) for x in item.get("locations", []) if isinstance(x, dict))
            levels = ", ".join(clean_text(x.get("name")) for x in item.get("levels", []) if isinstance(x, dict))
            company = item.get("company") or {}
            refs = item.get("refs") or {}
            jobs.append(Job(
                id=f"themuse:{item.get('id')}", source="The Muse", company=clean_text(company.get("name")),
                title=clean_text(item.get("name")), location=locations, workplace="Remote" if "remote" in locations.lower() else "",
                description=clean_text(item.get("contents")), url=refs.get("landing_page", ""),
                employment_type=levels, published_at=clean_text(item.get("publication_date")),
            ))
    return _apply_metadata(jobs, _, "themuse")


def adzuna(_: str) -> list[Job]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []
    jobs = []
    for query in _queries(_):
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50, "what": query, "content-type": "application/json"}
        data = get_json("https://api.adzuna.com/v1/api/jobs/in/search/1?" + urlencode(params))
        data = _expect_dict(data, "Adzuna")
        for item in _expect_items(data.get("results"), "Adzuna"):
            company, location = item.get("company") or {}, item.get("location") or {}
            salary = ""
            if item.get("salary_min") is not None or item.get("salary_max") is not None:
                salary = f"INR {item.get('salary_min', '')}-{item.get('salary_max', '')} per year"
            jobs.append(Job(
                id=f"adzuna:{item.get('id')}", source="Adzuna", company=clean_text(company.get("display_name")),
                title=clean_text(item.get("title")), location=clean_text(location.get("display_name")),
                workplace="Remote" if "remote" in f"{item.get('title')} {item.get('description')}".lower() else "",
                description=clean_text(item.get("description")), url=item.get("redirect_url", ""),
                employment_type=clean_text(item.get("contract_type")), compensation=salary,
                compensation_source="aggregator_estimate",
                published_at=clean_text(item.get("created")),
            ))
    return _apply_metadata(jobs, _, "adzuna")


def jooble(_: str) -> list[Job]:
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        return []
    jobs = []
    for query in _queries(_):
        data = post_json(f"https://jooble.org/api/{api_key}", {"keywords": query, "location": "India", "page": "1", "ResultOnPage": "50"})
        data = _expect_dict(data, "Jooble")
        for item in _expect_items(data.get("jobs"), "Jooble"):
            jobs.append(Job(
                id=f"jooble:{item.get('id') or item.get('link')}", source="Jooble", company=clean_text(item.get("company")),
                title=clean_text(item.get("title")), location=clean_text(item.get("location")),
                workplace="Remote" if "remote" in f"{item.get('title')} {item.get('location')} {item.get('snippet')}".lower() else "",
                description=clean_text(item.get("snippet")), url=item.get("link", ""),
                employment_type=clean_text(item.get("type")), compensation=clean_text(item.get("salary")),
                compensation_source="aggregator_estimate",
                published_at=clean_text(item.get("updated")),
            ))
    return _apply_metadata(jobs, _, "jooble")


def prefer_source_job(existing: Job, candidate: Job) -> Job:
    """Choose the more authoritative duplicate and preserve the fuller employer JD."""
    return prefer_canonical_job(existing, candidate)


def _legacy_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(config.get("sources"), list):
        queries = config.get("queries", {})
        result = []
        for source in config["sources"]:
            item = dict(source)
            item["queries"] = queries.get(item.get("query_group", ""), [])
            result.append(item)
        return result
    return [
        {"id": f"{source}/{identifier}", "type": source, "identifier": identifier, "enabled": True}
        for source, identifiers in config.items() if isinstance(identifiers, list) for identifier in identifiers
    ]


def validate_source_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 2 or not isinstance(config.get("sources"), list):
        raise ValueError("sources.json must use structured schema_version 2")
    required = {"id", "type", "enabled", "source_type"}
    known = {"greenhouse", "lever", "ashby", "remoteok", "remotive", "arbeitnow", "himalayas", "themuse", "adzuna", "jooble"}
    ids: set[str] = set()
    for source in config["sources"]:
        missing = required.difference(source)
        if missing: raise ValueError("Source entry is missing: " + ", ".join(sorted(missing)))
        if source["id"] in ids: raise ValueError(f"Duplicate source id: {source['id']}")
        if source["type"] not in known: raise ValueError(f"Unsupported source type: {source['type']}")
        ids.add(source["id"])
        forbidden_keys = {"api_key", "token", "password", "secret", "app_key"}
        if forbidden_keys.intersection(str(key).lower() for key in source):
            raise ValueError(f"Source {source['id']} appears to contain a secret field")


def collect(config: dict[str, Any], include_diagnostics: bool = False):
    jobs: list[Job] = []
    errors: list[str] = []
    diagnostics: list[SourceDiagnostic] = []
    handlers = {
        "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
        "remoteok": remoteok, "remotive": remotive, "arbeitnow": arbeitnow,
        "himalayas": himalayas, "themuse": themuse, "adzuna": adzuna, "jooble": jooble,
    }
    for metadata in _legacy_sources(config):
        source = str(metadata.get("type"))
        source_id = str(metadata.get("id") or source)
        source_type = str(metadata.get("source_type") or "unknown")
        if not metadata.get("enabled", True):
            diagnostics.append(SourceDiagnostic(source_id, source_type, "disabled", reason="disabled by configuration"))
            continue
        if source not in handlers:
            errors.append(f"Unknown source: {source}")
            diagnostics.append(SourceDiagnostic(source_id, source_type, "schema_parser_error", reason="unknown source type"))
            continue
        required_env = metadata.get("required_env", [])
        missing = [name for name in required_env if not os.getenv(name)]
        if missing:
            diagnostics.append(SourceDiagnostic(source_id, source_type, "missing_credential", reason="required credentials unavailable"))
            continue
        try:
            parsed = handlers[source](metadata)
            jobs.extend(parsed)
            status = "successful" if parsed else "zero_results"
            diagnostics.append(SourceDiagnostic(source_id, source_type, status, attempted=True, jobs_returned=len(parsed), jobs_retained=len(parsed)))
        except HTTPError as exc:
            errors.append(f"{source_id}: HTTP {exc.code}")
            diagnostics.append(SourceDiagnostic(source_id, source_type, "http_failure", attempted=True, reason=f"HTTP {exc.code}"))
        except (URLError, TimeoutError, socket.timeout) as exc:
            timed_out = _is_timeout_error(exc)
            status = "timeout" if timed_out else "http_failure"
            reason = "request timed out" if timed_out else "network request failed"
            errors.append(f"{source_id}: {reason}")
            diagnostics.append(SourceDiagnostic(source_id, source_type, status, attempted=True, reason=reason))
        except json.JSONDecodeError:
            errors.append(f"{source_id}: malformed JSON")
            diagnostics.append(SourceDiagnostic(source_id, source_type, "malformed_response", attempted=True, reason="response was not valid JSON"))
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            errors.append(f"{source_id}: response schema was incompatible")
            diagnostics.append(SourceDiagnostic(source_id, source_type, "schema_parser_error", attempted=True, reason="response schema was incompatible"))
    return (jobs, errors, diagnostics) if include_diagnostics else (jobs, errors)
