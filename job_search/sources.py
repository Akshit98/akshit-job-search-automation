from __future__ import annotations

import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .core import Job, clean_text


USER_AGENT = "AkshitJobSearch/2.0 (+personal job research)"


def get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
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


def collect(config: dict[str, list[str]]) -> tuple[list[Job], list[str]]:
    jobs, errors = [], []
    handlers = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
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
