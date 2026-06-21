"""
Google Drive Client - Finds and downloads PDFs from PaperPile's Drive folder.
"""

import io
import re
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

from feed_parser import Paper


class DriveClient:
    """Client for accessing PDFs stored in Google Drive by PaperPile."""

    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

    def __init__(self, credentials_path: str, folder_id: str):
        """
        Initialize the Drive client.

        Args:
            credentials_path: Path to service account JSON credentials
            folder_id: Google Drive folder ID where PaperPile stores PDFs
        """
        self.folder_id = folder_id
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=self.SCOPES
        )
        self.service = build('drive', 'v3', credentials=credentials)
        self._file_cache: dict[str, dict] = {}

    def _build_search_name(self, paper: Paper) -> str:
        """
        Build the expected filename pattern from paper metadata.
        PaperPile format: [FirstAuthor] [Year] - [Title].pdf
        Or for multiple authors: [FirstAuthor] et al. [Year] - [Title].pdf

        Example: "Matias 2025 - How public involvement can improve the science of AI.pdf"
        Example: "Pierri et al. 2025 - Research opportunities and challenges.pdf"
        """
        # Extract first author's last name
        first_author = "Unknown"
        if paper.authors and len(paper.authors) > 0:
            author_name = paper.authors[0]
            # Handle names like "J. Nathan Matias" -> "Matias"
            parts = author_name.split()
            if parts:
                first_author = parts[-1]  # Last word is typically the surname

        # Add "et al." for multiple authors
        author_part = first_author
        if paper.authors and len(paper.authors) > 1:
            author_part = f"{first_author} et al."

        # Extract year from date_published or fall back to ID
        year = ""
        if paper.date_published:
            # ISO format: 2025-12-02T00:00:00Z
            match = re.search(r'(\d{4})', paper.date_published)
            if match:
                year = match.group(1)

        if not year:
            # Try to extract from ID like "bibtex:Matias2025-px"
            match = re.search(r'(\d{4})', paper.id)
            if match:
                year = match.group(1)

        # Build the search pattern
        if year:
            return f"{author_part} {year} - {paper.title}"
        else:
            return f"{author_part} - {paper.title}"

    def _normalize_for_search(self, text: str) -> str:
        """Normalize text for fuzzy matching."""
        # Remove special characters, lowercase, collapse whitespace
        text = re.sub(r'[^\w\s]', '', text.lower())
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # Words too generic to carry identifying signal in a title-token match.
    _STOPWORDS = frozenset(
        "the a an of and or to in for on with as is are be how does do what when "
        "why who from by into across our we their its more than not at it this that "
        "can could should about over under between among new using toward towards".split()
    )

    def _title_tokens(self, text: str) -> set[str]:
        """Significant lowercase word tokens of a title/filename (>2 chars,
        non-stopword), used for overlap-based matching."""
        return {
            w for w in self._normalize_for_search(text).split()
            if len(w) > 2 and w not in self._STOPWORDS
        }

    def _list_folder_files(self) -> list[dict]:
        """List all PDF files in the PaperPile folder."""
        if self._file_cache:
            return list(self._file_cache.values())

        files = []
        page_token = None

        while True:
            query = f"'{self.folder_id}' in parents and mimeType='application/pdf'"
            results = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, size, modifiedTime)",
                pageSize=1000,
                pageToken=page_token
            ).execute()

            files.extend(results.get('files', []))
            page_token = results.get('nextPageToken')

            if not page_token:
                break

        # Cache for future lookups
        self._file_cache = {f['name']: f for f in files}
        return files

    def find_pdf(self, paper: Paper) -> Optional[dict]:
        """
        Find a PDF file in Drive that matches the paper.

        Returns file metadata dict with 'id', 'name', 'size' or None if not found.
        """
        expected_name = self._build_search_name(paper)

        files = self._list_folder_files()

        # Try exact match first (case-insensitive)
        for file in files:
            if file['name'].lower() == f"{expected_name.lower()}.pdf":
                return file

        # Title-gated token matching. The title is the only reliably
        # disambiguating signal: a raw author-surname substring match (the old
        # behaviour) silently picks a same-author sibling, or — for short
        # surnames like "Li"/"Shi" — an unrelated file, and the gate used to
        # pass on author+year alone with NO title match. We instead require a
        # genuine overlap of the paper's title words with the filename, and use
        # author/year only as tie-breakers. If nothing clears the title gate we
        # return None (skip the paper) rather than a wrong PDF.
        title_tokens = self._title_tokens(paper.title)
        if not title_tokens:
            return None

        author_last = ""
        if paper.authors:
            parts = paper.authors[0].split()
            if parts:
                author_last = self._normalize_for_search(parts[-1])

        year = ""
        if paper.date_published:
            m = re.search(r'(\d{4})', paper.date_published)
            if m:
                year = m.group(1)

        TITLE_THRESHOLD = 0.6  # fraction of title words that must appear

        best_match = None
        best_key = (0.0, False, False)
        for file in files:
            stem = file['name'].rsplit('.pdf', 1)[0]
            file_tokens = self._title_tokens(stem)
            if not file_tokens:
                continue

            overlap = len(title_tokens & file_tokens) / len(title_tokens)
            if overlap < TITLE_THRESHOLD:
                continue

            # Tie-breakers: whole-token surname match (>=3 chars to avoid "li"
            # matching mid-word) and the publication year in the filename.
            author_hit = len(author_last) >= 3 and author_last in file_tokens
            year_hit = bool(year) and year in file['name']

            key = (overlap, author_hit, year_hit)
            if key > best_key:
                best_key = key
                best_match = file

        return best_match

    def download_pdf(self, file_id: str) -> Optional[bytes]:
        """Download a PDF file from Drive by its ID."""
        try:
            request = self.service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            return buffer.getvalue()
        except Exception as e:
            print(f"Error downloading file {file_id}: {e}")
            return None

    def get_pdf_text(self, paper: Paper, max_chars: int = 80000) -> Optional[str]:
        """
        Find and extract text from the PDF matching the paper.

        Args:
            paper: Paper object with metadata for matching
            max_chars: Maximum characters to return (default 80000)

        Returns:
            Extracted text or None if PDF not found/unreadable
        """
        file_info = self.find_pdf(paper)
        if not file_info:
            print(f"PDF not found for: {paper.title}")
            return None

        print(f"Found PDF: {file_info['name']}")

        pdf_bytes = self.download_pdf(file_info['id'])
        if not pdf_bytes:
            return None

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text_parts = []

            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

            full_text = '\n\n'.join(text_parts)

            # Clean up whitespace
            full_text = re.sub(r'\s+', ' ', full_text)
            full_text = re.sub(r'\n{3,}', '\n\n', full_text)

            # Truncate if needed
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + "\n\n[Text truncated...]"

            return full_text.strip()

        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return None
