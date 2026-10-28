import pathlib
import unittest

from scout_fixture.parsers.tidewater import parse_jobs

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "tidewater_2026-10.json"


class TidewaterParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"))

    def test_finds_every_posting(self):
        self.assertEqual([j["job_id"] for j in self.jobs], ["tw-88", "tw-91", "tw-95"])

    def test_first_posting(self):
        self.assertEqual(self.jobs[0], {
            "job_id": "tw-88",
            "title": "Senior Data Analyst, Grid Operations",
            "location": "Vancouver, BC",
            "employment_type": "Full-time",
            "url": "https://jobs.tidewater.example/tw-88",
        })

    def test_empty_feed(self):
        self.assertEqual(parse_jobs('{"postings": []}'), [])


if __name__ == "__main__":
    unittest.main()
