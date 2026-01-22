#!/usr/bin/env python3
"""
Validate Sync - Check consistency between MP3 files, episodes.json, and processed.json

This script identifies:
1. MP3 files in GitHub releases that are missing from episodes.json
2. Episodes in episodes.json that reference non-existent MP3 files
3. Papers in processed.json that don't have corresponding episodes
4. Feed.xml entries that don't match episodes.json

Usage:
    python scripts/validate_sync.py [--fix] [--dry-run]

Options:
    --fix       Attempt to fix issues (add missing episodes, remove orphans)
    --dry-run   Simulate the main pipeline without making changes
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import GITHUB_REPO, FEED_URL, PROCESSED_FILE, DOCS_DIR


EPISODES_FILE = os.path.join(DOCS_DIR, "episodes.json")
FEED_FILE = os.path.join(DOCS_DIR, "feed.xml")
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/audio"


class ValidationResult:
    def __init__(self):
        self.mp3_without_episodes: list[str] = []  # MP3 exists, no episode
        self.episodes_without_mp3: list[str] = []  # Episode exists, no MP3
        self.processed_without_episodes: list[str] = []  # In processed, not in episodes
        self.episodes_without_processed: list[str] = []  # In episodes, not in processed
        self.feed_mismatch: list[str] = []  # Feed doesn't match episodes.json

    @property
    def has_issues(self) -> bool:
        return any([
            self.mp3_without_episodes,
            self.episodes_without_mp3,
            self.processed_without_episodes,
            self.episodes_without_processed,
            self.feed_mismatch
        ])

    def print_report(self):
        print("\n" + "=" * 60)
        print("VALIDATION REPORT")
        print("=" * 60)

        if not self.has_issues:
            print("\n✓ All checks passed! No sync issues found.")
            return

        if self.mp3_without_episodes:
            print(f"\n❌ MP3 files WITHOUT episodes ({len(self.mp3_without_episodes)}):")
            print("   These audio files exist in GitHub releases but have no episode entry.")
            for mp3 in self.mp3_without_episodes:
                print(f"   - {mp3}")

        if self.episodes_without_mp3:
            print(f"\n❌ Episodes WITHOUT MP3 files ({len(self.episodes_without_mp3)}):")
            print("   These episodes reference non-existent audio files.")
            for ep in self.episodes_without_mp3:
                print(f"   - {ep}")

        if self.processed_without_episodes:
            print(f"\n⚠ Processed papers WITHOUT episodes ({len(self.processed_without_episodes)}):")
            print("   These papers were marked as processed but have no episode.")
            for p in self.processed_without_episodes:
                print(f"   - {p}")

        if self.episodes_without_processed:
            print(f"\n⚠ Episodes NOT in processed list ({len(self.episodes_without_processed)}):")
            print("   These episodes exist but paper wasn't marked as processed.")
            for ep in self.episodes_without_processed:
                print(f"   - {ep}")

        if self.feed_mismatch:
            print(f"\n⚠ Feed.xml mismatches ({len(self.feed_mismatch)}):")
            for m in self.feed_mismatch:
                print(f"   - {m}")

        print("\n" + "=" * 60)


def get_mp3_files_from_releases() -> dict[str, dict]:
    """Fetch list of MP3 files from GitHub releases."""
    print("Fetching MP3 files from GitHub releases...")
    try:
        response = requests.get(GITHUB_API_URL, timeout=30)
        response.raise_for_status()
        data = response.json()

        mp3_files = {}
        for asset in data.get('assets', []):
            if asset['name'].endswith('.mp3'):
                paper_id = asset['name'].replace('.mp3', '')
                mp3_files[paper_id] = {
                    'name': asset['name'],
                    'size': asset['size'],
                    'url': asset['browser_download_url']
                }

        print(f"  Found {len(mp3_files)} MP3 files")
        return mp3_files
    except Exception as e:
        print(f"  Error fetching releases: {e}")
        return {}


def load_episodes() -> dict[str, dict]:
    """Load episodes from episodes.json."""
    print("Loading episodes.json...")
    try:
        with open(EPISODES_FILE, 'r') as f:
            data = json.load(f)

        episodes = {}
        for ep in data.get('episodes', []):
            # Extract paper ID (remove 'bibtex:' prefix)
            paper_id = ep['id'].replace('bibtex:', '')
            episodes[paper_id] = ep

        print(f"  Found {len(episodes)} episodes")
        return episodes
    except Exception as e:
        print(f"  Error loading episodes: {e}")
        return {}


def load_processed() -> set[str]:
    """Load processed paper IDs."""
    print("Loading processed.json...")
    try:
        with open(PROCESSED_FILE, 'r') as f:
            data = json.load(f)

        # Remove 'bibtex:' prefix for consistency
        processed = {p.replace('bibtex:', '') for p in data.get('processed_papers', [])}
        print(f"  Found {len(processed)} processed papers")
        return processed
    except Exception as e:
        print(f"  Error loading processed: {e}")
        return set()


def count_feed_items() -> int:
    """Count items in feed.xml."""
    print("Checking feed.xml...")
    try:
        with open(FEED_FILE, 'r') as f:
            content = f.read()
        count = content.count('<item>')
        print(f"  Found {count} items in feed")
        return count
    except Exception as e:
        print(f"  Error reading feed: {e}")
        return 0


def validate() -> ValidationResult:
    """Run all validation checks."""
    result = ValidationResult()

    # Load all data sources
    mp3_files = get_mp3_files_from_releases()
    episodes = load_episodes()
    processed = load_processed()
    feed_count = count_feed_items()

    mp3_ids = set(mp3_files.keys())
    episode_ids = set(episodes.keys())

    # Check 1: MP3 files without episodes
    result.mp3_without_episodes = sorted(mp3_ids - episode_ids)

    # Check 2: Episodes without MP3 files
    result.episodes_without_mp3 = sorted(episode_ids - mp3_ids)

    # Check 3: Processed without episodes
    result.processed_without_episodes = sorted(processed - episode_ids)

    # Check 4: Episodes without processed entry
    result.episodes_without_processed = sorted(episode_ids - processed)

    # Check 5: Feed count mismatch
    if feed_count != len(episodes):
        result.feed_mismatch.append(
            f"Feed has {feed_count} items but episodes.json has {len(episodes)}"
        )

    return result


def fetch_paper_metadata(paper_id: str) -> Optional[dict]:
    """Fetch paper metadata from the papers feed."""
    try:
        response = requests.get(FEED_URL, timeout=30)
        response.raise_for_status()
        data = response.json()

        for item in data.get('items', []):
            item_id = item.get('id', '').replace('bibtex:', '')
            if item_id == paper_id:
                authors = []
                for author in item.get('authors', []):
                    if isinstance(author, dict):
                        authors.append(author.get('name', 'Unknown'))
                    else:
                        authors.append(str(author))

                return {
                    'id': f"bibtex:{paper_id}",
                    'title': item.get('title', 'Untitled'),
                    'authors': authors,
                    'date_published': item.get('date_published'),
                    'external_url': item.get('external_url', item.get('url', ''))
                }
    except Exception as e:
        print(f"  Error fetching metadata for {paper_id}: {e}")

    return None


def dry_run_pipeline():
    """Simulate the main pipeline without making changes."""
    print("\n" + "=" * 60)
    print("DRY RUN - Simulating Pipeline")
    print("=" * 60)

    # Step 1: Fetch papers from feed
    print("\n[1/5] Fetching papers feed...")
    try:
        response = requests.get(FEED_URL, timeout=30)
        response.raise_for_status()
        feed_data = response.json()
        papers = feed_data.get('items', [])
        print(f"  Found {len(papers)} papers in feed")
    except Exception as e:
        print(f"  ❌ Error fetching feed: {e}")
        return

    # Step 2: Load processed papers
    print("\n[2/5] Loading processed papers...")
    processed = load_processed()

    # Step 3: Identify new papers
    print("\n[3/5] Identifying new papers...")
    new_papers = []
    for paper in papers:
        paper_id = paper.get('id', '').replace('bibtex:', '')
        if paper_id and paper_id not in processed:
            new_papers.append(paper)

    print(f"  Found {len(new_papers)} unprocessed papers")
    for p in new_papers[:5]:  # Show first 5
        print(f"    - {p.get('title', 'Untitled')[:60]}...")
    if len(new_papers) > 5:
        print(f"    ... and {len(new_papers) - 5} more")

    # Step 4: Check what would be generated
    print("\n[4/5] Checking current state...")
    mp3_files = get_mp3_files_from_releases()
    episodes = load_episodes()

    # Step 5: Validate consistency
    print("\n[5/5] Validating consistency...")
    result = validate()
    result.print_report()

    # Summary
    print("\n" + "=" * 60)
    print("DRY RUN SUMMARY")
    print("=" * 60)
    print(f"  Papers in feed:        {len(papers)}")
    print(f"  Already processed:     {len(processed)}")
    print(f"  New papers to process: {len(new_papers)}")
    print(f"  MP3 files in releases: {len(mp3_files)}")
    print(f"  Episodes in feed:      {len(episodes)}")

    if result.has_issues:
        print("\n  ⚠ ISSUES DETECTED - Run with --fix to repair")
    else:
        print("\n  ✓ No issues detected")


def main():
    parser = argparse.ArgumentParser(
        description="Validate sync between MP3 files, episodes, and processed papers"
    )
    parser.add_argument(
        '--fix',
        action='store_true',
        help='Attempt to fix issues (not implemented yet)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate the main pipeline'
    )

    args = parser.parse_args()

    if args.dry_run:
        dry_run_pipeline()
    else:
        result = validate()
        result.print_report()

        if args.fix and result.has_issues:
            print("\n⚠ --fix is not fully implemented yet.")
            print("  Please manually review and fix the issues above.")

    # Exit with error code if issues found
    if not args.dry_run:
        result = validate()
        sys.exit(1 if result.has_issues else 0)


if __name__ == "__main__":
    main()
