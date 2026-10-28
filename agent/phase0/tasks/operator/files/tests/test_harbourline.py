import pathlib
import unittest

from scout_fixture.parsers.harbourline import parse_jobs

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "harbourline_2026-10.html"
BASE = "https://harbourline.example"


class HarbourlineParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"), BASE)

    def test_finds_every_posting(self):
        self.assertEqual([j["job_id"] for j in self.jobs], ["HL-2031", "HL-2034", "HL-2040", "HL-2042"])

    def test_first_posting(self):
        self.assertEqual(self.jobs[0], {
            "job_id": "HL-2031",
            "title": "Data Engineer",
            "location": "Vancouver, BC",
            "employment_type": "Full-time",
            "url": "https://harbourline.example/careers/HL-2031",
        })

    def test_locations(self):
        self.assertEqual([j["location"] for j in self.jobs],
                         ["Vancouver, BC", "Delta, BC", "Vancouver, BC", "Vancouver, BC"])

    def test_employment_types_use_our_labels(self):
        self.assertEqual([j["employment_type"] for j in self.jobs],
                         ["Full-time", "Full-time", "Contract", "Internship"])

    def test_organization_is_not_a_job(self):
        self.assertNotIn("Harbourline Logistics", [j["title"] for j in self.jobs])

    def test_empty_page(self):
        html = '<html><head></head><body><div id="app"></div></body></html>'
        self.assertEqual(parse_jobs(html, BASE), [])


if __name__ == "__main__":
    unittest.main()
