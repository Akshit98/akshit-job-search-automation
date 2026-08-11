import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_search import cli


class WorkflowSafetyTests(unittest.TestCase):
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
            with patch.object(cli, "ROOT", root), patch.object(cli, "collect", return_value=([], ["all sources failed"])):
                self.assertEqual(cli.run(), 2)
            self.assertEqual(report.read_text(encoding="utf-8"), "previous good report")
