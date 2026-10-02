import json
import tempfile
import unittest
from pathlib import Path

from job_search.evidence import load_evidence, verified_evidence_terms


class EvidenceModelTests(unittest.TestCase):
    def test_learning_skills_are_not_verified_professional_terms(self):
        evidence = {
            "professional_responsibilities": [{"id": "data_validation", "terms": ["data validation"]}],
            "tools": [{"id": "salesforce", "terms": ["salesforce"], "level": "professional_use"}],
            "learning_only": [{"id": "sql", "terms": ["sql"]}, {"id": "python", "terms": ["python"]}],
        }
        terms = verified_evidence_terms(evidence)
        self.assertIn("salesforce", terms)
        self.assertIn("data validation", terms)
        self.assertNotIn("sql", terms)
        self.assertNotIn("python", terms)

    def test_evidence_loader_validates_required_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps({"candidate": "Akshit"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_evidence(path)
