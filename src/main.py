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
)
from src.github_uploader import upload_audio_to_release


def sanitize_filename(paper_id: str) -> str:
    """Convert paper ID to a safe filename."""
    # Remove 'bibtex:' prefix and replace unsafe characters
    name = paper_id.replace('bibtex:', '').replace('/', '_').replace('\\', '_')
    return name[:100]  # Limit length


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

    result = audio_generator.generate_podcast(paper_text, paper.title, audio_path)
    if not result:
        print("  Failed to generate podcast. Skipping paper.")
        return False

    audio_size = os.path.getsize(audio_path)
    audio_duration = audio_generator.get_audio_duration(audio_path)
    print(f"  Audio: {audio_filename} ({audio_size / 1024 / 1024:.1f}MB, {audio_duration // 60}:{audio_duration % 60:02d})")

    # Step 3: Upload to GitHub Release
    print("\n[3/3] Uploading to GitHub Release...")
    upload_success = upload_audio_to_release(audio_path)
    if not upload_success:
        print("  Warning: Failed to upload. Episode will be added but audio URL may not work.")

    # Create and save episode
    pub_date = datetime.now()
    if paper.date_published:
        try:
            pub_date = datetime.fromisoformat(paper.date_published.replace('Z', '+00:00'))
        except ValueError:
            pass

    episode = create_episode_from_paper(
        paper_id=paper.id,
        paper_title=paper.title,
        paper_authors=paper.authors,
        audio_filename=audio_filename,
        audio_size=audio_size,
        duration=audio_duration,
        pub_date=pub_date
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

    # Process each paper
    successful = 0
    failed = 0

    for paper in papers:
        try:
            if process_paper(paper, drive_client, audio_generator):
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
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print("="*60)


if __name__ == "__main__":
    main()
