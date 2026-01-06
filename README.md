# Paper-to-Podcast Generator

An automated pipeline that converts academic papers into podcast episodes using AI. It fetches papers from a reading list, retrieves PDFs from Google Drive, generates conversational scripts with Google Gemini, and produces multi-speaker audio using text-to-speech.

## About This Project

This repository contains the **code** for generating AI-powered podcast discussions of academic papers.

**Note:** "FG's Research Radio" is a podcast produced using this code, focusing on computational social science, platform studies, and misinformation research. If you use this code to create your own podcast, please choose a different name and branding for your show.

## Listen to FG's Research Radio

- [RSS Feed](https://fabiogiglietto.github.io/research-radio/feed.xml)
- [Spotify](https://open.spotify.com/show/5V99ieB2ljNvcwPZ53EoPX)
- [Apple Podcasts](https://podcasts.apple.com/us/podcast/research-radio/id1866587707)

## Related Project: ToRead

This project is designed to work with [ToRead](https://github.com/fabiogiglietto/toread), which converts Paperpile BibTeX exports into JSON feeds enriched with academic metadata (DOIs, citation counts, open access status).

**The full pipeline:**
1. **Paperpile** - Curate papers in your "To Read" folder
2. **ToRead** - Automatically exports to a JSON feed with enriched metadata
3. **Research-Radio** - Converts papers from the feed into podcast episodes

You can use research-radio with any JSON feed of papers, but it's optimized for the feed format produced by ToRead.

## Features

- Fetches papers from a JSON feed (compatible with [ToRead](https://github.com/fabiogiglietto/toread))
- Retrieves PDFs from Google Drive (Paperpile integration)
- Generates natural two-host conversation scripts using Gemini AI
- Produces multi-speaker audio with configurable voices
- Publishes as an RSS podcast feed
- Automated via GitHub Actions (hourly checks for new papers)

## Requirements

- Python 3.11+
- Google Cloud service account with Drive API access
- Gemini API key
- ffmpeg (for audio conversion)
- GitHub account (for releases and Actions)

## Setup

1. **Clone and install dependencies:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd YOUR_REPO
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Set up Google Cloud:**
   - Create a service account with Drive API access
   - Download the JSON key to `credentials/service-account.json`
   - Share your Drive folder with the service account email

4. **Configure GitHub Secrets** (for Actions):
   - `GCP_SA_KEY`: Contents of your service account JSON
   - `GEMINI_API_KEY`: Your Gemini API key

5. **Customize your podcast:**
   Edit `.env` to set your podcast name, description, and voices:
   ```
   PODCAST_TITLE=Your Podcast Name
   PODCAST_DESCRIPTION=Your podcast description
   PODCAST_AUTHOR=Your Name
   TTS_HOST_VOICE=Kore
   TTS_COHOST_VOICE=Charon
   ```

## Usage

**Run locally:**
```bash
python src/main.py
```

**Automated (GitHub Actions):**
The workflow runs hourly, checking for new papers and generating episodes automatically.

## How It Works

1. **Feed Parser** - Fetches papers from a JSON feed
2. **Drive Client** - Finds and downloads matching PDFs from Google Drive
3. **Gemini Audio** - Generates a two-host conversation script, then converts to audio
4. **GitHub Uploader** - Uploads audio files to GitHub Releases
5. **Feed Generator** - Creates/updates the RSS podcast feed

## Available TTS Voices

- Puck, Charon, Kore, Fenrir, Aoede

## License

This code is released under the MIT License. See [LICENSE](LICENSE) for details.

You are free to use, modify, and distribute this code to create your own paper-to-podcast pipeline. However, please create your own podcast identity (name, branding, description) rather than using "FG's Research Radio."

## Acknowledgments

Built with:
- [Google Gemini](https://ai.google.dev/) for script generation and TTS
- [Google Drive API](https://developers.google.com/drive) for PDF access
- [feedgen](https://github.com/lkiesow/python-feedgen) for RSS feed generation
