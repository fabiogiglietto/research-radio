"""
Fetch structured paper summaries from the public fg-zettelkasten repo.

fg-zettelkasten writes a structured summary per paper to
data/summaries/<bibtex-key>.json. research-radio uses it as a scaffold for the
podcast script. Every failure path returns None — the script then falls back
to the paper PDF alone, exactly as it did before this scaffold existed.

Summaries are fetched through the GitHub Contents API rather than
raw.githubusercontent.com: the raw CDN caches ~5 minutes, but in the pipeline
fg-zettelkasten commits the summary only moments before research-radio is
dispatched, so a raw fetch routinely misses a brand-new paper. The Contents
API is served fresh.
"""

import os
from typing import Optional
from urllib.parse import quote

import requests


def fetch_summary(paper_id: str, base_url: str, timeout: int = 20) -> Optional[dict]:
    """Return the fg-zettelkasten structured summary for `paper_id`, or None.

    `paper_id` is the feed id ("bibtex:AuthorYear-xx"); the summary file is
    <AuthorYear-xx>.json under `base_url`, which must be a GitHub Contents API
    directory URL — https://api.github.com/repos/<owner>/<repo>/contents/<path>.

    A missing summary (404) is normal and silent — fg-zettelkasten may simply
    not have processed the paper yet, or the vault repo may still be private.
    """
    key = paper_id.split(":", 1)[-1]
    url = f"{base_url.rstrip('/')}/{quote(key, safe='')}.json"
    # `raw` media type returns the file body directly. A token is optional for
    # a public repo but lifts the API rate limit from 60 to 5000 requests/hour.
    headers = {"Accept": "application/vnd.github.raw+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            return None  # expected: paper not (yet) in the vault
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  Note: fg-zettelkasten summary unavailable for {key} ({exc})")
        return None
