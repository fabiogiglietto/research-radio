#!/usr/bin/env python3
"""
Regenerate the audio of already-published episodes, in place.

For an episode whose render is broken beyond what mastering can fix (a dead
gap of digital silence, the wrong paper, a truncated file), this re-runs the
production path — Claude script, Gemini TTS, mastering, level gate — and
replaces the release asset under the SAME filename, then updates only the
byte size and duration in docs/episodes.json and docs/feed.xml. The episode
keeps its guid, pubDate, title, description and platform links, so podcast
apps see a changed enclosure, not a new episode.

The paper stays in data/processed.json throughout: nothing here can make the
generator pick it up again as new.

Usage:
    python scripts/regenerate_episode.py Topinka2026-hb
    python scripts/regenerate_episode.py Topinka2026-hb --no-upload   # render only

Needs the generator's credentials (GEMINI_API_KEY, Anthropic, Google Drive)
and, to publish, a GITHUB_TOKEN that may edit releases — i.e. it is meant to
run from .github/workflows/regenerate_episode.yml. Exit status 1 when any
episode could not be regenerated; a failed one keeps its old asset.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from config import (  # noqa: E402
    AUDIO_DIR,
    CLAUDE_CONNECTIONS_MODEL,
    EPISODES_FILE,
    FEED_URL,
    GEMINI_API_KEY,
    GOOGLE_APPLICATION_CREDENTIALS,
    GOOGLE_DRIVE_FOLDER_ID,
    RELATED_WORK_ENABLED,
    RELATED_WORK_MAX,
    TTS_COHOST_VOICE,
    TTS_HOST_VOICE,
    ZETTELKASTEN_SUMMARY_BASE,
    ZETTELKASTEN_TARBALL_URL,
    anthropic_credential_source,
)
from audio_levels import analyze, format_time  # noqa: E402
from feed_parser import fetch_feed, parse_papers  # noqa: E402
from repair_audio_levels import update_data_files  # noqa: E402


def _require(name: str, value) -> None:
    if not value:
        print(f"Error: {name} not set")
        sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-render published episodes and replace their audio in place."
    )
    ap.add_argument("ids", nargs="+", metavar="EPISODE_ID",
                    help="episode ids (asset name without .mp3, e.g. Topinka2026-hb)")
    ap.add_argument("--no-upload", action="store_true",
                    help="render into audio/ only; leave the release and data files alone")
    args = ap.parse_args()

    _require("GEMINI_API_KEY", GEMINI_API_KEY)
    _require("Anthropic credentials", anthropic_credential_source())
    _require("GOOGLE_APPLICATION_CREDENTIALS", GOOGLE_APPLICATION_CREDENTIALS)

    # Imports that need credentials or heavy SDKs stay below the checks so a
    # misconfigured run fails with a message, not a traceback.
    from drive_client import DriveClient
    from gemini_audio import GeminiAudioGenerator
    from kasten_client import fetch_vault_bundle
    from related_work import select_related
    from summary_client import fetch_summary

    with open(EPISODES_FILE) as fh:
        episodes = {
            ep["id"].replace("bibtex:", ""): ep
            for ep in json.load(fh).get("episodes", [])
        }
    unknown = [i for i in args.ids if i not in episodes]
    if unknown:
        print(f"Error: not in {os.path.relpath(EPISODES_FILE, ROOT)}: {unknown}")
        return 2

    print(f"Fetching toread feed from: {FEED_URL}")
    papers = {p.id: p for p in parse_papers(fetch_feed(FEED_URL))}

    os.makedirs(AUDIO_DIR, exist_ok=True)
    drive_client = DriveClient(
        credentials_path=GOOGLE_APPLICATION_CREDENTIALS, folder_id=GOOGLE_DRIVE_FOLDER_ID
    )
    audio_generator = GeminiAudioGenerator(api_key=GEMINI_API_KEY)
    audio_generator.VOICES['host'] = TTS_HOST_VOICE
    audio_generator.VOICES['cohost'] = TTS_COHOST_VOICE

    vault_bundle = None
    if RELATED_WORK_ENABLED:
        try:
            vault_bundle = fetch_vault_bundle(ZETTELKASTEN_TARBALL_URL)
        except Exception as e:  # never worth failing a regeneration over
            print(f"  Vault unavailable ({e}); episodes will have no connections")

    rendered: dict[str, tuple[int, int]] = {}
    failed: dict[str, str] = {}

    for eid in args.ids:
        ep = episodes[eid]
        paper = papers.get(ep["id"])
        print(f"\n{'=' * 60}\nRegenerating: {ep['title']}\nID: {ep['id']}\n{'=' * 60}")
        if paper is None:
            failed[eid] = "paper is no longer in the toread feed"
            print(f"  {failed[eid]}")
            continue

        paper_text = drive_client.get_pdf_text(paper)
        if not paper_text:
            failed[eid] = "no PDF text from Drive"
            print(f"  {failed[eid]}")
            continue
        print(f"  Extracted {len(paper_text)} characters from Drive PDF")

        summary = fetch_summary(paper.id, ZETTELKASTEN_SUMMARY_BASE)
        related = []
        if vault_bundle:
            try:
                related = select_related(
                    paper.id, paper.title, vault_bundle,
                    audio_generator.script_generator, CLAUDE_CONNECTIONS_MODEL,
                    summary=summary, content_text=paper.content_text or "",
                    paper_text=paper_text, max_n=RELATED_WORK_MAX,
                )
            except Exception as e:
                print(f"  Related work unavailable ({e}); continuing without connections")

        audio_path = os.path.join(AUDIO_DIR, f"{eid}.mp3")
        result = audio_generator.generate_podcast(
            paper_text, paper.title, audio_path, summary=summary, related=related
        )
        if not result:
            failed[eid] = "generation failed (see log); old asset kept"
            print(f"  {failed[eid]}")
            continue
        if result.episode_title:
            # Deliberately not applied: the published title stays stable.
            print(f"  (new script's title, not applied: {result.episode_title})")

        profile = analyze(audio_path)
        size = os.path.getsize(audio_path)
        duration = audio_generator.get_audio_duration(audio_path)
        print(f"  Rendered {size} bytes, {format_time(duration)}, "
              f"level gate {'FLAGGED' if profile.flagged else 'ok'}, "
              f"opening {profile.baseline_db:.1f} dBFS")
        if profile.flagged:  # generate_audio already enforces this; belt and braces
            failed[eid] = "mastered audio failed the level gate"
            continue
        rendered[eid] = (size, duration)

    uploaded: dict[str, tuple[int, int]] = {}
    if rendered and not args.no_upload:
        from github_uploader import upload_audio_to_release
        for eid, sz in rendered.items():
            if upload_audio_to_release(os.path.join(AUDIO_DIR, f"{eid}.mp3")):
                uploaded[eid] = sz
            else:
                failed[eid] = "upload failed; release asset may be missing — re-run"
        if uploaded:
            n_eps, n_items = update_data_files(uploaded)
            print(f"\nUpdated {n_eps} episode(s) in episodes.json and {n_items} item(s) in feed.xml")

    print("\n" + "=" * 60)
    print(f"Regenerated: {len(rendered)}   Published: {len(uploaded)}   Failed: {len(failed)}")
    for eid, why in failed.items():
        print(f"  {eid}: {why}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
