import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_search import cli
from job_search.core import Job


class WorkflowSafetyTests(unittest.TestCase):
    def test_workflow_stages_only_approved_public_artifacts(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "job-search.yml").read_text(encoding="utf-8")
        self.assertIn("git add -- output/latest.md output/jobs.json output/jobs.csv data/seen_jobs.json", workflow)
        self.assertNotIn("git add output ", workflow)

    def test_enriched_jobs_are_fully_reassessed(self):
        profile = {
            "maximum_required_experience_years": 5,
            "primary_role_terms": ["crm operations"],
            "strong_skills": ["salesforce"],
            "supporting_skills": [], "learning_skills": [],
            "excluded_titles": [], "screening_exclusion_terms": ["cold calling"],
            "mandatory_advanced_skill_terms": [],
            "screening_queue_thresholds": {"strong_shortlist":45,"review_queue":30},
        }
        job = Job("1","Aggregator","Example","CRM Operations Associate","India","Remote","Thin excerpt","https://example.test",screening_score=40,screening_queue="review_queue")
        job.description = (
            "Responsibilities: maintain CRM account and contact data, prepare weekly operational reports, "
            "document data-quality issues, coordinate corrections with customer teams, monitor recurring "
            "process exceptions, maintain operating procedures, support partner onboarding records, and "
            "perform cold calling. The role also reviews incomplete records, validates requested updates, "
            "tracks turnaround times, and communicates recurring process issues to operations leadership. "
            "Requirements: Salesforce experience, prior customer operations experience, careful written "
            "documentation, consistent data-maintenance practices, and experience preparing operational "
            "reports for cross-functional stakeholders."
        )
        reassessed = cli.reassess_after_enrichment([job], profile, {})
        self.assertEqual(len(reassessed), 1)
        self.assertIn("outbound_sales", reassessed[0].exclusion_signals)
        self.assertEqual(reassessed[0].screening_queue, "suppressed")
        self.assertEqual(reassessed[0].evidence_quality, "sufficient")
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

    def test_dry_run_evidence_counts_use_post_enrichment_reassessment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "data").mkdir()
            profile = {
                "internship_min_monthly_inr_remote": 40000,
                "internship_min_monthly_inr_onsite": 40000,
                "maximum_required_experience_years": 5,
                "primary_role_terms": ["operations analyst"],
                "strong_skills": [], "supporting_skills": [], "learning_skills": [],
                "excluded_titles": [], "screening_exclusion_terms": [],
                "mandatory_advanced_skill_terms": [],
                "screening_queue_thresholds": {"strong_shortlist": 45, "review_queue": 30},
                "location_preference": ["remote_india", "hyderabad"],
            }
            (root / "config" / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
            (root / "config" / "sources.json").write_text("{}", encoding="utf-8")
            (root / "data" / "seen_jobs.json").write_text("[]", encoding="utf-8")
            found = Job("1", "test", "Example", "Operations Analyst", "Remote - India", "Remote", "Short partial description", "https://example.test")

            def enrich(jobs, **kwargs):
                job = jobs[0]
                job.description = (
                    "Responsibilities: maintain operational records, validate incoming data, document recurring "
                    "quality issues, prepare weekly reports, coordinate corrections, support stakeholder reviews, "
                    "and maintain process documentation. The role reviews incomplete records and tracks resolution "
                    "timelines across operating teams. Requirements: experience with operational data validation, "
                    "quality review, documentation, recurring reporting, stakeholder coordination, and careful "
                    "record maintenance in structured business systems."
                )
                job.active_status = "active"
                return [job], 0, 0

            output = io.StringIO()
            with (
                patch.object(cli, "ROOT", root),
                patch.object(cli, "load_evidence", return_value={}),
                patch.object(cli, "collect", return_value=([found], [])),
                patch.object(cli, "retain_active_jobs", side_effect=enrich),
                patch("sys.stdout", output),
            ):
                self.assertEqual(cli.run(dry_run=True), 0)
            self.assertIn("Evidence quality after eligibility: sufficient 1; partial/insufficient 0", output.getvalue())
