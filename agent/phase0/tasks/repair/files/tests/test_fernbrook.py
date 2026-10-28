import pathlib
import unittest

from scout_fixture.parsers.fernbrook import parse_jobs

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "fernbrook_2026-10.html"
BASE = "https://careers.fernbrook.example"


class FernbrookParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"), BASE)

    def test_finds_every_opening(self):
        self.assertEqual(len(self.jobs), 5)

    def test_first_opening(self):
        self.assertEqual(self.jobs[0], {
            "job_id": "4101",
            "title": "Senior Data Scientist",
            "location": "Vancouver, BC",
            "employment_type": "Full-time",
            "url": "https://careers.fernbrook.example/jobs/4101-senior-data-scientist",
        })

    def test_job_ids_in_page_order(self):
        self.assertEqual([j["job_id"] for j in self.jobs], ["4101", "4102", "4107", "4110", "4112"])

    def test_urls_are_absolute(self):
        for job in self.jobs:
            self.assertTrue(job["url"].startswith(BASE + "/jobs/"), job["url"])

    def test_location_and_type_are_split(self):
        self.assertEqual(self.jobs[3]["location"], "Remote (Canada)")
        self.assertEqual(self.jobs[3]["employment_type"], "Contract")
        self.assertEqual(self.jobs[4]["location"], "Vancouver, BC (Hybrid)")
        self.assertEqual(self.jobs[4]["employment_type"], "Full-time")

    def test_html_entities_decoded(self):
        self.assertEqual(self.jobs[2]["title"], "Data & Analytics Intern")

    def test_promo_card_is_not_a_job(self):
        self.assertFalse(any("talent community" in j["title"].lower() for j in self.jobs))

    def test_empty_list_page(self):
        html = '<html><body><ul class="job-list"></ul></body></html>'
        self.assertEqual(parse_jobs(html, BASE), [])


if __name__ == "__main__":
    unittest.main()
