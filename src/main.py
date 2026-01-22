#!/usr/bin/env python3
"""
Research Radio - Main Orchestrator

Converts academic papers into podcast episodes using:
- PaperPile PDFs from Google Drive
- Gemini for script generation and TTS
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    FEED_URL,
    PROCESSED_FILE,
    AUDIO_DIR,
    GEMINI_API_KEY,
    GOOGLE_APPLICATION_CREDENTIALS,
    GOOGLE_DRIVE_FOLDER_ID,
    TTS_HOST_VOICE,
    TTS_COHOST_VOICE,
)
from src.feed_parser import get_new_papers, save_processed_id, Paper, parse_papers, fetch_feed, load_processed_ids
from src.drive_client import DriveClient
from src.gemini_audio import GeminiAudioGenerator
from src.feed_generator import (
    create_episode_from_paper,
    add_episode,
    generate_podcast_feed,
    load_episodes,
)

# Rate limiting: minimum hours between episode publications
MIN_HOURS_BETWEEN_EPISODES = 24
from src.github_uploader import upload_audio_to_release


def sanitize_filename(paper_id: str) -> str:
    """Convert paper ID to a safe filename."""
    # Remove 'bibtex:' prefix and replace unsafe characters
    name = paper_id.replace('bibtex:', '').replace('/', '_').replace('\\', '_')
    return name[:100]  # Limit length


def can_publish_new_episode() -> tuple[bool, str]:
    """
    Check if enough time has passed since the last episode to publish a new one.

    Returns:
        Tuple of (can_publish: bool, reason: str)
    """
    episodes = load_episodes()

    if not episodes:
        return True, "No existing episodes"

    # Find the most recent episode by publication date
    latest_episode = max(episodes, key=lambda e: e.pub_date)
    time_since_last = datetime.now(timezone.utc) - latest_episode.pub_date
    hours_since_last = time_since_last.total_seconds() / 3600

    if hours_since_last >= MIN_HOURS_BETWEEN_EPISODES:
        return True, f"{hours_since_last:.1f} hours since last episode"
    else:
        hours_remaining = MIN_HOURS_BETWEEN_EPISODES - hours_since_last
        return False, f"Only {hours_since_last:.1f} hours since last episode. Wait {hours_remaining:.1f} more hours."


def process_paper(
    paper: Paper,
    drive_client: DriveClient,
    audio_generator: GeminiAudioGenerator,
) -> bool:
    """
    Process a single paper through the pipeline.

    Returns True if successful, False otherwise.
    """
    print(f"\n{'='*60}")
    print(f"Processing: {paper.title}")
    print(f"ID: {paper.id}")
    print(f"Authors: {', '.join(paper.authors) if paper.authors else 'Unknown'}")
    print('='*60)

    # Step 1: Find and extract text from PDF in Drive
    print("\n[1/3] Finding PDF in Google Drive...")
    paper_text = drive_client.get_pdf_text(paper)
    if not paper_text:
        print("  Failed to find or extract PDF. Skipping paper.")
        return False
    print(f"  Extracted {len(paper_text)} characters")

    # Step 2: Generate podcast audio with Gemini
    print("\n[2/3] Generating podcast with Gemini...")
    audio_filename = f"{sanitize_filename(paper.id)}.mp3"
    audio_path = os.path.join(AUDIO_DIR, audio_filename)

    # Set custom voices if configured
    audio_generator.VOICES['host'] = TTS_HOST_VOICE
    audio_generator.VOICES['cohost'] = TTS_COHOST_VOICE

    podcast_result = audio_generator.generate_podcast(paper_text, paper.title, audio_path)
    if not podcast_result:
        print("  Failed to generate podcast. Skipping paper.")
        return False

    # Use generated episode title if available, otherwise fall back to paper title
    episode_title = podcast_result.episode_title or paper.title

    audio_size = os.path.getsize(podcast_result.audio_path)
    audio_duration = audio_generator.get_audio_duration(podcast_result.audio_path)
    print(f"  Audio: {audio_filename} ({audio_size / 1024 / 1024:.1f}MB, {audio_duration // 60}:{audio_duration % 60:02d})")

    # Step 3: Upload to GitHub Release
    print("\n[3/3] Uploading to GitHub Release...")
    upload_success = upload_audio_to_release(audio_path)
    if not upload_success:
        print("  Failed to upload audio. Skipping episode to prevent orphan entry.")
        return False

    # Create and save episode - use current date (when episode is created)
    pub_date = datetime.now(timezone.utc)

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
        episode_title=episode_title
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


def main():
    """Main entry point."""
    print("="*60)
    print("Research Radio - Paper to Podcast Generator")
    print("="*60)

    # Validate configuration
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set")
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

    # Get new papers with PDFs in Drive (only from last 30 days)
    print(f"\nFetching feed from: {FEED_URL}")
    print(f"Checking Drive folder: {GOOGLE_DRIVE_FOLDER_ID}")
    print(f"Only processing PDFs modified in the last 30 days")

    papers = get_papers_from_drive(drive_client, PROCESSED_FILE, max_age_days=30)

    if not papers:
        print("\nNo new papers with matching PDFs found.")
        return

    print(f"\nFound {len(papers)} new paper(s) with PDFs in Drive:")
    for i, paper in enumerate(papers, 1):
        print(f"  {i}. {paper.title}")

    # Check rate limiting - only publish if enough time has passed
    can_publish, reason = can_publish_new_episode()
    print(f"\nRate limit check: {reason}")

    if not can_publish:
        print(f"Skipping processing to avoid overloading listeners.")
        print(f"Papers will be queued for future runs.")
        return

    # Process only ONE paper per run to maintain regular publishing schedule
    # This creates a natural queue when multiple papers are available
    paper_to_process = papers[0]
    remaining = len(papers) - 1

    print(f"\nProcessing 1 paper (rate limit: 1 per {MIN_HOURS_BETWEEN_EPISODES} hours)")
    if remaining > 0:
        print(f"  {remaining} paper(s) queued for future runs")

    successful = 0
    failed = 0

    try:
        if process_paper(paper_to_process, drive_client, audio_generator):
            successful += 1
        else:
            failed += 1
    except Exception as e:
        print(f"\nError processing paper: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

    # Generate updated feed
    print("\n" + "="*60)
    print("Generating podcast feed...")
    generate_podcast_feed()

    # Summary
    print("\n" + "="*60)
    print("Summary:")
    print(f"  Processed: {successful}")
    print(f"  Failed: {failed}")
    if remaining > 0:
        print(f"  Queued for later: {remaining}")
    print("="*60)


if __name__ == "__main__":
    main()
