"""
PDF Extractor - Downloads PDFs and extracts text content.
"""

import io
import requests
from typing import Optional
from pypdf import PdfReader


# Common user agent to avoid blocks
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Timeout for PDF downloads (seconds)
DOWNLOAD_TIMEOUT = 60

# Maximum PDF size to download (bytes) - 50MB
MAX_PDF_SIZE = 50 * 1024 * 1024


def download_pdf(url: str) -> Optional[bytes]:
    """
    Download a PDF from the given URL.

    Returns the PDF content as bytes, or None if download fails.
    """
    headers = {"User-Agent": USER_AGENT}

    try:
        # First, do a HEAD request to check size
        head_response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)

        content_length = head_response.headers.get('content-length')
        if content_length and int(content_length) > MAX_PDF_SIZE:
            print(f"PDF too large: {int(content_length) / 1024 / 1024:.1f}MB")
            return None

        # Download the PDF
        response = requests.get(
            url,
            headers=headers,
            timeout=DOWNLOAD_TIMEOUT,
            allow_redirects=True
        )
        response.raise_for_status()

        # Verify it's actually a PDF
        content_type = response.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and not response.content[:4] == b'%PDF':
            print(f"Not a PDF: content-type={content_type}")
            return None

        return response.content

    except requests.exceptions.Timeout:
        print(f"Timeout downloading PDF: {url}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error downloading PDF: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error downloading PDF: {e}")
        return None


def extract_text_from_pdf(pdf_content: bytes) -> Optional[str]:
    """
    Extract text from PDF content.

    Returns the extracted text, or None if extraction fails.
    """
    try:
        pdf_file = io.BytesIO(pdf_content)
        reader = PdfReader(pdf_file)

        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

        if not text_parts:
            print("No text could be extracted from PDF")
            return None

        full_text = "\n\n".join(text_parts)

        # Basic cleanup
        full_text = clean_extracted_text(full_text)

        return full_text

    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return None


def clean_extracted_text(text: str) -> str:
    """Clean up extracted PDF text."""
    # Remove excessive whitespace
    lines = text.split('\n')
    cleaned_lines = []

    for line in lines:
        # Strip whitespace
        line = line.strip()
        # Skip empty lines that are just whitespace
        if line:
            cleaned_lines.append(line)

    # Join with single newlines, but preserve paragraph breaks
    result = []
    prev_empty = False

    for line in cleaned_lines:
        if not line:
            if not prev_empty:
                result.append('')
                prev_empty = True
        else:
            result.append(line)
            prev_empty = False

    return '\n'.join(result)


def get_paper_text(pdf_url: str) -> Optional[str]:
    """
    Download a PDF and extract its text.

    This is the main entry point for the module.
    Returns the extracted text, or None if the paper is not accessible.
    """
    print(f"Downloading PDF: {pdf_url}")

    pdf_content = download_pdf(pdf_url)
    if pdf_content is None:
        return None

    print(f"Extracting text from PDF ({len(pdf_content) / 1024:.1f}KB)")

    text = extract_text_from_pdf(pdf_content)
    if text is None:
        return None

    print(f"Extracted {len(text)} characters of text")
    return text


def truncate_text(text: str, max_chars: int = 100000) -> str:
    """
    Truncate text to a maximum character count.

    This is useful for very long papers to avoid exceeding
    API limits on the LLM.
    """
    if len(text) <= max_chars:
        return text

    # Try to truncate at a paragraph break
    truncated = text[:max_chars]
    last_para = truncated.rfind('\n\n')

    if last_para > max_chars * 0.8:
        truncated = truncated[:last_para]

    return truncated + "\n\n[Content truncated due to length...]"
