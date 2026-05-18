import os
from dotenv import load_dotenv

load_dotenv()

# Google Cloud / Drive
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GOOGLE_DRIVE_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_FOLDER_ID",
    "1gluNDqRQkyqxa_WIASaaoNEItrDlETkn"  # PaperPile PDFs folder
)

# Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_SCRIPT_MODEL = os.getenv("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")

# Anthropic / Claude API (writes the podcast dialogue script)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_SCRIPT_MODEL = os.getenv("CLAUDE_SCRIPT_MODEL", "claude-opus-4-7")

# fg-zettelkasten structured summaries — optional podcast-script scaffold.
# GitHub Contents API directory (served fresh, unlike the ~5-min-cached
# raw.githubusercontent.com CDN — the summary is committed moments before
# research-radio runs, so a cached fetch would miss it).
ZETTELKASTEN_SUMMARY_BASE = os.getenv(
    "ZETTELKASTEN_SUMMARY_BASE",
    "https://api.github.com/repos/fabiogiglietto/fg-zettelkasten/contents/data/summaries",
)

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

# Feed
FEED_URL = os.getenv(
    "FEED_URL",
    "https://raw.githubusercontent.com/fabiogiglietto/toread/main/output/feed.json"
)

# Own-publications feed — Fabio Giglietto's own papers, published by
# fabiogiglietto.github.io. A podcast is generated only for the recent ones
# (OWN_PAPERS_MIN_YEAR onward); older own papers are intentionally skipped.
OWN_FEED_URL = os.getenv(
    "OWN_FEED_URL",
    "https://raw.githubusercontent.com/fabiogiglietto/fabiogiglietto.github.io/main/public/data/own-publications.json"
)
OWN_PAPERS_MIN_YEAR = int(os.getenv("OWN_PAPERS_MIN_YEAR", "2026"))

# Podcast metadata
PODCAST_TITLE = os.getenv("PODCAST_TITLE", "Research Radio")
PODCAST_DESCRIPTION = os.getenv(
    "PODCAST_DESCRIPTION",
    "AI-generated podcast discussions of academic papers"
)
PODCAST_AUTHOR = os.getenv("PODCAST_AUTHOR", "Research Radio")
PODCAST_EMAIL = os.getenv("PODCAST_EMAIL", "")
PODCAST_WEBSITE = os.getenv("PODCAST_WEBSITE", "")

# Paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
AUDIO_DIR = os.path.join(PROJECT_ROOT, "audio")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
CREDENTIALS_DIR = os.path.join(PROJECT_ROOT, "credentials")
PROCESSED_FILE = os.path.join(DATA_DIR, "processed.json")
EPISODES_FILE = os.path.join(DOCS_DIR, "episodes.json")
FEED_FILE = os.path.join(DOCS_DIR, "feed.xml")

# Gemini TTS voices (options: Puck, Charon, Kore, Fenrir, Aoede)
TTS_HOST_VOICE = os.getenv("TTS_HOST_VOICE", "Kore")
TTS_COHOST_VOICE = os.getenv("TTS_COHOST_VOICE", "Charon")
