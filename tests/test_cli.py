import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_search import cli


class WorkflowSafetyTests(unittest.TestCase):
    def test_status_command_writes_only_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_state = root / "private" / "application_state.json"
            with (
                patch.object(cli, "ROOT", root),
                patch.dict(os.environ, {"JOB_SEARCH_PRIVATE_STATE": str(private_state)}),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(cli.main(["status", "job-123", "applied", "--note", "Submitted directly"]), 0)
            payload = json.loads(private_state.read_text(encoding="utf-8"))
            self.assertEqual(payload["jobs"]["job-123"]["state"], "applied")
            self.assertEqual(payload["jobs"]["job-123"]["note"], "Submitted directly")

    def test_zero_collection_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "data").mkdir()
            (root / "output").mkdir()
            (root / "config" / "profile.json").write_text("{}", encoding="utf-8")
            (root / "config" / "sources.json").write_text("{}", encoding="utf-8")
            (root / "data" / "seen_jobs.json").write_text("[]", encoding="utf-8")
            report = root / "output" / "latest.md"
            report.write_text("previous good report", encoding="utf-8")
            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "load_evidence", return_value={}),
                patch.object(cli, "collect", return_value=([], ["all sources failed"])),
            ):
                self.assertEqual(cli.run(), 2)
            self.assertEqual(report.read_text(encoding="utf-8"), "previous good report")

    def test_dry_run_does_not_write_reports_or_seen_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "data").mkdir()
            (root / "output").mkdir()
            profile = {
                "minimum_screening_score": 0,
                "internship_min_monthly_inr_remote": 40000,
                "internship_min_monthly_inr_onsite": 40000,
                "maximum_required_experience_years": 5,
                "primary_role_terms": ["operations analyst"],
                "strong_skills": [],
                "supporting_skills": [],
                "learning_skills": [],
                "excluded_titles": [],
                "location_preference": ["remote_india", "hyderabad"],
            }
            (root / "config" / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
            (root / "config" / "sources.json").write_text("{}", encoding="utf-8")
            seen = root / "data" / "seen_jobs.json"
            seen.write_text("[]", encoding="utf-8")
            report = root / "output" / "latest.md"
            report.write_text("previous report", encoding="utf-8")
            from job_search.core import Job
            found = Job("1", "test", "Example", "Operations Analyst", "Remote - India", "Remote", "Operations analyst role", "https://example.test")
            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "load_evidence", return_value={}),
                patch.object(cli, "collect", return_value=([found], [])),
                patch.object(cli, "retain_active_jobs", return_value=([found], 0, 0)),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(cli.run(dry_run=True), 0)
            self.assertEqual(report.read_text(encoding="utf-8"), "previous report")
            self.assertEqual(seen.read_text(encoding="utf-8"), "[]")
