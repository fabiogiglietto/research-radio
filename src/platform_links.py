"""Resolve per-episode Spotify and Apple Podcasts URLs for published episodes.

Spotify and Apple mint their own episode ids when they ingest the RSS feed, so a
per-episode deep link can't be derived locally — it has to be looked up from each
platform's catalogue and matched back to our episodes:

- Apple: the free iTunes Lookup API returns every episode with its `episodeGuid`,
  which equals our `bibtex:` episode id — a deterministic, credential-free join.
- Spotify: the Web API needs an app (client-credentials) token and exposes no RSS
  guid, so episodes are matched by title. Skipped when SPOTIFY_CLIENT_ID /
  SPOTIFY_CLIENT_SECRET are unset.

Resolution is best-effort: a platform lookup that fails is logged and skipped so
it never blocks podcast generation. The resolved URLs are written into
docs/episodes.json, which fg-zettelkasten reads to render each paper note's
Podcast section.

Run standalone to backfill links into the existing feed:
    python -m src.platform_links
"""
from __future__ import annotations

import sys

import requests

from config import (
    APPLE_PODCAST_ID,
    APPLE_PODCAST_COUNTRY,
    SPOTIFY_SHOW_ID,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
)

_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_ITUNES_LIMIT = 200  # the lookup API's maximum
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_API = "https://api.spotify.com/v1"


def _apple_urls_by_guid(timeout: int = 30) -> dict[str, str]:
    """Map episode guid ("bibtex:...") -> Apple Podcasts episode URL."""
    if not APPLE_PODCAST_ID:
        return {}
    resp = requests.get(
        _ITUNES_LOOKUP,
        params={
            "id": APPLE_PODCAST_ID,
            "country": APPLE_PODCAST_COUNTRY,
            "media": "podcast",
            "entity": "podcastEpisode",
            "limit": _ITUNES_LIMIT,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    out: dict[str, str] = {}
    for item in resp.json().get("results", []):
        if item.get("wrapperType") != "podcastEpisode":
            continue
        guid = item.get("episodeGuid")
        url = item.get("trackViewUrl")
        if guid and url:
            out[guid] = url.split("&uo=")[0]  # drop the analytics param
    if len(out) >= _ITUNES_LIMIT:
        print("  apple: hit the lookup cap; oldest episodes may be unresolved")
    return out


def _norm_title(title: str) -> str:
    """Collapse whitespace and casefold a title for cross-platform matching."""
    return " ".join((title or "").split()).casefold()


def _spotify_urls_by_title(timeout: int = 30) -> dict[str, str]:
    """Map normalised episode title -> Spotify episode URL.

    Empty when credentials are unset (the common case until a Spotify app is
    provisioned). Spotify exposes no RSS guid, so the join is by title."""
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and SPOTIFY_SHOW_ID):
        return {}
    token_resp = requests.post(
        _SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=timeout,
    )
    token_resp.raise_for_status()
    headers = {"Authorization": f"Bearer {token_resp.json()['access_token']}"}

    out: dict[str, str] = {}
    url = f"{_SPOTIFY_API}/shows/{SPOTIFY_SHOW_ID}/episodes"
    params: dict | None = {"market": "US", "limit": 50}
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        page = resp.json()
        for item in page.get("items", []):
            if not item:  # Spotify returns null items for unavailable episodes
                continue
            name = item.get("name")
            ep_url = (item.get("external_urls") or {}).get("spotify")
            if name and ep_url:
                out.setdefault(_norm_title(name), ep_url)
        url = page.get("next")  # a full URL, so the original params no longer apply
        params = None
    return out


def enrich_episodes(episodes) -> int:
    """Fill apple_url / spotify_url on each Episode in place; return count changed.

    Own-publication episodes are excluded from the public feed and never reach
    Spotify/Apple, so they are left untouched. Each platform is resolved
    independently and a failure in one is non-fatal."""
    try:
        apple = _apple_urls_by_guid()
    except requests.RequestException as exc:  # noqa: BLE001 - non-fatal
        print(f"  apple: lookup failed ({exc})")
        apple = {}
    try:
        spotify = _spotify_urls_by_title()
    except requests.RequestException as exc:  # noqa: BLE001 - non-fatal
        print(f"  spotify: lookup failed ({exc})")
        spotify = {}

    if not apple and not spotify:
        return 0

    changed = 0
    for ep in episodes:
        if ep.own:
            continue
        new_apple = apple.get(ep.id, ep.apple_url)
        new_spotify = spotify.get(_norm_title(ep.title), ep.spotify_url)
        if new_apple != ep.apple_url or new_spotify != ep.spotify_url:
            ep.apple_url = new_apple
            ep.spotify_url = new_spotify
            changed += 1
    return changed


def enrich_and_save() -> int:
    """Load episodes, resolve platform links, persist if anything changed."""
    from src.feed_generator import load_episodes, save_episodes

    episodes = load_episodes()
    changed = enrich_episodes(episodes)
    if changed:
        save_episodes(episodes)
    return changed


def main() -> int:
    changed = enrich_and_save()
    print(f"platform links: {changed} episode(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
