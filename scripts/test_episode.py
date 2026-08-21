#!/usr/bin/env python3
"""
Smoke-test podcast generation with the new Claude script method.

Runs script (Claude) -> episode title (Claude) -> audio (Gemini TTS) for ONE
paper and saves the MP3 locally for review. It does NOT upload to GitHub and
does NOT modify episodes.json / feed.xml / processed.json — safe to run while
the production pipeline is live.

Usage:
    python scripts/test_episode.py                  # first Drive paper, full run
    python scripts/test_episode.py --script-only    # Claude script only, skip TTS
    python scripts/test_episode.py --text-file paper.txt --title "A title"
                                                    # bypass Drive, use a text file
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Mirror production's effective sys.path (repo root + src/) so the repo's
# mixed import styles resolve exactly as they do under `python src/main.py`.
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from config import (
    GEMINI_API_KEY,
    ANTHROPIC_API_KEY,
    anthropic_credential_source,
    CLAUDE_SCRIPT_MODEL,
    CLAUDE_TITLE_MODEL,
    GOOGLE_APPLICATION_CREDENTIALS,
    GOOGLE_DRIVE_FOLDER_ID,
    TTS_HOST_VOICE,
    TTS_COHOST_VOICE,
    AUDIO_DIR,
    PROCESSED_FILE,
    ZETTELKASTEN_SUMMARY_BASE,
)


def _require(name: str, value) -> None:
    if not value:
        print(f"Error: {name} not set (check research-radio/.env)")
        sys.exit(1)


def get_paper_text_from_drive():
    """Pick the first unprocessed paper with a Drive PDF; return (title, text, id)."""
    _require("GOOGLE_APPLICATION_CREDENTIALS", GOOGLE_APPLICATION_CREDENTIALS)
    from src.drive_client import DriveClient
    from src.main import get_papers_from_drive

    drive = DriveClient(GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_DRIVE_FOLDER_ID)
    # Large max_age so any paper with a matching PDF qualifies.
    papers = get_papers_from_drive(drive, PROCESSED_FILE, max_age_days=36500)
    if not papers:
        print("No unprocessed papers with a matching Drive PDF were found.")
        sys.exit(1)
    paper = papers[0]
    print(f"Test paper: {paper.title}")
    text = drive.get_pdf_text(paper)
    if not text:
        print("Could not extract text from the PDF.")
        sys.exit(1)
    print(f"Extracted {len(text)} characters of paper text.\n")
    return paper.title, text, paper.id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test Claude-scripted podcast generation (no publishing)."
    )
    parser.add_argument("--text-file", help="use this text file instead of Drive")
    parser.add_argument("--title", help="paper title (used with --text-file)")
    parser.add_argument("--script-only", action="store_true", help="skip Gemini TTS")
    args = parser.parse_args()

    # Accepts a federated credential too, so this smoke test works anywhere
    # the real pipeline does.
    if anthropic_credential_source() is None:
        raise SystemExit("Missing Anthropic credentials: set ANTHROPIC_API_KEY "
                         "(or the ANTHROPIC_FEDERATION_* quartet)")
    if not args.script_only:
        _require("GEMINI_API_KEY", GEMINI_API_KEY)

    if args.text_file:
        with open(args.text_file, encoding="utf-8") as fh:
            text = fh.read()
        title = args.title or os.path.splitext(os.path.basename(args.text_file))[0]
        paper_id = None
        print(f"Test paper (from file): {title}\n")
    else:
        title, text, paper_id = get_paper_text_from_drive()

    # fg-zettelkasten summary scaffold (Part B).
    summary = None
    if paper_id:
        from src.summary_client import fetch_summary

        summary = fetch_summary(paper_id, ZETTELKASTEN_SUMMARY_BASE)
    print(
        "Summary scaffold: "
        + ("fg-zettelkasten summary found" if summary else "none — PDF text only")
        + "\n"
    )

    # Step 1: Claude writes the dialogue script.
    from src.claude_script_generator import ClaudeScriptGenerator

    script_gen = ClaudeScriptGenerator(
        ANTHROPIC_API_KEY, CLAUDE_SCRIPT_MODEL, title_model=CLAUDE_TITLE_MODEL
    )
    print(f"[1] Generating script with Claude ({CLAUDE_SCRIPT_MODEL})...")
    script = script_gen.generate_script(
        text, title, TTS_HOST_VOICE, TTS_COHOST_VOICE, summary=summary
    )
    if not script:
        print("Script generation failed.")
        sys.exit(1)
    print("\n===== SCRIPT =====")
    print(script)
    print("===== END SCRIPT =====\n")

    # Step 2: Claude writes the episode title.
    print("[2] Generating episode title with Claude...")
    ep_title = script_gen.generate_episode_title(script, title)
    print(f"Episode title: {ep_title}\n")

    if args.script_only:
        print("--script-only: skipped Gemini TTS. Script test complete.")
        return

    # Step 3: Gemini TTS renders the audio (unchanged production path).
    from src.gemini_audio import GeminiAudioGenerator

    audio_gen = GeminiAudioGenerator(api_key=GEMINI_API_KEY)
    audio_gen.VOICES["host"] = TTS_HOST_VOICE
    audio_gen.VOICES["cohost"] = TTS_COHOST_VOICE

    print("[3] Generating audio with Gemini TTS...")
    os.makedirs(AUDIO_DIR, exist_ok=True)
    out_path = os.path.join(AUDIO_DIR, "TEST_episode.mp3")
    if not audio_gen.generate_audio(script, out_path):
        print("Audio generation failed.")
        sys.exit(1)

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    duration = audio_gen.get_audio_duration(out_path)
    print(f"\n✓ Test MP3: {out_path} ({size_mb:.1f} MB, {duration // 60}:{duration % 60:02d})")
    print("  NOT uploaded, NOT added to the feed — safe test. Listen and review.")


if __name__ == "__main__":
    main()
