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

        Example: "Matias 2025 - How public involvement can improve the science of AI.pdf"
        """
        # Extract first author's last name
        first_author = "Unknown"
        if paper.authors and len(paper.authors) > 0:
            author_name = paper.authors[0]
            # Handle names like "J. Nathan Matias" -> "Matias"
            parts = author_name.split()
            if parts:
                first_author = parts[-1]  # Last word is typically the surname

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
            return f"{first_author} {year} - {paper.title}"
        else:
            return f"{first_author} - {paper.title}"

    def _normalize_for_search(self, text: str) -> str:
        """Normalize text for fuzzy matching."""
        # Remove special characters, lowercase, collapse whitespace
        text = re.sub(r'[^\w\s]', '', text.lower())
        text = re.sub(r'\s+', ' ', text).strip()
        return text

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
                fields="nextPageToken, files(id, name, size)",
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
        expected_normalized = self._normalize_for_search(expected_name)

        files = self._list_folder_files()

        # Try exact match first (case-insensitive)
        for file in files:
            if file['name'].lower() == f"{expected_name.lower()}.pdf":
                return file

        # Try fuzzy matching on normalized strings
        best_match = None
        best_score = 0

        for file in files:
            file_normalized = self._normalize_for_search(file['name'].replace('.pdf', ''))

            # Check if key parts match
            score = 0

            # Title match (most important)
            title_normalized = self._normalize_for_search(paper.title)
            if title_normalized in file_normalized:
                score += 50

            # Author match
            if paper.authors:
                author_last = paper.authors[0].split()[-1].lower()
                if author_last in file_normalized:
                    score += 30

            # Year match
            if paper.date_published:
                year_match = re.search(r'(\d{4})', paper.date_published)
                if year_match and year_match.group(1) in file['name']:
                    score += 20

            if score > best_score:
                best_score = score
                best_match = file

        # Require at least title match (score >= 50)
        if best_score >= 50:
            return best_match

        return None

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
