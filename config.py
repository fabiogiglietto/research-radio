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

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

# Feed
FEED_URL = os.getenv(
    "FEED_URL",
    "https://raw.githubusercontent.com/fabiogiglietto/toread/main/output/feed.json"
)

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
EPISODES_FILE = os.path.join(DATA_DIR, "episodes.json")
FEED_FILE = os.path.join(DOCS_DIR, "feed.xml")

# Gemini TTS voices (options: Puck, Charon, Kore, Fenrir, Aoede)
TTS_HOST_VOICE = os.getenv("TTS_HOST_VOICE", "Kore")
TTS_COHOST_VOICE = os.getenv("TTS_COHOST_VOICE", "Charon")
