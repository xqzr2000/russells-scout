import pathlib
import unittest

from scout_fixture.parsers.quayside import parse_jobs

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "quayside_2026-10.html"


class QuaysideParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"))

    def test_finds_every_row(self):
        self.assertEqual([j["job_id"] for j in self.jobs], ["q-311", "q-317", "q-320"])

    def test_first_row(self):
        self.assertEqual(self.jobs[0], {
            "job_id": "q-311",
            "title": "Data Scientist II",
            "location": "Vancouver, BC",
            "employment_type": "Full-time",
            "url": "https://quayside.example/careers/q-311",
        })

    def test_relative_links_made_absolute(self):
        self.assertEqual(self.jobs[1]["url"], "https://quayside.example/careers/q-317")

    def test_header_row_skipped(self):
        self.assertNotIn("Role", [j["title"] for j in self.jobs])


if __name__ == "__main__":
    unittest.main()
