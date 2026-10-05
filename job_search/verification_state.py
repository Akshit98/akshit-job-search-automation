from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .active import apply_freshness
from .core import Job, clean_text, SENSITIVE_URL_KEYS, TRACKING_URL_KEYS

SCHEMA_VERSION = 2
MAX_COUNTER = 1_000_000
SAFE_URL_SENTINEL = "unsafe-or-unvalidated-url"
ALLOWED_ENTRY_FIELDS = {
    "last_outcome", "last_attempted_at", "next_eligible_at",
    "consecutive_failures", "times_deferred", "times_scheduled",
    "last_scheduled_at", "source_signature_hash", "source_signature_hashes",
}
TRANSIENT_OUTCOMES = {
    "timeout", "page_inaccessible", "canonical_destination_unresolved",
    "malformed_page", "page_too_large", "other_sanitized_failure",
}
ALLOWED_OUTCOMES = TRANSIENT_OUTCOMES | {
    "http_blocked", "unsafe_destination", "verified_active", "verified_closed",
    "verification_budget_exhausted", "http_not_found", "aggregator_page_only",
    "canonical_employer_url_missing", "no_structured_job_description",
    "redirect_to_generic_index", "unsupported_verification_source",
}
HTTP_BLOCKED_BACKOFF_DAYS = (3, 7, 14, 30)
TRANSIENT_BACKOFF_DAYS = (1, 2, 4, 7, 14, 30)
CLOSED_RECHECK_DAYS = 60
UNSAFE_RECHECK_DAYS = 30


class VerificationStateError(RuntimeError):
    """Sanitized persistent-state failure safe for operational logs."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _safe_state_url(value: str, *, validated: bool) -> str:
    """Return a safe URL identity or a fixed sentinel without doing DNS."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return SAFE_URL_SENTINEL
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return SAFE_URL_SENTINEL
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host == "metadata.google.internal":
        return SAFE_URL_SENTINEL
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not validated:
            return SAFE_URL_SENTINEL
    else:
        if not address.is_global:
            return SAFE_URL_SENTINEL
    if not validated:
        return SAFE_URL_SENTINEL
    try:
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        authority = host + (f":{parsed.port}" if parsed.port and parsed.port != default_port else "")
    except ValueError:
        return SAFE_URL_SENTINEL
    query = sorted(
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(secret in key.lower() for secret in SENSITIVE_URL_KEYS)
        and key.lower() not in TRACKING_URL_KEYS
    )
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path.rstrip("/"), urlencode(query), ""))


def _official_url(job: Job) -> str:
    value = job.canonical_employer_url or (job.url if job.source_type == "direct_employer" else "")
    try:
        hostname = (urlsplit(value).hostname or "").casefold()
    except ValueError:
        hostname = ""
    # Collection-time source authority and canonical metadata are not network
    # validation. The verifier records the host only after safe_http_get has
    # accepted it as a public destination; retain_active_jobs then timestamps
    # that attempt. Freshly collected jobs intentionally have neither field.
    validated = bool(
        value and hostname and job.verified_at
        and job.verification_stage in {"source", "canonical"}
        and clean_text(job.verification_attempted_host).casefold() == hostname
    )
    safe = _safe_state_url(value, validated=validated)
    return "" if safe == SAFE_URL_SENTINEL else safe


def _alias_hash(kind: str, *parts: str) -> str:
    material = (f"alias:v2:{kind}", *(clean_text(part).casefold() for part in parts))
    return _sha256("|".join(material))


def source_alias_hash(job: Job) -> str:
    return _alias_hash("source", job.source_id or job.source, job.id)


def ats_alias_hash(job: Job) -> str:
    return _alias_hash("ats", job.ats_board_id, job.id) if job.ats_board_id and job.id else ""


def official_url_alias_hash(job: Job) -> str:
    safe = _official_url(job)
    return _alias_hash("official-url", safe) if safe else ""


def job_alias_hashes(job: Job) -> set[str]:
    return {value for value in (source_alias_hash(job), ats_alias_hash(job), official_url_alias_hash(job)) if value}


def _preferred_identity(job: Job) -> str:
    return ats_alias_hash(job) or official_url_alias_hash(job) or source_alias_hash(job)


def stable_identity_hash(job: Job, state: dict[str, Any] | None = None) -> str:
    aliases = (state or {}).get("aliases", {})
    resolved = {aliases[a] for a in job_alias_hashes(job) if _is_hash(aliases.get(a))}
    return sorted(resolved)[0] if resolved else _preferred_identity(job)


def source_signature_hash(job: Job) -> str:
    # Runtime URL-validation evidence is deliberately not part of the source
    # signature: it is absent on the next day's fresh source object. Keeping the
    # sentinel here prevents both raw URL hashing and cache self-invalidation.
    safe_url = _safe_state_url(job.original_source_url or job.url or job.canonical_url, validated=False)
    return _sha256("|".join((
        "signature:v2", clean_text(job.source_id or job.source).casefold(),
        clean_text(job.id).casefold(), safe_url, clean_text(job.ats_board_id).casefold(),
    )))


def empty_state() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "jobs": {}, "aliases": {}}


def _safe_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    outcome = entry.get("last_outcome")
    if outcome is not None and outcome not in ALLOWED_OUTCOMES:
        return None
    safe: dict[str, Any] = {}
    if outcome:
        safe["last_outcome"] = outcome
    for field in ("last_attempted_at", "next_eligible_at", "last_scheduled_at"):
        if entry.get(field) is None:
            continue
        parsed = _parse_timestamp(entry[field])
        if not parsed:
            return None
        safe[field] = parsed.isoformat(timespec="seconds")
    for field in ("consecutive_failures", "times_deferred", "times_scheduled"):
        if entry.get(field) is None:
            continue
        value = entry[field]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_COUNTER:
            return None
        safe[field] = value
    if entry.get("source_signature_hash") is not None:
        if not _is_hash(entry["source_signature_hash"]):
            return None
        safe["source_signature_hash"] = entry["source_signature_hash"]
    if entry.get("source_signature_hashes") is not None:
        values = entry["source_signature_hashes"]
        if not isinstance(values, list) or len(values) > 32 or not all(_is_hash(value) for value in values):
            return None
        safe["source_signature_hashes"] = sorted(set(values))
    return safe


def load_verification_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise VerificationStateError("verification state is unreadable or invalid") from exc
    if not isinstance(payload, dict):
        raise VerificationStateError("verification state has an invalid top-level structure")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise VerificationStateError("verification state schema is unsupported")
    if not isinstance(payload.get("jobs"), dict) or not isinstance(payload.get("aliases"), dict):
        raise VerificationStateError("verification state is missing required object fields")
    state = empty_state()
    discarded = 0
    for identity, entry in payload["jobs"].items():
        safe = _safe_entry(entry) if _is_hash(identity) and isinstance(entry, dict) else None
        if safe is None:
            discarded += 1
        else:
            state["jobs"][identity] = safe
    for alias, identity in payload["aliases"].items():
        if _is_hash(alias) and _is_hash(identity):
            state["aliases"][alias] = identity
        else:
            discarded += 1
    state["invalid_entries_discarded"] = discarded
    return state


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def write_verification_state(path: Path, state: dict[str, Any]) -> None:
    safe = empty_state()
    for identity, entry in state.get("jobs", {}).items():
        sanitized = _safe_entry(entry) if _is_hash(identity) and isinstance(entry, dict) else None
        if sanitized is not None:
            safe["jobs"][identity] = sanitized
    for alias, identity in state.get("aliases", {}).items():
        if _is_hash(alias) and _is_hash(identity):
            safe["aliases"][alias] = identity
    atomic_write_json(path, safe)


def _latest(left: str | None, right: str | None) -> str | None:
    values = [value for value in (left, right) if _parse_timestamp(value or "")]
    return max(values, key=lambda value: _parse_timestamp(value)) if values else None


def _merge_entries(entries: list[dict[str, Any]], signature: str) -> dict[str, Any]:
    if not entries:
        return {"source_signature_hash": signature}
    rank = {"unsafe_destination": 7, "http_blocked": 6, "timeout": 5,
            "other_sanitized_failure": 5, "verification_budget_exhausted": 4,
            "verified_closed": 3, "verified_active": 1}
    chosen = max(entries, key=lambda item: (
        rank.get(item.get("last_outcome", ""), 5),
        _parse_timestamp(item.get("last_attempted_at", "")) or datetime.min.replace(tzinfo=timezone.utc),
    ))
    merged = dict(chosen)
    for field in ("consecutive_failures", "times_deferred", "times_scheduled"):
        merged[field] = max(int(item.get(field, 0)) for item in entries)
    for field in ("last_attempted_at", "next_eligible_at", "last_scheduled_at"):
        value = None
        for item in entries:
            value = _latest(value, item.get(field))
        if value:
            merged[field] = value
        else:
            merged.pop(field, None)
    merged["source_signature_hash"] = signature
    merged["source_signature_hashes"] = sorted({
        value for item in entries
        for value in ([item.get("source_signature_hash")] + list(item.get("source_signature_hashes", [])))
        if _is_hash(value)
    } | {signature})
    return merged


def register_duplicate_aliases(state: dict[str, Any], duplicate_pairs: list[tuple[Job, Job]]) -> int:
    """Bind aliases only for pairs already proven equivalent by canonicalization."""
    migrated = 0
    for loser, winner in duplicate_pairs:
        ats = {v for job in (loser, winner) if (v := ats_alias_hash(job))}
        official_urls = {v for job in (loser, winner) if (v := official_url_alias_hash(job))}
        if len(ats) > 1 or len(official_urls) > 1:
            continue
        aliases = job_alias_hashes(loser) | job_alias_hashes(winner)
        existing = {state["aliases"][a] for a in aliases if _is_hash(state.get("aliases", {}).get(a))}
        preferred = (ats_alias_hash(winner) or ats_alias_hash(loser) or
                     official_url_alias_hash(winner) or official_url_alias_hash(loser) or _preferred_identity(winner))
        identities = existing | {stable_identity_hash(loser, state), stable_identity_hash(winner, state), preferred}
        entries = [state["jobs"].pop(identity) for identity in identities if identity in state["jobs"]]
        if entries:
            state["jobs"][preferred] = _merge_entries(entries, source_signature_hash(winner))
            migrated += 1
        for alias in aliases | identities:
            state["aliases"][alias] = preferred
        for alias, identity in list(state["aliases"].items()):
            if identity in identities:
                state["aliases"][alias] = preferred
    return migrated


def strong_capable(job: Job) -> bool:
    return job.screening_score >= 45 and not job.hard_excluded and job.evidence_quality != "insufficient"


def _score_band(score: int) -> int:
    if score >= 50: return 0
    if score >= 45: return 1
    if score >= 40: return 2
    if score >= 35: return 3
    return 4


def _freshness_rank(days: int | None) -> int:
    if days is None: return 5
    if days <= 7: return 0
    if days <= 30: return 1
    if days <= 60: return 2
    if days <= 90: return 3
    return 4


def _retry_rank(job: Job, entry: dict[str, Any] | None) -> int:
    if not entry: return 0 if job.is_new else 2
    if entry.get("last_outcome") == "verification_budget_exhausted": return 1
    return 3


def verification_priority_key(job: Job, entry: dict[str, Any] | None = None) -> tuple:
    evidence_rank = {"sufficient": 0, "partial": 1, "insufficient": 2}
    source_rank = {"direct_employer": 0, "job_board": 1, "aggregator": 2}
    location_rank = {"remote_india": 0, "hyderabad": 1}
    compensation_rank = 0 if job.compensation_source == "employer_posted" and job.compensation_assessment == "meets_target" else 1
    last_scheduled = _parse_timestamp(entry.get("last_scheduled_at", "")) if entry else None
    return (
        1 if entry and entry.get("last_outcome") in {"verified_closed", "unsafe_destination"} else 0,
        0 if strong_capable(job) else 1, _score_band(job.screening_score),
        _retry_rank(job, entry), -job.screening_score, _freshness_rank(job.listing_age_days),
        job.listing_age_days if job.listing_age_days is not None else 10**9,
        0 if job.is_new else 1, evidence_rank.get(job.evidence_quality, 2),
        source_rank.get(job.source_type, 3), compensation_rank,
        location_rank.get(job.location_tier, 2),
        0 if not entry or int(entry.get("times_scheduled", 0)) == 0 else 1,
        last_scheduled or datetime.min.replace(tzinfo=timezone.utc),
        int(entry.get("times_scheduled", 0)) if entry else 0,
        -int(entry.get("times_deferred", 0)) if entry else 0,
        stable_identity_hash(job),
    )


def _current_entry(state: dict[str, Any], job: Job) -> tuple[str, dict[str, Any] | None, str]:
    identity = stable_identity_hash(job, state)
    signature = source_signature_hash(job)
    entry = state.setdefault("jobs", {}).get(identity)
    known_signatures = set(entry.get("source_signature_hashes", [])) if entry else set()
    if entry and entry.get("source_signature_hash") != signature and signature not in known_signatures:
        state["jobs"].pop(identity, None)
        entry = None
    for alias in job_alias_hashes(job):
        state.setdefault("aliases", {})[alias] = identity
    return identity, entry, signature


def plan_verification(jobs: list[Job], state: dict[str, Any], *, now: datetime | None = None,
                      maximum_jobs: int = 100, preferred_age_days: int = 7) -> dict[str, Any]:
    current = _utc(now)
    eligible: list[tuple[Job, dict[str, Any] | None]] = []
    cached_active, backoff, cold, closed_dormant = [], [], [], []
    closed_rechecks_due = 0
    for job in jobs:
        apply_freshness(job, preferred_age_days=preferred_age_days, now=current)
        _identity, entry, _signature = _current_entry(state, job)
        next_eligible = _parse_timestamp(entry.get("next_eligible_at", "")) if entry else None
        if entry and entry.get("last_outcome") == "verified_closed" and next_eligible and current < next_eligible:
            job.active_status, job.verification_reason = "closed", "verified_closed"
            job.verification_schedule_status = "closed_dormant"
            closed_dormant.append(job); continue
        if entry and entry.get("last_outcome") == "verified_closed": closed_rechecks_due += 1
        last_attempt = _parse_timestamp(entry.get("last_attempted_at", "")) if entry else None
        if (entry and entry.get("last_outcome") == "verified_active" and
                job.source_type == "direct_employer" and last_attempt and
                current < last_attempt + timedelta(days=7)):
            job.active_status, job.verification_reason = "active", "cached_verified_active"
            job.verification_schedule_status, job.verified_at = "cached_active", entry.get("last_attempted_at", "")
            cached_active.append(job); continue
        if job.source_type == "aggregator" and job.listing_age_days is not None and job.listing_age_days > 365:
            job.active_status = "unverified"
            job.verification_reason = entry.get("last_outcome", "verification_not_attempted") if entry else "verification_not_attempted"
            job.verification_schedule_status = "cold_backlog"; cold.append(job); continue
        if next_eligible and current < next_eligible:
            job.active_status = "unverified"
            job.verification_reason = str(entry.get("last_outcome") or "other_sanitized_failure")
            job.verification_schedule_status = "backoff"; backoff.append(job); continue
        eligible.append((job, entry))
    eligible.sort(key=lambda item: verification_priority_key(item[0], item[1]))
    selected_pairs = list(eligible[:maximum_jobs])
    selected_ids = {stable_identity_hash(job, state) for job, _ in selected_pairs}
    omitted = [pair for pair in eligible[maximum_jobs:] if pair[0].source_type == "direct_employer"
               and strong_capable(pair[0]) and not (pair[1] and pair[1].get("last_outcome") == "verified_closed")]
    for direct_pair in omitted:
        replace_at = next((i for i in range(len(selected_pairs)-1, -1, -1)
                           if not strong_capable(selected_pairs[i][0])), None)
        if replace_at is None: break
        selected_ids.discard(stable_identity_hash(selected_pairs[replace_at][0], state))
        selected_pairs[replace_at] = direct_pair
        selected_ids.add(stable_identity_hash(direct_pair[0], state))
    selected_pairs.sort(key=lambda item: verification_priority_key(item[0], item[1]))
    selected = [job for job, _ in selected_pairs]
    deferred = [job for job, _ in eligible if stable_identity_hash(job, state) not in selected_ids]
    for job in selected: job.verification_schedule_status = "submitted"
    for job in deferred:
        job.active_status, job.verification_reason = "unverified", "verification_budget_exhausted"
        job.verification_schedule_status = "deferred_by_cap"
    return {
        "priority_pool": [job for job, _ in eligible],
        "priority_snapshot": [{"id": j.id, "title": j.title, "company": j.company,
            "screening_score": j.screening_score, "listing_age_days": j.listing_age_days,
            "is_new": j.is_new, "source_type": j.source_type,
            "evidence_quality": j.evidence_quality, "strong_capable": strong_capable(j)} for j, _ in eligible],
        "selected": selected, "deferred": deferred, "backoff": backoff,
        "cached_active": cached_active, "cold": cold, "closed_dormant": closed_dormant,
        "closed_rechecks_due": closed_rechecks_due,
    }


def _backoff_days(sequence: tuple[int, ...], failures: int) -> int:
    return sequence[min(max(failures, 1) - 1, len(sequence) - 1)]


def update_verification_state(state: dict[str, Any], *, attempted: list[Job], deferred: list[Job],
                              now: datetime | None = None) -> dict[str, Any]:
    current = _utc(now); timestamp = current.isoformat(timespec="seconds")
    jobs_state = state.setdefault("jobs", {})
    for job in deferred:
        identity, entry, signature = _current_entry(state, job); entry = dict(entry or {})
        entry.update({"last_outcome": "verification_budget_exhausted",
                      "times_deferred": int(entry.get("times_deferred", 0)) + 1,
            "source_signature_hash": signature,
            "source_signature_hashes": sorted(set(entry.get("source_signature_hashes", [])) | {signature})})
        entry.pop("next_eligible_at", None); jobs_state[identity] = entry
    for job in attempted:
        identity, entry, signature = _current_entry(state, job); entry = dict(entry or {})
        entry["times_scheduled"] = int(entry.get("times_scheduled", 0)) + 1
        entry["last_scheduled_at"] = timestamp
        failures = int(entry.get("consecutive_failures", 0)); outcome = job.verification_reason
        next_eligible = None; attempted_http = outcome != "verification_budget_exhausted"
        if outcome == "http_blocked":
            failures += 1; next_eligible = current + timedelta(days=_backoff_days(HTTP_BLOCKED_BACKOFF_DAYS, failures))
        elif outcome in TRANSIENT_OUTCOMES:
            failures += 1; next_eligible = current + timedelta(days=_backoff_days(TRANSIENT_BACKOFF_DAYS, failures))
        elif outcome == "verified_active": failures = 0; next_eligible = current + timedelta(days=7)
        elif outcome == "verified_closed": failures = 0; next_eligible = current + timedelta(days=CLOSED_RECHECK_DAYS)
        elif outcome == "unsafe_destination":
            failures += 1; next_eligible = current + timedelta(days=UNSAFE_RECHECK_DAYS)
        elif outcome == "verification_budget_exhausted": entry["times_deferred"] = int(entry.get("times_deferred", 0)) + 1
        else:
            failures += 1; next_eligible = current + timedelta(days=_backoff_days(TRANSIENT_BACKOFF_DAYS, failures))
        entry.update({"last_outcome": outcome, "consecutive_failures": failures,
                      "source_signature_hash": signature,
                      "source_signature_hashes": sorted(set(entry.get("source_signature_hashes", [])) | {signature})})
        if attempted_http: entry["last_attempted_at"] = timestamp
        if next_eligible: entry["next_eligible_at"] = next_eligible.isoformat(timespec="seconds")
        else: entry.pop("next_eligible_at", None)
        jobs_state[identity] = entry
    return state


def assign_review_dispositions(review_jobs: list[Job], state: dict[str, Any], *, human_limit: int = 20):
    cold = [j for j in review_jobs if j.source_type == "aggregator" and j.active_status == "unverified" and (j.listing_age_days or 0) > 365]
    normal = [j for j in review_jobs if j not in cold]
    ranked = sorted(normal, key=lambda j: verification_priority_key(j, state.get("jobs", {}).get(stable_identity_hash(j, state))))
    mandatory = [j for j in ranked if j.active_status == "active" or strong_capable(j)]
    human_ids = {stable_identity_hash(j, state) for j in mandatory}
    for job in ranked:
        if len(human_ids) >= max(human_limit, len(mandatory)): break
        human_ids.add(stable_identity_hash(job, state))
    human = [j for j in ranked if stable_identity_hash(j, state) in human_ids]
    backlog = [j for j in ranked if stable_identity_hash(j, state) not in human_ids]
    for job in human: job.review_disposition = "human_review"
    for job in backlog: job.review_disposition = "verification_backlog"
    for job in cold: job.review_disposition = "cold_verification_backlog"
    return human, backlog, sorted(cold, key=lambda j: verification_priority_key(j, state.get("jobs", {}).get(stable_identity_hash(j, state))))
