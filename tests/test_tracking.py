import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_search.core import Job
from job_search.notify import build_slack_message
from job_search.report import write_reports
from job_search.tracking import ALLOWED_STATES, TrackingStore, resolve_private_state_path


class TrackingTests(unittest.TestCase):
    def test_every_allowed_state_can_be_saved_with_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(Path(directory) / "private" / "state.json", Path(directory))
            for state in sorted(ALLOWED_STATES):
                store.set_status("job-1", state)
            record = store.load()["jobs"]["job-1"]
            self.assertEqual(record["state"], sorted(ALLOWED_STATES)[-1])
            self.assertEqual(len(record["history"]), len(ALLOWED_STATES))

    def test_invalid_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TrackingStore(Path(directory) / "private" / "state.json", Path(directory))
            with self.assertRaises(ValueError):
                store.set_status("job-1", "maybe")

    def test_environment_path_overrides_ignored_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "private-two" / "state.json"
            with patch.dict(os.environ, {"JOB_SEARCH_PRIVATE_STATE": str(custom)}):
                self.assertEqual(resolve_private_state_path(root), custom.resolve())

    def test_refuses_public_repository_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for unsafe in (root / "output" / "state.json", root / "data" / "state.json", root / "config" / "state.json"):
                with self.subTest(path=unsafe):
                    with self.assertRaises(ValueError):
                        TrackingStore(unsafe, root)

    def test_arbitrary_in_repo_path_must_be_git_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                TrackingStore(root / "misc-private" / "state.json", root)

    def test_ignored_private_fallback_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackingStore(root / "private" / "application_state.json", root)
            store.set_status("job-1", "saved")
            self.assertTrue(store.path.exists())

    def test_private_values_never_enter_public_reports_or_slack(self):
        secret = "PRIVATE_INTERVIEW_NOTE_9274"
        job = Job(
            "job-1", "test", "Example", "Operations Analyst", "India", "Remote",
            "Operations role", "https://example.test/job", active_status="active",
            screening_score=50, screening_queue="strong_shortlist",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrackingStore(root / "private" / "state.json", root)
            store.set_status(job.id, "interview", note=secret)
            output = root / "public-output"
            write_reports([job], [], [], output)
            public_text = "\n".join(path.read_text(encoding="utf-8-sig") for path in output.iterdir())
            message = build_slack_message({"jobs": [{**job.to_dict(), "private_note": secret}], "errors": []}) or ""
        self.assertNotIn(secret, public_text)
        self.assertNotIn(secret, message)

    def test_loading_missing_private_state_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "state.json"
            store = TrackingStore(path, Path(directory))
            self.assertEqual(store.load(), {"version": 1, "jobs": {}})
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
