"""Resolve per-episode Spotify and Apple Podcasts URLs for published episodes.

Spotify and Apple mint their own episode ids when they ingest the RSS feed, so a
per-episode deep link can't be derived locally — it has to be looked up from each
platform's catalogue and matched back to our episodes:

- Apple: the free iTunes Lookup API returns every episode with its `episodeGuid`,
  which equals our `bibtex:` episode id — a deterministic, credential-free join.
- Spotify: per-episode deep links would need a *user*-authorized token — the
  episode endpoint rejects app-only Client Credentials (403) — so we link to the
  show page instead (open.spotify.com/show/<id>) for every public episode.

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
)

_ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_ITUNES_LIMIT = 200  # the lookup API's maximum


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


def _spotify_show_url() -> str:
    """The Spotify show page URL, or "" when no show id is configured.

    Per-episode deep links aren't reachable: the episode endpoint requires a
    user-authorized token (app-only Client Credentials get a 403), so every
    public episode points at the show page instead."""
    return f"https://open.spotify.com/show/{SPOTIFY_SHOW_ID}" if SPOTIFY_SHOW_ID else ""


def enrich_episodes(episodes) -> int:
    """Fill apple_url / spotify_url on each Episode in place; return count changed.

    Own-publication episodes are excluded from the public feed and never reach
    Spotify/Apple, so they are left untouched. Apple resolution is best-effort:
    a lookup failure is non-fatal."""
    try:
        apple = _apple_urls_by_guid()
    except requests.RequestException as exc:  # noqa: BLE001 - non-fatal
        print(f"  apple: lookup failed ({exc})")
        apple = {}
    spotify_url = _spotify_show_url()

    if not apple and not spotify_url:
        return 0

    changed = 0
    for ep in episodes:
        if ep.own:
            continue
        new_apple = apple.get(ep.id, ep.apple_url)
        new_spotify = spotify_url or ep.spotify_url
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
