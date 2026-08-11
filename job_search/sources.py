from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .core import Job, clean_text


USER_AGENT = "AkshitJobSearch/2.0 (+personal job research)"


def get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> object:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def greenhouse(board: str) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            id=f"greenhouse:{board}:{item.get('id')}", source="greenhouse", company=board,
            title=clean_text(item.get("title")), location=clean_text((item.get("location") or {}).get("name")),
            workplace="", description=clean_text(item.get("content")), url=item.get("absolute_url", ""),
            published_at=item.get("updated_at", "")
        ))
    return jobs


def lever(site: str) -> list[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        description = " ".join([clean_text(item.get("descriptionPlain")), clean_text(item.get("additionalPlain"))])
        salary = item.get("salaryRange") or {}
        compensation = clean_text(item.get("salaryDescriptionPlain"))
        if not compensation and salary:
            compensation = f"{salary.get('currency', '')} {salary.get('min', '')}-{salary.get('max', '')} {salary.get('interval', '')}"
        jobs.append(Job(
            id=f"lever:{site}:{item.get('id')}", source="lever", company=site,
            title=clean_text(item.get("text")), location=clean_text(categories.get("location")),
            workplace=clean_text(item.get("workplaceType")), description=description,
            url=item.get("hostedUrl", ""), employment_type=clean_text(categories.get("commitment")),
            compensation=compensation, published_at=str(item.get("createdAt", ""))
        ))
    return jobs


def ashby(board: str) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            id=f"ashby:{board}:{item.get('jobUrl') or item.get('title')}", source="ashby", company=board,
            title=clean_text(item.get("title")), location=clean_text(item.get("location")),
            workplace=clean_text(item.get("workplaceType")), description=clean_text(item.get("descriptionPlain")),
            url=item.get("jobUrl", ""), employment_type=clean_text(item.get("employmentType")),
            compensation=clean_text(item.get("compensationTierSummary")), published_at=item.get("publishedAt", "")
        ))
    return jobs


def remoteok(_: str) -> list[Job]:
    data = get_json("https://remoteok.com/api")
    jobs = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        location = clean_text(item.get("location") or "Worldwide")
        jobs.append(Job(
            id=f"remoteok:{item.get('id')}", source="Remote OK", company=clean_text(item.get("company")),
            title=clean_text(item.get("position")), location=location, workplace="Remote",
            description=clean_text(item.get("description")), url=item.get("url", ""),
            employment_type="Full-time", compensation=clean_text(item.get("salary")),
            published_at=clean_text(item.get("date")),
        ))
    return jobs


def remotive(_: str) -> list[Job]:
    data = get_json("https://remotive.com/api/remote-jobs")
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            id=f"remotive:{item.get('id')}", source="Remotive", company=clean_text(item.get("company_name")),
            title=clean_text(item.get("title")), location=clean_text(item.get("candidate_required_location") or "Worldwide"),
            workplace="Remote", description=clean_text(item.get("description")), url=item.get("url", ""),
            employment_type=clean_text(item.get("job_type")), compensation=clean_text(item.get("salary")),
            published_at=clean_text(item.get("publication_date")),
        ))
    return jobs


def arbeitnow(_: str) -> list[Job]:
    jobs = []
    for page in range(1, 4):
        data = get_json(f"https://arbeitnow.com/api/job-board-api?page={page}")
        for item in data.get("data", []):
            remote = bool(item.get("remote"))
            jobs.append(Job(
                id=f"arbeitnow:{item.get('slug')}", source="Arbeitnow", company=clean_text(item.get("company_name")),
                title=clean_text(item.get("title")), location=clean_text(item.get("location") or ("Worldwide" if remote else "")),
                workplace="Remote" if remote else "On-site", description=clean_text(item.get("description")),
                url=item.get("url", ""), employment_type="Full-time", published_at=clean_text(item.get("created_at")),
            ))
        if not (data.get("links") or {}).get("next"):
            break
    return jobs


def himalayas(_: str) -> list[Job]:
    jobs = []
    queries = ("operations analyst", "data quality", "market research", "sales operations", "revenue operations")
    for query in queries:
        data = get_json("https://himalayas.app/jobs/api/search?" + urlencode({"q": query, "sort": "recent", "page": 1}))
        for item in data.get("jobs", []):
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
    return jobs


def themuse(_: str) -> list[Job]:
    jobs = []
    api_key = os.getenv("THEMUSE_API_KEY", "")
    for page in range(3):
        params = {"page": page, "descending": "true"}
        if api_key:
            params["api_key"] = api_key
        data = get_json("https://www.themuse.com/api/public/jobs?" + urlencode(params))
        for item in data.get("results", []):
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
    return jobs


def adzuna(_: str) -> list[Job]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []
    jobs = []
    for query in ("operations analyst", "data quality", "market research", "sales operations", "revenue operations"):
        params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50, "what": query, "content-type": "application/json"}
        data = get_json("https://api.adzuna.com/v1/api/jobs/in/search/1?" + urlencode(params))
        for item in data.get("results", []):
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
                published_at=clean_text(item.get("created")),
            ))
    return jobs


def jooble(_: str) -> list[Job]:
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        return []
    jobs = []
    for query in ("operations analyst", "data quality", "market research", "sales operations", "revenue operations"):
        data = post_json(f"https://jooble.org/api/{api_key}", {"keywords": query, "location": "India", "page": "1", "ResultOnPage": "50"})
        for item in data.get("jobs", []):
            jobs.append(Job(
                id=f"jooble:{item.get('id') or item.get('link')}", source="Jooble", company=clean_text(item.get("company")),
                title=clean_text(item.get("title")), location=clean_text(item.get("location")),
                workplace="Remote" if "remote" in f"{item.get('title')} {item.get('location')} {item.get('snippet')}".lower() else "",
                description=clean_text(item.get("snippet")), url=item.get("link", ""),
                employment_type=clean_text(item.get("type")), compensation=clean_text(item.get("salary")),
                published_at=clean_text(item.get("updated")),
            ))
    return jobs


def collect(config: dict[str, list[str]]) -> tuple[list[Job], list[str]]:
    jobs, errors = [], []
    handlers = {
        "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
        "remoteok": remoteok, "remotive": remotive, "arbeitnow": arbeitnow,
        "himalayas": himalayas, "themuse": themuse, "adzuna": adzuna, "jooble": jooble,
    }
    for source, identifiers in config.items():
        if source not in handlers:
            errors.append(f"Unknown source: {source}")
            continue
        for identifier in identifiers:
            try:
                jobs.extend(handlers[source](identifier))
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
                errors.append(f"{source}/{identifier}: {exc}")
    return jobs, errors
