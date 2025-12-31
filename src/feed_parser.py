"""
Feed Parser - Fetches and parses the JSON feed of academic papers.
"""

import json
import re
import requests
from typing import Optional
from dataclasses import dataclass


@dataclass
class Paper:
    """Represents a paper from the feed."""
    id: str
    title: str
    url: str
    external_url: Optional[str]
    content_text: Optional[str]
    content_html: Optional[str]
    date_published: Optional[str]
    authors: list[str]
    pdf_url: Optional[str] = None

    def __post_init__(self):
        """Extract PDF URL from available fields."""
        self.pdf_url = self._find_pdf_url()

    def _find_pdf_url(self) -> Optional[str]:
        """Try to find a PDF URL from the paper's metadata."""
        # Check external_url first
        if self.external_url and self._is_pdf_url(self.external_url):
            return self.external_url

        # Look for PDF links in content_html
        if self.content_html:
            pdf_urls = self._extract_pdf_links(self.content_html)
            if pdf_urls:
                return pdf_urls[0]

        # Check if main URL is a PDF
        if self.url and self._is_pdf_url(self.url):
            return self.url

        return None

    def _is_pdf_url(self, url: str) -> bool:
        """Check if URL likely points to a PDF."""
        url_lower = url.lower()
        return (
            url_lower.endswith('.pdf') or
            '/pdf/' in url_lower or
            'arxiv.org/pdf' in url_lower or
            '.pdf?' in url_lower or
            'journals.sagepub.com/doi/reader/' in url_lower
        )

    def _extract_pdf_links(self, html: str) -> list[str]:
        """Extract PDF links from HTML content."""
        # Match href attributes containing PDF links
        pattern = r'href=["\']([^"\']*\.pdf[^"\']*)["\']'
        matches = re.findall(pattern, html, re.IGNORECASE)

        # Also look for arxiv PDF links
        arxiv_pattern = r'href=["\']([^"\']*arxiv\.org/pdf[^"\']*)["\']'
        arxiv_matches = re.findall(arxiv_pattern, html, re.IGNORECASE)

        return matches + arxiv_matches

    def has_accessible_pdf(self) -> bool:
        """Check if paper has a potentially accessible PDF."""
        return self.pdf_url is not None


def fetch_feed(feed_url: str) -> dict:
    """Fetch the JSON feed from the given URL."""
    response = requests.get(feed_url, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_papers(feed_data: dict) -> list[Paper]:
    """Parse the feed data into Paper objects."""
    papers = []

    for item in feed_data.get('items', []):
        # Extract authors
        authors = []
        for author in item.get('authors', []):
            if isinstance(author, dict):
                authors.append(author.get('name', 'Unknown'))
            else:
                authors.append(str(author))

        paper = Paper(
            id=item.get('id', ''),
            title=item.get('title', 'Untitled'),
            url=item.get('url', ''),
            external_url=item.get('external_url'),
            content_text=item.get('content_text'),
            content_html=item.get('content_html'),
            date_published=item.get('date_published'),
            authors=authors
        )
        papers.append(paper)

    return papers


def get_papers_with_pdfs(feed_url: str) -> list[Paper]:
    """Fetch feed and return only papers with accessible PDFs."""
    feed_data = fetch_feed(feed_url)
    papers = parse_papers(feed_data)
    return [p for p in papers if p.has_accessible_pdf()]


def load_processed_ids(processed_file: str) -> set[str]:
    """Load the set of already processed paper IDs."""
    try:
        with open(processed_file, 'r') as f:
            data = json.load(f)
            return set(data.get('processed_papers', []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_processed_id(processed_file: str, paper_id: str):
    """Add a paper ID to the processed list."""
    processed = load_processed_ids(processed_file)
    processed.add(paper_id)

    with open(processed_file, 'w') as f:
        json.dump({'processed_papers': list(processed)}, f, indent=2)


def get_new_papers(feed_url: str, processed_file: str) -> list[Paper]:
    """Get papers that have PDFs and haven't been processed yet."""
    papers = get_papers_with_pdfs(feed_url)
    processed_ids = load_processed_ids(processed_file)
    return [p for p in papers if p.id not in processed_ids]
