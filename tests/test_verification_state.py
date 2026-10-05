import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_search.core import Job
from job_search.verification_state import (
    ALLOWED_ENTRY_FIELDS, MAX_COUNTER, SAFE_URL_SENTINEL, VerificationStateError,
    _safe_state_url, assign_review_dispositions, atomic_write_json, empty_state,
    job_alias_hashes, load_verification_state, official_url_alias_hash, plan_verification,
    register_duplicate_aliases, source_signature_hash, stable_identity_hash,
    update_verification_state, verification_priority_key, write_verification_state,
)


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def make_job(identifier: str, score: int = 40, *, age: int = 1, new: bool = False,
             source_type: str = "aggregator", evidence: str = "partial",
             location: str = "other_india", title: str = "Operations Analyst") -> Job:
    job = Job(
        identifier, "source", "Example", title, "India", "", "Detailed operations role",
        f"https://example.test/jobs/{identifier}",
        published_at=(NOW - timedelta(days=age)).isoformat(), screening_score=score,
        evidence_quality=evidence, source_type=source_type, source_id="source/main",
        location_tier=location, is_new=new, screening_queue="review_queue",
        listing_age_days=age,
    )
    return job


class VerificationPriorityTests(unittest.TestCase):
    def test_source_order_does_not_control_selection(self):
        low = make_job("low", 30)
        high = make_job("high", 50)
        plan = plan_verification([low, high], empty_state(), now=NOW, maximum_jobs=1)
        self.assertEqual([job.id for job in plan["selected"]], ["high"])

    def test_strong_capable_outranks_score_30(self):
        high = make_job("high", 45)
        low = make_job("low", 30, new=True)
        self.assertLess(verification_priority_key(high), verification_priority_key(low))

    def test_fresh_high_value_new_job_gets_slot(self):
        jobs = [make_job(f"old-{index}", 40, age=100) for index in range(100)]
        fresh = make_job("fresh", 48, age=0, new=True)
        plan = plan_verification(jobs + [fresh], empty_state(), now=NOW, maximum_jobs=100)
        self.assertIn("fresh", [job.id for job in plan["selected"]])

    def test_budget_exhausted_has_next_run_priority_within_value_band(self):
        deferred = make_job("deferred", 40)
        seen = make_job("seen", 40)
        state = empty_state()
        state["jobs"][stable_identity_hash(deferred)] = {
            "last_outcome": "verification_budget_exhausted",
            "times_deferred": 1,
            "source_signature_hash": source_signature_hash(deferred),
        }
        plan = plan_verification([seen, deferred], state, now=NOW, maximum_jobs=1)
        self.assertEqual(plan["selected"][0].id, "deferred")

    def test_location_is_only_a_late_tiebreak(self):
        remote_low = make_job("remote", 39, location="remote_india")
        other_high = make_job("other", 40, location="other_india")
        self.assertLess(verification_priority_key(other_high), verification_priority_key(remote_low))
        remote_tie = make_job("remote-tie", 40, location="remote_india")
        self.assertLess(verification_priority_key(remote_tie), verification_priority_key(other_high))

    def test_stable_identity_is_deterministic_final_tiebreak(self):
        jobs = [make_job("b", 40), make_job("a", 40)]
        first = [job.id for job in plan_verification(jobs, empty_state(), now=NOW)["priority_pool"]]
        second = [job.id for job in plan_verification(list(reversed(jobs)), empty_state(), now=NOW)["priority_pool"]]
        self.assertEqual(first, second)

    def test_due_strong_direct_employer_displaces_only_non_strong_candidate(self):
        normal = [make_job(f"normal-{index}", 40) for index in range(100)]
        direct = make_job("direct", 45, source_type="direct_employer")
        plan = plan_verification(normal + [direct], empty_state(), now=NOW, maximum_jobs=100)
        self.assertIn("direct", [job.id for job in plan["selected"]])
        self.assertEqual(len(plan["selected"]), 100)


class RetryStateTests(unittest.TestCase):
    def _attempt(self, outcome: str, failures: int = 0, job: Job | None = None):
        job = job or make_job("retry")
        state = empty_state()
        if failures:
            state["jobs"][stable_identity_hash(job)] = {
                "last_outcome": outcome,
                "consecutive_failures": failures,
                "source_signature_hash": source_signature_hash(job),
            }
        job.verification_reason = outcome
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        return state["jobs"][stable_identity_hash(job)]

    def test_http_blocked_backoff_intervals(self):
        for prior, expected in ((0, 3), (1, 7), (2, 14), (3, 30), (8, 30)):
            entry = self._attempt("http_blocked", prior)
            next_at = datetime.fromisoformat(entry["next_eligible_at"])
            self.assertEqual((next_at - NOW).days, expected)

    def test_timeout_backoff_sequence(self):
        for prior, expected in ((0, 1), (1, 2), (2, 4), (3, 7), (4, 14), (5, 30), (9, 30)):
            entry = self._attempt("timeout", prior)
            next_at = datetime.fromisoformat(entry["next_eligible_at"])
            self.assertEqual((next_at - NOW).days, expected)

    def test_budget_exhaustion_does_not_increment_failure_count(self):
        job = make_job("deferred")
        state = empty_state()
        state["jobs"][stable_identity_hash(job)] = {
            "last_outcome": "http_blocked", "consecutive_failures": 2,
            "source_signature_hash": source_signature_hash(job),
        }
        update_verification_state(state, attempted=[], deferred=[job], now=NOW)
        entry = state["jobs"][stable_identity_hash(job)]
        self.assertEqual(entry["consecutive_failures"], 2)
        self.assertEqual(entry["times_deferred"], 1)

    def test_unsafe_destination_waits_for_signature_change(self):
        job = make_job("unsafe")
        state = empty_state()
        job.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        self.assertEqual(plan_verification([job], state, now=NOW + timedelta(days=1))["backoff"][0].id, "unsafe")
        job.source_id = "source/changed"
        self.assertEqual(plan_verification([job], state, now=NOW + timedelta(days=100))["selected"][0].id, "unsafe")

    def test_unsafe_destination_rechecks_at_thirty_day_boundary(self):
        job = make_job("unsafe-boundary")
        state = empty_state(); job.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        entry = state["jobs"][stable_identity_hash(job, state)]
        self.assertEqual(datetime.fromisoformat(entry["next_eligible_at"]), NOW + timedelta(days=30))
        self.assertEqual(len(plan_verification([job], state, now=NOW)["backoff"]), 1)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=30, seconds=-1))["backoff"]), 1)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=30))["selected"]), 1)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=30, seconds=1))["selected"]), 1)

    def test_repeated_unsafe_destination_schedules_another_thirty_days(self):
        job = make_job("unsafe-repeat")
        state = empty_state(); job.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        update_verification_state(state, attempted=[job], deferred=[], now=NOW + timedelta(days=30))
        entry = state["jobs"][stable_identity_hash(job, state)]
        self.assertEqual(datetime.fromisoformat(entry["next_eligible_at"]), NOW + timedelta(days=60))
        self.assertEqual(entry["consecutive_failures"], 2)

    def test_due_unsafe_retry_is_below_normal_high_value_job(self):
        unsafe = make_job("unsafe-low", 50)
        state = empty_state(); unsafe.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[unsafe], deferred=[], now=NOW)
        normal = make_job("normal-high", 50, new=True)
        plan = plan_verification([unsafe, normal], state, now=NOW + timedelta(days=30), maximum_jobs=1)
        self.assertEqual(plan["selected"][0].id, "normal-high")

    def test_unsafe_state_contains_no_raw_destination_material(self):
        job = make_job("unsafe-private")
        job.url = job.original_source_url = "http://127.0.0.1/admin?token=TOPSECRET"
        state = empty_state(); job.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        serialized = json.dumps(state)
        for raw in ("127.0.0.1", "admin", "TOPSECRET", "token", "http://"):
            self.assertNotIn(raw, serialized)

    def test_corrected_unvalidated_url_gets_thirty_day_opportunity_without_raw_hashing(self):
        job = make_job("unsafe-corrected")
        job.url = job.original_source_url = "http://169.254.169.254/latest/meta-data"
        unsafe_signature = source_signature_hash(job)
        state = empty_state(); job.verification_reason = "unsafe_destination"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        job.url = job.original_source_url = "https://public-employer.example/jobs/123"
        self.assertEqual(source_signature_hash(job), unsafe_signature)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=29))["backoff"]), 1)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=30))["selected"]), 1)

    def test_source_signature_change_resets_backoff(self):
        job = make_job("changed")
        state = empty_state()
        job.verification_reason = "http_blocked"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        job.source_id = "source/changed"
        plan = plan_verification([job], state, now=NOW + timedelta(days=1))
        self.assertEqual(plan["selected"][0].id, "changed")

    def test_cached_active_expires_after_seven_days(self):
        job = make_job("active", source_type="direct_employer")
        state = empty_state(); job.verification_reason = "verified_active"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=6))["cached_active"]), 1)
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=7))["selected"]), 1)

    def test_direct_ats_active_cache_survives_fresh_daily_objects(self):
        def fresh(identifier="req-cache"):
            job = make_job(identifier, source_type="direct_employer")
            job.source_id = "lever/board"
            job.ats_board_id = "board"
            job.url = job.original_source_url = job.canonical_employer_url = "https://jobs.example.test/req-cache"
            job.canonical_resolution_status = "resolved_employer_page"
            return job

        verified = fresh()
        pre_signature = source_signature_hash(verified)
        verified.verification_reason = "verified_active"
        verified.active_status = "active"
        verified.verification_stage = "source"
        verified.verification_attempted_host = "jobs.example.test"
        verified.verified_at = NOW.isoformat()
        self.assertEqual(source_signature_hash(verified), pre_signature)
        state = empty_state()
        update_verification_state(state, attempted=[verified], deferred=[], now=NOW)
        for day in (1, 6):
            current = fresh()
            self.assertEqual(source_signature_hash(current), pre_signature)
            self.assertEqual(len(plan_verification([current], state, now=NOW + timedelta(days=day))["cached_active"]), 1)
        self.assertEqual(len(plan_verification([fresh()], state, now=NOW + timedelta(days=7))["selected"]), 1)
        changed = fresh("req-cache-2")
        self.assertNotEqual(stable_identity_hash(changed, state), stable_identity_hash(verified, state))
        self.assertEqual(len(plan_verification([changed], state, now=NOW + timedelta(days=1))["selected"]), 1)

    def test_closed_is_dormant_then_due_after_sixty_days(self):
        closed = make_job("closed"); state = empty_state(); closed.verification_reason = "verified_closed"
        update_verification_state(state, attempted=[closed], deferred=[], now=NOW)
        self.assertEqual(len(plan_verification([closed], state, now=NOW + timedelta(days=59, seconds=86399))["closed_dormant"]), 1)
        self.assertEqual(len(plan_verification([closed], state, now=NOW + timedelta(days=60))["selected"]), 1)
        self.assertEqual(len(plan_verification([closed], state, now=NOW + timedelta(days=61))["selected"]), 1)
        new_job = make_job("new-vacancy")
        self.assertEqual(len(plan_verification([new_job], state, now=NOW + timedelta(days=30))["selected"]), 1)

    def test_three_run_starvation_scenario(self):
        blocked = make_job("blocked", 46)
        deferred = make_job("deferred", 45)
        state = empty_state()
        day1 = plan_verification([blocked, deferred], state, now=NOW, maximum_jobs=1)
        day1["selected"][0].verification_reason = "http_blocked"
        update_verification_state(state, attempted=day1["selected"], deferred=day1["deferred"], now=NOW)
        new = make_job("new", 50, new=True)
        day2 = plan_verification([blocked, deferred, new], state, now=NOW + timedelta(days=1), maximum_jobs=2)
        self.assertNotIn("blocked", [job.id for job in day2["selected"]])
        self.assertEqual({job.id for job in day2["selected"]}, {"deferred", "new"})
        day4 = plan_verification([blocked, deferred, new], state, now=NOW + timedelta(days=3), maximum_jobs=3)
        self.assertIn("blocked", [job.id for job in day4["selected"]])

    def test_state_writer_allows_only_public_operational_fields(self):
        job = make_job("safe")
        state = empty_state()
        state["jobs"][stable_identity_hash(job)] = {
            "last_outcome": "http_blocked", "private_note": "secret",
            "url": "https://private.test", "source_signature_hash": source_signature_hash(job),
            "consecutive_failures": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_verification_state(path, state)
            raw = path.read_text(encoding="utf-8")
            loaded = load_verification_state(path)
        self.assertNotIn("secret", raw)
        self.assertNotIn("private.test", raw)
        self.assertTrue(set(next(iter(loaded["jobs"].values()))) <= ALLOWED_ENTRY_FIELDS)


class ReviewDispositionTests(unittest.TestCase):
    def test_review_dispositions(self):
        active = make_job("active", 40); active.active_status = "active"
        strong = make_job("strong", 45); strong.active_status = "unverified"
        low = make_job("low", 30); low.active_status = "unverified"; low.verification_schedule_status = "backoff"
        cold = make_job("cold", 50, age=400); cold.active_status = "unverified"
        human, backlog, cold_jobs = assign_review_dispositions([low, cold, strong, active], empty_state(), human_limit=2)
        self.assertEqual({job.id for job in human}, {"active", "strong"})
        self.assertEqual([job.id for job in backlog], ["low"])
        self.assertEqual([job.id for job in cold_jobs], ["cold"])

    def test_old_verified_official_job_is_not_cold(self):
        job = make_job("official", 40, age=500, source_type="direct_employer")
        job.active_status = "active"
        human, _backlog, cold = assign_review_dispositions([job], empty_state())
        self.assertEqual([item.id for item in human], ["official"])
        self.assertEqual(cold, [])

    def test_same_raw_id_does_not_cross_select_distinct_vacancies(self):
        chosen = make_job("shared", 50, source_type="direct_employer")
        chosen.ats_board_id = "board-a"
        other = make_job("shared", 30, source_type="direct_employer")
        other.ats_board_id = "board-b"
        human, backlog, cold = assign_review_dispositions([chosen, other], empty_state(), human_limit=1)
        self.assertEqual([job.ats_board_id for job in human], ["board-a"])
        self.assertEqual([job.ats_board_id for job in backlog], ["board-b"])
        self.assertEqual(cold, [])


class IdentityAliasTests(unittest.TestCase):
    def direct(self, identifier="req-1", board="acme"):
        job = make_job(identifier, 45, source_type="direct_employer")
        job.source_id = f"greenhouse/{board}"
        job.ats_board_id = board
        job.company = "Acme Private Limited"
        job.canonical_employer = "Acme"
        job.url = job.original_source_url = job.canonical_employer_url = f"https://jobs.acme.test/jobs/{identifier}"
        job.canonical_resolution_status = "resolved_employer_page"
        return job

    def aggregator(self, identifier="agg-9", source="adzuna/main"):
        job = make_job(identifier, 45)
        job.source_id = source
        job.company = "Acme Pvt Ltd"
        job.canonical_employer = "Acme"
        job.description = " ".join(["operations crm data quality reporting process"] * 12)
        return job

    def test_aggregator_to_direct_and_back_preserves_history(self):
        agg, direct = self.aggregator(), self.direct()
        state = empty_state(); agg.verification_reason = "http_blocked"
        update_verification_state(state, attempted=[agg], deferred=[], now=NOW)
        register_duplicate_aliases(state, [(agg, direct)])
        self.assertEqual(stable_identity_hash(agg, state), stable_identity_hash(direct, state))
        self.assertEqual(plan_verification([direct], state, now=NOW + timedelta(days=1))["backoff"][0].id, direct.id)
        self.assertEqual(plan_verification([agg], state, now=NOW + timedelta(days=1))["backoff"][0].id, agg.id)

    def test_source_churn_company_and_location_changes_use_proven_aliases(self):
        old, direct = self.aggregator(), self.direct()
        state = empty_state(); old.verification_reason = "http_blocked"
        update_verification_state(state, attempted=[old], deferred=[], now=NOW)
        changed = self.aggregator("new-source-id", "jooble/main")
        changed.company = "ACME LIMITED"; changed.location_tier = "hyderabad"
        register_duplicate_aliases(state, [(old, direct), (changed, direct)])
        self.assertEqual(stable_identity_hash(old, state), stable_identity_hash(changed, state))

    def test_conflicting_official_requisitions_never_alias(self):
        first, second = self.direct("req-1"), self.direct("req-2")
        state = empty_state(); first.verification_reason = "http_blocked"
        update_verification_state(state, attempted=[first], deferred=[], now=NOW)
        register_duplicate_aliases(state, [(first, second)])
        self.assertNotEqual(stable_identity_hash(first, state), stable_identity_hash(second, state))
        self.assertEqual(len(plan_verification([second], state, now=NOW + timedelta(days=1))["selected"]), 1)

    def test_tracking_change_stable_for_safe_official_url(self):
        first = self.direct(); second = self.direct()
        second.url = second.original_source_url = second.canonical_employer_url + "?utm_source=x"
        self.assertEqual(source_signature_hash(first), source_signature_hash(second))

    def test_direct_ats_identity_does_not_require_validated_url(self):
        first = self.direct("req-1", "lever-board")
        first.url = first.original_source_url = first.canonical_employer_url = "https://internal.corp/jobs/123"
        second = self.direct("req-1", "lever-board")
        second.url = second.original_source_url = second.canonical_employer_url = "https://intranet/job"
        self.assertEqual(stable_identity_hash(first), stable_identity_hash(second))
        self.assertEqual(source_signature_hash(first), source_signature_hash(second))
        self.assertNotIn(official_url_alias_hash(first), job_alias_hashes(first))

    def test_runtime_validated_official_url_alias_requires_explicit_evidence(self):
        job = self.direct()
        self.assertEqual(official_url_alias_hash(job), "")
        job.verification_stage = "source"
        job.verification_attempted_host = "jobs.acme.test"
        job.verified_at = NOW.isoformat()
        self.assertTrue(official_url_alias_hash(job))


class SignatureSafetyTests(unittest.TestCase):
    def test_unsafe_urls_use_fixed_sentinel(self):
        unsafe = (
            "https://user:pass@example.test/job", "http://localhost/job",
            "http://127.0.0.1/job", "http://10.0.0.1/job",
            "http://169.254.169.254/latest", "http://[::1]/job", "file:///tmp/job",
        )
        for value in unsafe:
            with self.subTest(value=value):
                self.assertEqual(_safe_state_url(value, validated=True), SAFE_URL_SENTINEL)

    def test_unvalidated_hostname_uses_sentinel(self):
        self.assertEqual(_safe_state_url("https://private.example/job", validated=False), SAFE_URL_SENTINEL)

    def test_direct_source_metadata_is_not_runtime_validation(self):
        for value in (
            "https://internal.corp/jobs/123", "https://internal.example.local/job",
            "https://corp.internal/job", "https://intranet/job",
        ):
            with self.subTest(value=value):
                job = make_job("direct", source_type="direct_employer")
                job.ats_board_id = "lever-board"
                job.url = job.original_source_url = job.canonical_employer_url = value
                job.canonical_resolution_status = "resolved_employer_page"
                self.assertEqual(official_url_alias_hash(job), "")
                self.assertEqual(_safe_state_url(value, validated=False), SAFE_URL_SENTINEL)
                state = empty_state(); job.verification_reason = "http_blocked"
                update_verification_state(state, attempted=[job], deferred=[], now=NOW)
                serialized = json.dumps(state)
                for raw in ("internal.corp", "internal.example.local", "corp.internal", "intranet", "/job", "https://"):
                    self.assertNotIn(raw, serialized)

    def test_secret_and_tracking_parameters_are_removed_before_hashing(self):
        base = make_job("safe", source_type="direct_employer")
        base.canonical_resolution_status = "resolved_employer_page"
        base.original_source_url = "https://jobs.example.test/job/1"
        changed = make_job("safe", source_type="direct_employer")
        changed.canonical_resolution_status = "resolved_employer_page"
        changed.original_source_url = "https://jobs.example.test/job/1?utm_source=x&token=SECRET"
        self.assertEqual(source_signature_hash(base), source_signature_hash(changed))
        changed.original_source_url = "https://jobs.example.test/job/2"
        self.assertEqual(source_signature_hash(base), source_signature_hash(changed))


class LoaderAndAtomicWriteTests(unittest.TestCase):
    def test_missing_file_initializes_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_verification_state(Path(directory) / "missing.json"), empty_state())

    def test_structural_corruption_is_hard_failure(self):
        payloads = ("", "{", "[]", '{"schema_version":1,"jobs":{},"aliases":{}}',
                    '{"schema_version":99,"jobs":{},"aliases":{}}',
                    '{"schema_version":2,"jobs":[],"aliases":{}}',
                    '{"schema_version":2,"jobs":{},"aliases":[]}')
        with tempfile.TemporaryDirectory() as directory:
            for index, payload in enumerate(payloads):
                path = Path(directory) / f"{index}.json"; path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(VerificationStateError):
                    load_verification_state(path)

    def test_invalid_siblings_are_discarded_and_valid_entry_survives(self):
        valid_key, bad_key = "a" * 64, "b" * 64
        payload = {"schema_version": 2, "jobs": {
            valid_key: {"last_outcome": "http_blocked", "consecutive_failures": 2},
            bad_key: {"last_outcome": "unknown"},
            "invalid": {"last_outcome": "timeout"},
            "c" * 64: {"times_scheduled": MAX_COUNTER + 1},
            "d" * 64: None,
            "f" * 64: {"last_outcome": "timeout", "last_attempted_at": "not-a-time"},
            "1" * 64: {"last_outcome": "timeout", "consecutive_failures": -1},
            "2" * 64: {"last_outcome": "timeout", "unknown_field": "discard me"},
        }, "aliases": {"e" * 64: valid_key, "bad": valid_key}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_verification_state(path)
        self.assertEqual(loaded["jobs"][valid_key]["consecutive_failures"], 2)
        self.assertEqual(set(loaded["jobs"]), {valid_key, "2" * 64})
        self.assertNotIn("unknown_field", loaded["jobs"]["2" * 64])
        self.assertEqual(loaded["invalid_entries_discarded"], 7)

    def test_atomic_replace_and_failed_replace_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"old": 1}); self.assertEqual(json.loads(path.read_text()), {"old": 1})
            with patch("job_search.verification_state.os.replace", side_effect=OSError("failure")):
                with self.assertRaises(OSError): atomic_write_json(path, {"new": 2})
            self.assertEqual(json.loads(path.read_text()), {"old": 1})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            with patch("job_search.verification_state.tempfile.NamedTemporaryFile", side_effect=OSError("temp failure")):
                with self.assertRaises(OSError): atomic_write_json(path, {"newer": 3})
            self.assertEqual(json.loads(path.read_text()), {"old": 1})


class FairnessAndClosedTests(unittest.TestCase):
    def test_equivalent_budget_exhausted_jobs_rotate(self):
        jobs = [make_job(f"equal-{index}") for index in range(150)]
        state = empty_state(); scheduled = set(); selections = []
        for day in range(10):
            plan = plan_verification(jobs, state, now=NOW + timedelta(days=day), maximum_jobs=100)
            ids = {job.id for job in plan["selected"]}; scheduled |= ids; selections.append(ids)
            for job in plan["selected"]: job.verification_reason = "verification_budget_exhausted"
            update_verification_state(state, attempted=plan["selected"], deferred=plan["deferred"], now=NOW + timedelta(days=day))
        self.assertEqual(len(scheduled), 150)
        self.assertNotEqual(selections[0], selections[1])

    def test_high_value_is_not_displaced_by_fairness(self):
        high = [make_job(f"high-{index}", 50) for index in range(100)]
        low = [make_job(f"low-{index}", 30) for index in range(100)]
        plan = plan_verification(high + low, empty_state(), now=NOW, maximum_jobs=100)
        self.assertEqual({job.id for job in plan["selected"]}, {job.id for job in high})

    def test_closed_recheck_repeats_and_can_reopen(self):
        job = make_job("closed"); state = empty_state(); job.verification_reason = "verified_closed"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        due = plan_verification([job], state, now=NOW + timedelta(days=60))
        self.assertEqual(due["closed_rechecks_due"], 1)
        job.verification_reason = "verified_closed"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW + timedelta(days=60))
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=119))["closed_dormant"]), 1)
        job.verification_reason = "verified_active"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW + timedelta(days=120))
        self.assertNotEqual(state["jobs"][stable_identity_hash(job, state)]["last_outcome"], "verified_closed")

    def test_closed_signature_change_rechecks_before_sixty_days(self):
        job = make_job("closed", source_type="direct_employer")
        job.canonical_resolution_status = "resolved_employer_page"
        job.original_source_url = "https://jobs.example.test/closed"
        state = empty_state(); job.verification_reason = "verified_closed"
        update_verification_state(state, attempted=[job], deferred=[], now=NOW)
        job.source_id = "source/reopened"
        self.assertEqual(len(plan_verification([job], state, now=NOW + timedelta(days=2))["selected"]), 1)


if __name__ == "__main__":
    unittest.main()
