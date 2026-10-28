"""Parser for Tidewater Energy Analytics' postings API (JSON).

    {"postings": [{"id": "tw-88", "text": "Title",
                   "categories": {"location": "Vancouver, BC", "commitment": "Full-time"},
                   "hostedUrl": "https://jobs.tidewater.example/tw-88"}]}
"""

import json


def parse_jobs(text: str) -> list[dict]:
    data = json.loads(text)
    jobs = []
    for posting in data.get("postings", []):
        categories = posting.get("categories") or {}
        jobs.append({
            "job_id": posting["id"],
            "title": " ".join(posting.get("text", "").split()),
            "location": categories.get("location", ""),
            "employment_type": categories.get("commitment", ""),
            "url": posting.get("hostedUrl"),
        })
    return jobs
