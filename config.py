import os
from dotenv import load_dotenv

load_dotenv()

# Google Cloud / Drive
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GOOGLE_DRIVE_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_FOLDER_ID",
    "1gluNDqRQkyqxa_WIASaaoNEItrDlETkn"  # PaperPile PDFs folder
)

# Gemini API (multi-speaker TTS only; the script is written by Claude)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")

# Anthropic / Claude API (writes the podcast dialogue script)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def anthropic_credential_source():
    """Which credential the Anthropic SDK will resolve, or None if it finds none.

    Deliberately not a bare ANTHROPIC_API_KEY check. In CI the credentials come
    from Workload Identity Federation: the workflow writes a short-lived GitHub
    OIDC token to a file and the SDK exchanges it for an access token. There is
    no API key at all on that path, so gating on one would abort every CI run
    while local runs kept working.

    Federation needs the whole quartet; a partial set is a misconfiguration.
    """
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "api-key"
    federated = (
        os.getenv("ANTHROPIC_FEDERATION_RULE_ID")
        and os.getenv("ANTHROPIC_ORGANIZATION_ID")
        and os.getenv("ANTHROPIC_SERVICE_ACCOUNT_ID")
        and (os.getenv("ANTHROPIC_IDENTITY_TOKEN_FILE")
             or os.getenv("ANTHROPIC_IDENTITY_TOKEN"))
    )
    return "federation" if federated else None


# Opus, settled by an A/B on one paper and one fixed connections brief
# (scripts/test_episode.py --brief-file, same brief for both runs). Sonnet 5
# wrote a fine episode but clustered two of the three back-catalogue
# connections together near the end, which is the announced-segment shape the
# show deliberately avoids; Opus spread them across the episode and attached
# each to the point it bears on. Costs ~$0.15/episode more — worth it.
CLAUDE_SCRIPT_MODEL = os.getenv("CLAUDE_SCRIPT_MODEL", "claude-opus-5")
# Episode titles are classification-grade work — a cheap model suffices.
CLAUDE_TITLE_MODEL = os.getenv("CLAUDE_TITLE_MODEL", "claude-haiku-4-5")

# fg-zettelkasten structured summaries — optional podcast-script scaffold.
# GitHub Contents API directory (served fresh, unlike the ~5-min-cached
# raw.githubusercontent.com CDN — the summary is committed moments before
# research-radio runs, so a cached fetch would miss it).
ZETTELKASTEN_SUMMARY_BASE = os.getenv(
    "ZETTELKASTEN_SUMMARY_BASE",
    "https://api.github.com/repos/fabiogiglietto/fg-zettelkasten/contents/data/summaries",
)

# Related-work connections — weave links to papers the show already covered
# into the dialogue. The candidate pool is our own docs/episodes.json; the
# substance comes from the fg-zettelkasten vault, fetched once per run as a
# tarball (public, no auth). Off switch is a plain env var so a bad run can be
# neutralised without a deploy.
RELATED_WORK_ENABLED = os.getenv("RELATED_WORK_ENABLED", "true").lower() not in (
    "0", "false", "no", "off"
)
# Choosing and characterising a relationship between two papers is the part
# that fails badly when it fails — a plausible-but-false link is worse than
# silence. Kept as its own knob rather than reusing CLAUDE_SCRIPT_MODEL so the
# script model can be traded down for cost without also weakening selection.
# Input is ~8k tokens, so the cost of this call is noise either way.
CLAUDE_CONNECTIONS_MODEL = os.getenv("CLAUDE_CONNECTIONS_MODEL", "claude-opus-5")
ZETTELKASTEN_TARBALL_URL = os.getenv(
    "ZETTELKASTEN_TARBALL_URL",
    "https://api.github.com/repos/fabiogiglietto/fg-zettelkasten/tarball/main",
)
# Connections offered to the script writer. More than three cannot be woven
# into a 1200-1800 word episode without crowding out the paper itself.
RELATED_WORK_MAX = int(os.getenv("RELATED_WORK_MAX", "3"))

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

# Per-episode platform links (resolved by src/platform_links.py and written into
# docs/episodes.json for fg-zettelkasten to consume). Apple resolves per-episode
# URLs from the free iTunes Lookup API by show id. Spotify links point at the
# show page — its episode endpoint needs a user token, so per-episode deep links
# aren't reachable from CI.
APPLE_PODCAST_ID = os.getenv("APPLE_PODCAST_ID", "1866587707")
APPLE_PODCAST_COUNTRY = os.getenv("APPLE_PODCAST_COUNTRY", "us")
SPOTIFY_SHOW_ID = os.getenv("SPOTIFY_SHOW_ID", "5V99ieB2ljNvcwPZ53EoPX")

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
