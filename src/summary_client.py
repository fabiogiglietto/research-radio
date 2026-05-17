"""
Fetch structured paper summaries from the public fg-zettelkasten repo.

fg-zettelkasten writes a structured summary per paper to
data/summaries/<bibtex-key>.json. research-radio uses it as a scaffold for the
podcast script. Every failure path returns None — the script then falls back
to the paper PDF alone, exactly as it did before this scaffold existed.
"""

from typing import Optional
from urllib.parse import quote

import requests


def fetch_summary(paper_id: str, base_url: str, timeout: int = 20) -> Optional[dict]:
    """Return the fg-zettelkasten structured summary for `paper_id`, or None.

    `paper_id` is the feed id ("bibtex:AuthorYear-xx"); the summary file is
    <AuthorYear-xx>.json under `base_url`. A missing summary (404) is normal
    and silent — fg-zettelkasten may simply not have processed the paper yet,
    or the vault repo may still be private.
    """
    key = paper_id.split(":", 1)[-1]
    url = f"{base_url.rstrip('/')}/{quote(key, safe='')}.json"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 404:
            return None  # expected: paper not (yet) in the vault
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  Note: fg-zettelkasten summary unavailable for {key} ({exc})")
        return None
