# Research Radio

Paper-to-podcast pipeline that converts academic papers into AI-generated podcast episodes.

## Tech Stack

- Python 3.11+
- Anthropic Claude API (dialogue script generation)
- Google Gemini API (TTS)
- Google Drive API (PDF retrieval)
- GitHub Releases (audio hosting)
- feedgen (RSS generation)

## Project Structure

```
src/
  main.py           # Main orchestrator - entry point
  feed_parser.py    # Fetches papers from JSON feed
  drive_client.py   # Google Drive PDF retrieval
  gemini_audio.py   # Orchestrates Claude script generation + Gemini TTS
  github_uploader.py # Upload audio to GitHub Releases
  feed_generator.py # RSS podcast feed generation
scripts/
  validate_sync.py  # Validates feed/audio sync
data/
  processed.json    # Track processed paper IDs
docs/
  episodes.json     # Episode metadata
  feed.xml          # RSS podcast feed
audio/              # Generated MP3 files (local)
```

## Running

```bash
# Activate virtualenv
source venv/bin/activate

# Run main pipeline (processes one paper per run)
python src/main.py

# Validate feed sync
python scripts/validate_sync.py
```

## Configuration

All config in `config.py`, values loaded from `.env`:
- `ANTHROPIC_API_KEY` - Required for dialogue script generation (local only;
  CI has no key and authenticates by Workload Identity Federation — the
  single-use OIDC assertion is minted in-process, once per token exchange,
  by `src/anthropic_credentials.py`)
- `GEMINI_API_KEY` - Required for TTS
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to service account JSON
- `GOOGLE_DRIVE_FOLDER_ID` - Paperpile PDFs folder
- `GITHUB_TOKEN`, `GITHUB_REPO` - For uploading releases
- `TTS_HOST_VOICE`, `TTS_COHOST_VOICE` - Voice options: Puck, Charon, Kore, Fenrir, Aoede

## Key Behaviors

- Generation is unthrottled: every queued paper gets a podcast each run.
  Publication is paced instead — each episode is scheduled into the next free
  RSS slot (`next_publish_slot`), `MIN_HOURS_BETWEEN_EPISODES` apart.
- `generate_podcast_feed()` emits only episodes whose `pub_date` has arrived,
  so Spotify/Apple see ~one per day; `episodes.json` keeps every episode, and
  audio is on GitHub Releases immediately (so the vault note can link it).
- Each script also weaves in 2-3 connections to papers the show has already
  covered (`src/related_work.py`). Candidates are our own published, non-own
  episodes (`docs/episodes.json`); the substance comes from the fg-zettelkasten
  vault, fetched once per run as a tarball (`src/kasten_client.py`) and ranked
  with BM25. One Claude call (`CLAUDE_CONNECTIONS_MODEL`, Opus by default)
  turns the shortlist into an anchored brief; the script writer may assert
  nothing about an earlier paper beyond what that brief states. Every failure
  path yields no connections and a normal episode; `RELATED_WORK_ENABLED=false`
  turns it off.
- Only processes PDFs modified in last 30 days
- Papers tracked in `data/processed.json` to avoid reprocessing
- Audio uploaded to GitHub Releases, URLs in `docs/episodes.json`
- Two input sources (see `main.py`): the **toread feed** (needs a Paperpile
  Drive PDF) and the **own-publications feed** — Fabio Giglietto's own papers
  from `fabiogiglietto.github.io`, processed only for `OWN_PAPERS_MIN_YEAR`
  onward. Own-paper full text comes from the open-access PDF the feed points to
  (green OA deposited in ORA); a paper with no PDF is skipped — no abstract
  fallback (`resolve_own_paper_text`). The episode is tagged `own: true`.

## Pipeline position

Research-Radio is the **second stage** of a four-repo pipeline:
`toread` → **research-radio** → `fabiogiglietto.github.io` → `fg-zettelkasten`.
It consumes the JSON feed produced by **ToRead** and publishes
`docs/episodes.json` + audio Releases, which the two downstream repos consume.

Full DAG and orchestration model:
https://github.com/fabiogiglietto/toread/blob/main/PIPELINE.md

**ToRead feed location** (fetched live — never a local sibling working copy):
`https://raw.githubusercontent.com/fabiogiglietto/toread/main/output/feed.json`

**Own-publications feed** (the author's own papers, fetched live):
`https://raw.githubusercontent.com/fabiogiglietto/fabiogiglietto.github.io/main/public/data/own-publications.json`
Same JSON Feed 1.1 shape; podcasts generated for `OWN_PAPERS_MIN_YEAR`+ only.

**Feed format:** JSON Feed 1.1 with `_academic` extensions. Full contract:
https://github.com/fabiogiglietto/toread/blob/main/SCHEMA.md

When debugging feed issues, check the toread project's feed generation logic
(`src/rss_generator.py`).
