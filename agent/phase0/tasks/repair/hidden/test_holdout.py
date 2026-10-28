"""Holdout test: a second page with the same new layout, never shown to the agent.

Catches fixes that hard-code the visible fixture instead of parsing the layout.
Run by the scorer from the run folder:  python3 .score/test_holdout.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from scout_fixture.parsers.fernbrook import parse_jobs  # noqa: E402

BASE = "https://careers.fernbrook.example"


class FernbrookHoldoutTest(unittest.TestCase):
    def test_holdout_page(self):
        html = (HERE / "fernbrook_holdout.html").read_text(encoding="utf-8")
        self.assertEqual(parse_jobs(html, BASE), [
            {"job_id": "5201", "title": "Data Analyst, Supply Chain", "location": "Richmond, BC",
             "employment_type": "Part-time",
             "url": "https://careers.fernbrook.example/jobs/5201-data-analyst-supply-chain"},
            {"job_id": "5203", "title": "BI Analyst", "location": "Vancouver, BC",
             "employment_type": "Full-time",
             "url": "https://careers.fernbrook.example/jobs/5203-bi-analyst"},
            {"job_id": "5210", "title": "MLOps Engineer", "location": "Remote (Canada)",
             "employment_type": "Contract",
             "url": "https://careers.fernbrook.example/jobs/5210-ml-ops-engineer"},
        ])


if __name__ == "__main__":
    unittest.main()
