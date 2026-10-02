import json
import os
import socket
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from job_search.core import job_fingerprint
from job_search.sources import adzuna, arbeitnow, ashby, collect, greenhouse, himalayas, jooble, lever, prefer_source_job, remotive, remoteok, themuse, validate_source_config

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "sources.json").read_text(encoding="utf-8"))


class SourceParserTests(unittest.TestCase):
    def test_all_supported_source_fixtures_parse(self):
        cases = [(greenhouse,"board","greenhouse"),(lever,"board","lever"),(ashby,"board","ashby"),(remoteok,"public","remoteok"),(remotive,"public","remotive"),(arbeitnow,"public","arbeitnow"),(himalayas,"public","himalayas"),(themuse,"public","themuse")]
        for handler, identifier, fixture in cases:
            with self.subTest(source=fixture), patch("job_search.sources.get_json", return_value=FIXTURES[fixture]):
                job = handler(identifier)[0]
                self.assertTrue(job.company)
                self.assertTrue(job.published_at)

    def test_direct_board_uses_canonical_employer_not_slug(self):
        metadata = {"board_id":"remotecom","employer_name":"Remote","source_type":"direct_employer"}
        with patch("job_search.sources.get_json", return_value=FIXTURES["greenhouse"]):
            job = greenhouse(metadata)[0]
        self.assertEqual(job.company, "Remote")
        self.assertEqual(job.ats_board_id, "remotecom")
        self.assertEqual(job.source_type, "direct_employer")

    def test_lever_retains_all_material_content_sections(self):
        with patch("job_search.sources.get_json", return_value=FIXTURES["lever"]):
            job = lever({"board_id":"board","employer_name":"Direct Employer","source_type":"direct_employer"})[0]
        self.assertIn("Responsibilities", job.description)
        self.assertIn("Maintain CRM data quality", job.description)
        self.assertIn("Required qualifications", job.description)
        self.assertIn("Experience using Salesforce", job.description)
        self.assertIn("Preferred qualifications", job.description)
        self.assertIn("HubSpot data-maintenance", job.description)
        self.assertEqual(job.location, "Bengaluru, India")
        self.assertEqual(job.workplace, "remote")

    def test_jobgether_preserves_advertised_employer_and_provider(self):
        with patch("job_search.sources.get_json", return_value=FIXTURES["lever"]):
            job = lever({"board_id":"jobgether","provider_name":"Jobgether","source_type":"aggregator","source_priority":25})[0]
        self.assertEqual(job.company, "Example Hiring Company")
        self.assertEqual(job.canonical_employer, "Example Hiring Company")
        self.assertEqual(job.source, "Jobgether")
        self.assertEqual(job.ats_board_id, "jobgether")

    def test_jobgether_can_preserve_employer_advertised_in_posting_text(self):
        payload = [{**FIXTURES["lever"][0], "companyName":"", "descriptionPlain":"This position is posted on behalf of Acme Research. The role supports operations."}]
        with patch("job_search.sources.get_json", return_value=payload):
            job = lever({"board_id":"jobgether","provider_name":"Jobgether","source_type":"aggregator"})[0]
        self.assertEqual(job.company, "Acme Research")
        self.assertEqual(job.source, "Jobgether")

    def test_aggregator_compensation_provenance(self):
        with patch.dict(os.environ,{"ADZUNA_APP_ID":"id","ADZUNA_APP_KEY":"key"}), patch("job_search.sources.get_json",return_value=FIXTURES["adzuna"]):
            job = adzuna({"queries":["operations analyst"]})[0]
        self.assertEqual(job.compensation_source,"aggregator_estimate")
        self.assertEqual(job.description_provenance,"aggregator_excerpt")

    def test_jooble_fixture_parses_without_exposing_credentials(self):
        with patch.dict(os.environ,{"JOOBLE_API_KEY":"test-only"}), patch("job_search.sources.post_json",return_value=FIXTURES["jooble"]):
            job=jooble({"queries":["operations analyst"]})[0]
        self.assertEqual(job.company,"Example Jooble")
        self.assertEqual(job.compensation_source,"aggregator_estimate")
        self.assertNotIn("test-only",job.url)

    def test_structured_source_config_validates(self):
        config=json.loads((Path(__file__).parents[1]/"config"/"sources.json").read_text(encoding="utf-8"))
        validate_source_config(config)

    def test_missing_credentials_and_empty_results_are_distinct(self):
        config={"schema_version":2,"queries":{"career_targets":["operations analyst"]},"sources":[{"id":"adzuna/public","type":"adzuna","enabled":True,"source_type":"aggregator","query_group":"career_targets","required_env":["ADZUNA_APP_ID","ADZUNA_APP_KEY"]},{"id":"remoteok/public","type":"remoteok","enabled":True,"source_type":"job_board"}]}
        with patch.dict(os.environ,{},clear=True), patch("job_search.sources.get_json",return_value=[]):
            jobs, errors, diagnostics=collect(config,include_diagnostics=True)
        statuses={d.source_id:d.status for d in diagnostics}
        self.assertEqual(statuses["adzuna/public"],"missing_credential")
        self.assertEqual(statuses["remoteok/public"],"zero_results")
        self.assertFalse(errors)

    def test_malformed_and_partial_failures_are_diagnostic(self):
        config={"schema_version":2,"queries":{},"sources":[{"id":"good/public","type":"remoteok","enabled":True,"source_type":"job_board"},{"id":"bad/board","type":"greenhouse","enabled":True,"source_type":"direct_employer","board_id":"bad","employer_name":"Bad"}]}
        def response(url, **kwargs):
            if "greenhouse" in url: raise json.JSONDecodeError("malformed JSON", "", 0)
            return FIXTURES["remoteok"]
        with patch("job_search.sources.get_json",side_effect=response):
            jobs, errors, diagnostics=collect(config,include_diagnostics=True)
        self.assertTrue(jobs)
        self.assertTrue(errors)
        self.assertEqual({d.status for d in diagnostics},{"successful","malformed_response"})

    def test_changed_source_schema_is_not_zero_results(self):
        config={"schema_version":2,"queries":{},"sources":[{"id":"remoteok/public","type":"remoteok","enabled":True,"source_type":"job_board"}]}
        with patch("job_search.sources.get_json", return_value={"unexpected":"shape"}):
            jobs, errors, diagnostics=collect(config,include_diagnostics=True)
        self.assertFalse(jobs)
        self.assertTrue(errors)
        self.assertEqual(diagnostics[0].status,"schema_parser_error")

    def test_wrapped_timeout_is_classified_as_timeout(self):
        config={"schema_version":2,"queries":{},"sources":[{"id":"remoteok/public","type":"remoteok","enabled":True,"source_type":"job_board"}]}
        with patch("job_search.sources.get_json", side_effect=URLError(socket.timeout("secret-token"))):
            jobs, errors, diagnostics=collect(config,include_diagnostics=True)
        self.assertFalse(jobs)
        self.assertEqual(diagnostics[0].status,"timeout")
        self.assertEqual(diagnostics[0].reason,"request timed out")
        self.assertNotIn("secret-token", " ".join(errors) + diagnostics[0].reason)

    def test_public_diagnostics_redact_sensitive_request_text(self):
        config={"schema_version":2,"queries":{},"sources":[{"id":"remoteok/public","type":"remoteok","enabled":True,"source_type":"job_board"}]}
        sentinel="SENTINEL_SECRET_VALUE"
        with patch("job_search.sources.get_json", side_effect=URLError(f"https://example.test/jobs?api_key={sentinel}")):
            jobs, errors, diagnostics=collect(config,include_diagnostics=True)
        public_text=json.dumps({"errors":errors,"diagnostics":[d.to_dict() for d in diagnostics]})
        self.assertNotIn(sentinel,public_text)
        self.assertEqual(diagnostics[0].reason,"network request failed")

    def test_cross_source_identity_uses_canonical_employer(self):
        with patch("job_search.sources.get_json",return_value=FIXTURES["greenhouse"]):
            direct=greenhouse({"board_id":"remotecom","employer_name":"Remote","source_type":"direct_employer"})[0]
        duplicate=direct.__class__(**{**direct.to_dict(),"id":"aggregator:1","source":"Aggregator","source_type":"aggregator"})
        self.assertEqual(job_fingerprint(direct),job_fingerprint(duplicate))

    def test_direct_employer_duplicate_wins_and_keeps_fuller_description(self):
        with patch("job_search.sources.get_json",return_value=FIXTURES["greenhouse"]):
            direct=greenhouse({"board_id":"remotecom","employer_name":"Remote","source_type":"direct_employer","source_priority":100})[0]
        aggregator=direct.__class__(**{**direct.to_dict(),"id":"aggregator:1","source_type":"aggregator","source_priority":20,"description":"Thin excerpt","description_provenance":"aggregator_excerpt"})
        winner=prefer_source_job(aggregator,direct)
        self.assertEqual(winner.source_type,"direct_employer")
        self.assertGreater(len(winner.description),len(aggregator.description))


class HimalayasLocationTests(unittest.TestCase):
    def test_string_location_restrictions_are_preserved(self):
        payload={"jobs":[{**FIXTURES["himalayas"]["jobs"][0],"locationRestrictions":["United States"]}]}
        with patch("job_search.sources.get_json",return_value=payload): jobs=himalayas("public")
        self.assertTrue(all(job.location=="United States" for job in jobs))

    def test_missing_location_restrictions_are_not_called_worldwide(self):
        payload={"jobs":[{**FIXTURES["himalayas"]["jobs"][0],"locationRestrictions":[]}]}
        with patch("job_search.sources.get_json",return_value=payload): jobs=himalayas("public")
        self.assertTrue(all(job.location=="" for job in jobs))


if __name__ == "__main__": unittest.main()
