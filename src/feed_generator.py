"""
Feed Generator - Creates RSS 2.0 podcast feed with iTunes extensions.
"""

import os
import json
import html
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, asdict

from feedgen.feed import FeedGenerator

from config import (
    PODCAST_TITLE,
    PODCAST_DESCRIPTION,
    PODCAST_AUTHOR,
    PODCAST_EMAIL,
    PODCAST_WEBSITE,
    DOCS_DIR,
    DATA_DIR,
    GITHUB_REPO,
)


@dataclass
class Episode:
    """Represents a podcast episode."""
    id: str
    title: str
    description: str
    audio_url: str
    audio_size: int
    duration: int  # seconds
    pub_date: datetime
    authors: list[str]
    own: bool = False  # True when the episode is one of the author's own papers
    spotify_url: str = ""  # per-episode Spotify URL (resolved in platform_links)
    apple_url: str = ""    # per-episode Apple Podcasts URL (resolved in platform_links)
    audio_mismatch: bool = False  # audio is the wrong paper; withhold from feed
                                  # until regenerated (see WRONG_AUDIO_REPORT.md)


EPISODES_FILE = os.path.join(DOCS_DIR, "episodes.json")


def load_episodes() -> list[Episode]:
    """Load episodes from the episodes file."""
    try:
        with open(EPISODES_FILE, 'r') as f:
            data = json.load(f)
            episodes = []
            for ep_data in data.get('episodes', []):
                pub_date = datetime.fromisoformat(ep_data['pub_date'])
                # Ensure timezone-aware for consistent sorting
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                ep_data['pub_date'] = pub_date
                episodes.append(Episode(**ep_data))
            return episodes
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_episodes(episodes: list[Episode]):
    """Save episodes to the episodes file."""
    data = {
        'episodes': [
            {**asdict(ep), 'pub_date': ep.pub_date.isoformat()}
            for ep in episodes
        ]
    }
    with open(EPISODES_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def add_episode(episode: Episode):
    """Add a new episode to the list."""
    episodes = load_episodes()

    # Check if episode already exists
    if any(ep.id == episode.id for ep in episodes):
        # Update existing
        episodes = [ep if ep.id != episode.id else episode for ep in episodes]
    else:
        episodes.append(episode)

    save_episodes(episodes)


def next_publish_slot(interval_hours: int) -> datetime:
    """Return the next free RSS publication slot.

    Episodes are revealed in the feed one per `interval_hours`: the slot is
    `interval_hours` after the latest already-scheduled episode, or now if that
    is in the past (or there are no episodes yet). The audio is uploaded to
    GitHub Releases immediately regardless — this only paces when an episode
    surfaces in the feed for Spotify/Apple listeners.
    """
    now = datetime.now(timezone.utc)
    episodes = load_episodes()
    if not episodes:
        return now
    latest = max(ep.pub_date for ep in episodes)
    candidate = latest + timedelta(hours=interval_hours)
    return candidate if candidate > now else now


def get_github_release_url(filename: str) -> str:
    """Get the GitHub release download URL for an audio file."""
    # Format: https://github.com/owner/repo/releases/download/TAG/filename
    # We'll use 'audio' as the release tag
    return f"https://github.com/{GITHUB_REPO}/releases/download/audio/{filename}"


def generate_podcast_feed(output_path: Optional[str] = None) -> str:
    """
    Generate the podcast RSS feed.

    Returns the path to the generated feed file.
    """
    if output_path is None:
        output_path = os.path.join(DOCS_DIR, "feed.xml")

    # Only episodes whose scheduled slot has arrived appear in the RSS feed —
    # this is the one-per-slot pace Spotify/Apple see. Audio for not-yet-due
    # episodes is already live on GitHub Releases and linked from the vault.
    #
    # Own-publication episodes (`own=True`) are excluded from the public RSS
    # feed entirely: they are surfaced only as podcast links on the author's
    # site (fabiogiglietto.github.io), never as episodes of the public show.
    # episodes.json still records them — that is what the website reads.
    # Episodes flagged `audio_mismatch` carry the wrong paper's audio (a
    # wrong-PDF generation bug) and are withheld from the public feed until
    # their audio is regenerated — see WRONG_AUDIO_REPORT.md.
    now = datetime.now(timezone.utc)
    episodes = [
        ep for ep in load_episodes()
        if ep.pub_date <= now and not ep.own and not ep.audio_mismatch
    ]

    # Sort episodes by date (newest first)
    episodes.sort(key=lambda e: e.pub_date, reverse=True)

    # Create the feed
    fg = FeedGenerator()
    fg.load_extension('podcast')

    # Basic feed info
    fg.title(PODCAST_TITLE)
    fg.description(PODCAST_DESCRIPTION)
    fg.link(href=PODCAST_WEBSITE or f"https://github.com/{GITHUB_REPO}", rel='alternate')
    fg.language('en')

    # Podcast-specific info
    fg.podcast.itunes_author(PODCAST_AUTHOR)
    fg.podcast.itunes_category('Science')
    fg.podcast.itunes_explicit('no')
    fg.podcast.itunes_owner(name=PODCAST_AUTHOR, email=PODCAST_EMAIL or 'noreply@example.com')
    fg.podcast.itunes_summary(PODCAST_DESCRIPTION)
    fg.podcast.itunes_image(f"{PODCAST_WEBSITE}/cover.png")

    # Add episodes
    for episode in episodes:
        fe = fg.add_entry()
        fe.id(episode.id)
        fe.title(episode.title)
        fe.description(episode.description)
        fe.pubDate(episode.pub_date)

        # Audio enclosure
        fe.enclosure(
            url=episode.audio_url,
            length=str(episode.audio_size),
            type='audio/mpeg'
        )

        # iTunes-specific
        fe.podcast.itunes_author(', '.join(episode.authors) if episode.authors else PODCAST_AUTHOR)
        fe.podcast.itunes_duration(format_duration(episode.duration))
        fe.podcast.itunes_summary(episode.description[:4000])  # iTunes limit

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Write the feed
    fg.rss_file(output_path)

    print(f"Feed generated: {output_path}")
    return output_path


def format_duration(seconds: int) -> str:
    """Format duration in HH:MM:SS for iTunes."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_authors_apa7(authors: list[str]) -> str:
    """Format authors list in APA7 style."""
    if not authors:
        return "Unknown"

    def format_name(name: str) -> str:
        """Convert 'First Middle Last' to 'Last, F. M.' format."""
        parts = name.strip().split()
        if len(parts) == 1:
            return parts[0]
        # Last name is typically the final part
        last = parts[-1]
        initials = '. '.join(p[0].upper() for p in parts[:-1]) + '.'
        return f"{last}, {initials}"

    formatted = [format_name(a) for a in authors]

    if len(formatted) == 1:
        return formatted[0]
    # APA 7 uses a comma before the ampersand for all multi-author references,
    # including exactly two authors ("Last, A., & Last, B.").
    else:
        return ', '.join(formatted[:-1]) + ', & ' + formatted[-1]


def create_episode_from_paper(
    paper_id: str,
    paper_title: str,
    paper_authors: list[str],
    audio_filename: str,
    audio_size: int,
    duration: int,
    pub_date: Optional[datetime] = None,
    paper_url: Optional[str] = None,
    paper_year: Optional[str] = None,
    episode_title: Optional[str] = None,
    is_own: bool = False
) -> Episode:
    """Create an Episode object from paper metadata."""
    if pub_date is None:
        pub_date = datetime.now(timezone.utc)

    if paper_year is None:
        paper_year = str(pub_date.year)

    # Feed paper titles arrive HTML-escaped (e.g. &#x27; for an apostrophe);
    # unescape so the citation and episode title read as plain text.
    paper_title = html.unescape(paper_title)

    # Build APA7-style citation
    authors_apa = format_authors_apa7(paper_authors)
    citation = f"{authors_apa} ({paper_year}). {paper_title}."
    if paper_url:
        citation += f" {paper_url}"

    description = f"AI-generated podcast discussion.\n\nReference:\n{citation}"

    # Use provided episode title or fall back to paper title
    title = html.unescape(episode_title) if episode_title else paper_title

    return Episode(
        id=paper_id,
        title=f"FG's Research Radio: {title}",
        description=description,
        audio_url=get_github_release_url(audio_filename),
        audio_size=audio_size,
        duration=duration,
        pub_date=pub_date,
        authors=paper_authors,
        own=is_own
    )
