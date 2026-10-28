"""Holdout test: a second Harbourline page with the new layout, never shown to the agent.

Run by the scorer from the run folder:  python3 .score/test_holdout.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from scout_fixture.parsers.harbourline import parse_jobs  # noqa: E402

BASE = "https://harbourline.example"


class HarbourlineHoldoutTest(unittest.TestCase):
    def test_holdout_page(self):
        html = (HERE / "harbourline_holdout.html").read_text(encoding="utf-8")
        self.assertEqual(parse_jobs(html, BASE), [
            {"job_id": "HL-2101", "title": "Senior Analytics Engineer", "location": "Burnaby, BC",
             "employment_type": "Full-time", "url": "https://harbourline.example/careers/HL-2101"},
            {"job_id": "HL-2104", "title": "Geospatial Data Scientist", "location": "Vancouver, BC",
             "employment_type": "Contract", "url": "https://harbourline.example/careers/HL-2104"},
        ])


if __name__ == "__main__":
    unittest.main()
