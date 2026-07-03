#!/usr/bin/env python3
"""
Research Radio - Main Orchestrator

Converts academic papers into podcast episodes using:
- PaperPile PDFs from Google Drive
- Claude for podcast script generation
- Gemini for multi-speaker TTS
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    FEED_URL,
    OWN_FEED_URL,
    OWN_PAPERS_MIN_YEAR,
    PROCESSED_FILE,
    AUDIO_DIR,
    GEMINI_API_KEY,
    ANTHROPIC_API_KEY,
    GOOGLE_APPLICATION_CREDENTIALS,
    GOOGLE_DRIVE_FOLDER_ID,
    TTS_HOST_VOICE,
    TTS_COHOST_VOICE,
    ZETTELKASTEN_SUMMARY_BASE,
)
from src.feed_parser import (
    get_new_papers,
    save_processed_id,
    Paper,
    parse_papers,
    parse_own_papers,
    fetch_feed,
    load_processed_ids,
)
from src.feed_contract import validate_toread_feed
from src.drive_client import DriveClient
from src.gemini_audio import GeminiAudioGenerator
from src.pdf_extractor import get_paper_text, truncate_text
from src.summary_client import fetch_summary
from src.feed_generator import (
    create_episode_from_paper,
    add_episode,
    generate_podcast_feed,
    next_publish_slot,
)
from src.platform_links import enrich_and_save

# Rate limiting: minimum hours between episode publications
MIN_HOURS_BETWEEN_EPISODES = 24
from src.github_uploader import upload_audio_to_release


def sanitize_filename(paper_id: str) -> str:
    """Convert paper ID to a safe filename."""
    # Remove 'bibtex:' prefix and replace unsafe characters
    name = paper_id.replace('bibtex:', '').replace('/', '_').replace('\\', '_')
    return name[:100]  # Limit length


def process_paper(
    paper: Paper,
    paper_text: str,
    audio_generator: GeminiAudioGenerator,
) -> bool:
    """
    Generate, upload and register a podcast episode for one paper.

    `paper_text` is the already-resolved source text — a PDF extract for
    toread papers, an open-access PDF or abstract for the author's own papers.
    The caller decides where it comes from.

    Returns True if successful, False otherwise.
    """
    print(f"\n{'='*60}")
    print(f"Processing: {paper.title}")
    print(f"ID: {paper.id}")
    print(f"Authors: {', '.join(paper.authors) if paper.authors else 'Unknown'}")
    print('='*60)

    # Step 1: Generate podcast (Claude script + Gemini TTS)
    print("\n[1/2] Generating podcast (Claude script + Gemini TTS)...")
    audio_filename = f"{sanitize_filename(paper.id)}.mp3"
    audio_path = os.path.join(AUDIO_DIR, audio_filename)

    # Set custom voices if configured
    audio_generator.VOICES['host'] = TTS_HOST_VOICE
    audio_generator.VOICES['cohost'] = TTS_COHOST_VOICE

    # Optional scaffold: the structured summary from the fg-zettelkasten vault.
    summary = fetch_summary(paper.id, ZETTELKASTEN_SUMMARY_BASE)
    if summary:
        print("  Using fg-zettelkasten summary as a scaffold")

    podcast_result = audio_generator.generate_podcast(
        paper_text, paper.title, audio_path, summary=summary
    )
    if not podcast_result:
        print("  Failed to generate podcast. Skipping paper.")
        return False

    # Use generated episode title if available, otherwise fall back to paper title
    episode_title = podcast_result.episode_title or paper.title

    audio_size = os.path.getsize(podcast_result.audio_path)
    audio_duration = audio_generator.get_audio_duration(podcast_result.audio_path)
    print(f"  Audio: {audio_filename} ({audio_size / 1024 / 1024:.1f}MB, {audio_duration // 60}:{audio_duration % 60:02d})")

    # Step 2: Upload to GitHub Release
    print("\n[2/2] Uploading to GitHub Release...")
    upload_success = upload_audio_to_release(audio_path)
    if not upload_success:
        print("  Failed to upload audio. Skipping episode to prevent orphan entry.")
        return False

    # Schedule the episode into the next free RSS slot. The audio is already
    # live on GitHub Releases (uploaded above); pub_date controls only when the
    # episode surfaces in the podcast feed, pacing listeners to one per slot.
    pub_date = next_publish_slot(MIN_HOURS_BETWEEN_EPISODES)
    print(f"  Scheduled for publication: {pub_date.strftime('%Y-%m-%d %H:%M UTC')}")

    # Extract year from date_published
    paper_year = None
    if paper.date_published:
        import re
        year_match = re.search(r'(\d{4})', paper.date_published)
        if year_match:
            paper_year = year_match.group(1)

    episode = create_episode_from_paper(
        paper_id=paper.id,
        paper_title=paper.title,
        paper_authors=paper.authors,
        audio_filename=audio_filename,
        audio_size=audio_size,
        duration=audio_duration,
        pub_date=pub_date,
        paper_url=paper.external_url or paper.url,
        paper_year=paper_year,
        episode_title=episode_title,
        is_own=paper.is_own
    )

    add_episode(episode)
    save_processed_id(PROCESSED_FILE, paper.id)

    print(f"\n  Successfully processed: {paper.title}")
    return True


def get_papers_from_drive(drive_client: DriveClient, processed_file: str, max_age_days: int = 30) -> list[Paper]:
    """
    Get papers from the feed that have matching PDFs in Drive.

    Returns only unprocessed papers that have a PDF in the Drive folder
    that was modified within the last max_age_days.
    """
    feed_data = fetch_feed(FEED_URL)
    validate_toread_feed(feed_data)
    all_papers = parse_papers(feed_data)
    processed_ids = load_processed_ids(processed_file)

    # Calculate cutoff date for recent PDFs
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')

    papers_with_pdfs = []
    for paper in all_papers:
        if paper.id in processed_ids:
            continue

        # Check if PDF exists in Drive
        pdf_info = drive_client.find_pdf(paper)
        if pdf_info:
            # Check if PDF was modified recently
            modified_time = pdf_info.get('modifiedTime', '')
            if modified_time >= cutoff_str:
                papers_with_pdfs.append(paper)

    return papers_with_pdfs


def resolve_own_paper_text(paper: Paper, drive_client: DriveClient) -> Optional[str]:
    """Full-text source for one of the author's own publications — a PDF or nothing.

    Order: a manually-added Paperpile Drive PDF, then the open-access PDF from
    the feed (the green-OA copy the author deposits in ORA). There is no
    abstract fallback: an own paper with no PDF is skipped, since an
    abstract-only episode would be too thin to publish.

    Returns the extracted text, or None when no PDF is available.
    """
    # 1. Manual override: a PDF dropped into the Paperpile Drive folder.
    text = drive_client.get_pdf_text(paper)
    if text:
        print(f"  Source text: Drive PDF ({len(text)} chars)")
        return text

    # 2. Open-access PDF advertised by the feed (resolved from ORA green OA).
    if paper.open_access_pdf_url:
        text = get_paper_text(paper.open_access_pdf_url)
        if text:
            text = truncate_text(text, 80000)
            print(f"  Source text: open-access PDF ({len(text)} chars)")
            return text
        print("  Open-access PDF could not be downloaded or read")

    return None


def get_own_papers(processed_file: str, min_year: int) -> list[Paper]:
    """Fetch the author's own publications; return recent, unprocessed ones.

    Only papers published in `min_year` or later are returned — older own
    papers are intentionally left without a podcast. Unlike toread papers
    these need no Drive PDF; text is resolved later (see resolve_own_paper_text).
    """
    try:
        feed_data = fetch_feed(OWN_FEED_URL)
    except Exception as e:
        print(f"  Could not fetch own-publications feed: {e}")
        return []

    processed_ids = load_processed_ids(processed_file)
    selected = []
    for paper in parse_own_papers(feed_data):
        if paper.id in processed_ids:
            continue
        if paper.year is None or paper.year < min_year:
            continue
        selected.append(paper)
    return selected


def main():
    """Main entry point."""
    print("="*60)
    print("Research Radio - Paper to Podcast Generator")
    print("="*60)

    # Validate configuration
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    if not GOOGLE_APPLICATION_CREDENTIALS:
        print("Error: GOOGLE_APPLICATION_CREDENTIALS not set")
        sys.exit(1)

    # Ensure directories exist
    os.makedirs(AUDIO_DIR, exist_ok=True)

    # Initialize clients
    print("\nInitializing clients...")
    drive_client = DriveClient(
        credentials_path=GOOGLE_APPLICATION_CREDENTIALS,
        folder_id=GOOGLE_DRIVE_FOLDER_ID
    )
    audio_generator = GeminiAudioGenerator(api_key=GEMINI_API_KEY)

    # Source 1 — the toread reading list (needs a recent Paperpile Drive PDF).
    print(f"\nFetching toread feed from: {FEED_URL}")
    print(f"Checking Drive folder: {GOOGLE_DRIVE_FOLDER_ID}")
    print("Only processing PDFs modified in the last 30 days")
    toread_papers = get_papers_from_drive(drive_client, PROCESSED_FILE, max_age_days=30)

    # Source 2 — the author's own publications (year >= OWN_PAPERS_MIN_YEAR).
    print(f"\nFetching own-publications feed from: {OWN_FEED_URL}")
    print(f"Only processing own papers published in {OWN_PAPERS_MIN_YEAR} or later")
    own_papers = get_own_papers(PROCESSED_FILE, OWN_PAPERS_MIN_YEAR)

    if not toread_papers and not own_papers:
        print("\nNo new papers found.")
        # Still regenerate the feed: a previously-queued episode may have
        # reached its scheduled slot and now needs to surface.
        print("Refreshing Spotify/Apple episode links...")
        print(f"  {enrich_and_save()} episode(s) updated")
        print("Regenerating feed so any due episodes can surface...")
        generate_podcast_feed()
        return

    print(f"\nFound {len(toread_papers)} toread paper(s) and "
          f"{len(own_papers)} own paper(s) to process:")
    for i, paper in enumerate(toread_papers + own_papers, 1):
        tag = " [own]" if paper.is_own else ""
        print(f"  {i}. {paper.title}{tag}")

    # Generate a podcast for every queued paper now — generation is no longer
    # rate-limited. Publication is paced separately: each episode is scheduled
    # into the next free RSS slot (see next_publish_slot / generate_podcast_feed),
    # so Spotify/Apple listeners still receive about one per slot.
    print(f"\nPublication paced one per {MIN_HOURS_BETWEEN_EPISODES}h")

    successful = 0
    failed = 0

    # toread papers — text comes from the matched Paperpile Drive PDF.
    for paper in toread_papers:
        try:
            paper_text = drive_client.get_pdf_text(paper)
            if not paper_text:
                print(f"\nNo PDF text for {paper.id}; skipping.")
                failed += 1
                continue
            print(f"  Extracted {len(paper_text)} characters from Drive PDF")
            if process_paper(paper, paper_text, audio_generator):
                successful += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\nError processing paper {paper.id}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    # Own publications — full text from a PDF; papers with no PDF are skipped.
    for paper in own_papers:
        try:
            print(f"\nResolving source text for own paper: {paper.title}")
            paper_text = resolve_own_paper_text(paper, drive_client)
            if not paper_text:
                print(f"No PDF for own paper {paper.id}; skipping.")
                failed += 1
                continue
            if process_paper(paper, paper_text, audio_generator):
                successful += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\nError processing own paper {paper.id}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    # Refresh per-episode Spotify/Apple links before regenerating the feed.
    print("\nRefreshing Spotify/Apple episode links...")
    print(f"  {enrich_and_save()} episode(s) updated")

    # Generate updated feed
    print("\n" + "="*60)
    print("Generating podcast feed...")
    generate_podcast_feed()

    # Summary
    print("\n" + "="*60)
    print("Summary:")
    print(f"  Generated: {successful}")
    print(f"  Failed: {failed}")
    print("="*60)


if __name__ == "__main__":
    main()
