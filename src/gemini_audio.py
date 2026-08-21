"""
Gemini Audio Generator - Generates podcast audio from a paper.

The dialogue script is written by Claude (see claude_script_generator);
Gemini is used only for multi-speaker TTS.
"""

import os
import subprocess
import httpx
import time
from typing import Optional
from dataclasses import dataclass
from google import genai
from google.genai import types

from config import (
    GEMINI_TTS_MODEL,
    ANTHROPIC_API_KEY,
    CLAUDE_SCRIPT_MODEL,
    CLAUDE_TITLE_MODEL,
)
from claude_script_generator import ClaudeScriptGenerator


@dataclass
class PodcastResult:
    """Result of podcast generation."""
    audio_path: str
    episode_title: Optional[str] = None


class GeminiAudioGenerator:
    """Generates podcast episodes: Claude writes the script, Gemini TTS renders audio."""

    # Available voices for multi-speaker TTS
    VOICES = {
        'host': 'Kore',      # First host - warm, engaging
        'cohost': 'Charon',  # Second host - analytical, curious
    }

    # Model IDs (defaults, overridden by config)
    TTS_MODEL = GEMINI_TTS_MODEL

    # Retry settings
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2  # seconds

    # google-genai leaves HttpOptions.timeout at None, which reaches httpx as
    # timeout=None — no timeout at all. When the TTS endpoint degraded on
    # 2026-08-18 the request simply never returned: three runs burned GitHub's
    # full 6h job ceiling, and one took 4h52m before the server hung up with
    # "Server disconnected without sending a response". A healthy multi-speaker
    # render of a 12-minute episode takes 4.5-5 min (measured 2026-08-21), so
    # 8 min is generous headroom while still failing long before the 25m the
    # workflow allows the whole generator.
    # NOTE: HttpOptions.timeout is in MILLISECONDS (google-genai converts it to
    # seconds for httpx internally). Verified against 2.10.0, the version
    # requirements.lock installs in CI.
    TTS_TIMEOUT_MS = 8 * 60 * 1000

    # Ceiling on all attempts for one episode, so a wedged endpoint costs one
    # paper rather than starving every paper behind it in the 25m the workflow
    # allows the whole generator.
    #
    # A new attempt only starts if the budget can still absorb it in full
    # (delay + another TTS_TIMEOUT_MS), which makes the worst case per episode
    # ~8 min rather than the ~16 min it would be if the check only looked at
    # time already spent. The practical effect: a fast transient error still
    # retries — the 502 observed on 2026-08-21 came back in 13s and cleared on
    # the first retry — while a full 8-minute timeout does not, because a
    # second 8-minute wait cannot fit. That is the intended trade: one slow
    # episode must not consume the run.
    TTS_TOTAL_BUDGET_SECONDS = 15 * 60

    def __init__(self, api_key: str):
        """Initialize with the Gemini API key.

        The Claude script generator is built from config (ANTHROPIC_API_KEY,
        CLAUDE_SCRIPT_MODEL). ANTHROPIC_API_KEY is None under Workload Identity
        Federation, which ClaudeScriptGenerator handles by constructing the SDK
        client zero-arg so it federates instead.
        """
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=self.TTS_TIMEOUT_MS),
        )
        self.script_generator = ClaudeScriptGenerator(
            ANTHROPIC_API_KEY, CLAUDE_SCRIPT_MODEL, title_model=CLAUDE_TITLE_MODEL
        )

    def _generate_with_retry(self, **kwargs):
        """Call generate_content with retry on transient errors.

        A request that exceeds TTS_TIMEOUT_MS raises httpx.TimeoutException
        rather than hanging. That counts as transient — the endpoint stalling
        for one attempt says nothing about the next — but every attempt shares
        TTS_TOTAL_BUDGET_SECONDS so a persistently wedged endpoint costs this
        episode a bounded amount of time instead of the whole run.
        """
        deadline = time.monotonic() + self.TTS_TOTAL_BUDGET_SECONDS
        for attempt in range(self.MAX_RETRIES):
            try:
                return self.client.models.generate_content(**kwargs)
            except Exception as e:
                # Timeouts carry no HTTP status, so match the type rather than
                # the string — str(httpx.ReadTimeout) is often just ''.
                # google-genai can also be backed by httpx2, a drop-in fork
                # whose exceptions are not httpx subclasses; match those by
                # module so the branch cannot be silently unreachable, without
                # catching every unrelated class that happens to say "Timeout".
                is_timeout = isinstance(e, (httpx.TimeoutException, TimeoutError)) or (
                    type(e).__module__.split(".")[0] == "httpx2"
                    and "Timeout" in type(e).__name__
                )
                error_str = str(e)
                is_transient = is_timeout or any(
                    code in error_str
                    for code in ['429', '500', '502', '503', '504', 'RESOURCE_EXHAUSTED']
                )
                if is_timeout:
                    print(
                        f"  TTS request exceeded {self.TTS_TIMEOUT_MS // 1000}s "
                        f"(attempt {attempt + 1}/{self.MAX_RETRIES})"
                    )
                if not is_transient or attempt >= self.MAX_RETRIES - 1:
                    raise

                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                # Reserve room for the retry to run to its own full timeout,
                # otherwise the budget is only enforced after it has already
                # been overspent.
                next_attempt_worst_case = delay + self.TTS_TIMEOUT_MS / 1000
                if time.monotonic() + next_attempt_worst_case > deadline:
                    print(
                        f"  TTS retry budget ({self.TTS_TOTAL_BUDGET_SECONDS}s) "
                        f"cannot absorb another attempt; giving up on this episode."
                    )
                    raise
                print(f"  Retrying in {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                time.sleep(delay)

    def generate_audio(
        self,
        script: str,
        output_path: str,
        host_voice: str = None,
        cohost_voice: str = None,
    ) -> bool:
        """
        Convert a multi-speaker script to audio using Gemini TTS.

        Args:
            script: Formatted dialogue (Host: ... / Cohost: ...)
            output_path: Path to save the audio file (will save as .wav, convert to .mp3)
            host_voice: Voice name for host (default: Kore)
            cohost_voice: Voice name for cohost (default: Charon)

        Returns:
            True if successful, False otherwise
        """
        host_voice = host_voice or self.VOICES['host']
        cohost_voice = cohost_voice or self.VOICES['cohost']

        # Prepare the prompt for TTS
        tts_prompt = f"""Read this podcast conversation naturally with appropriate emotion and pacing:

{script}"""

        try:
            response = self._generate_with_retry(
                model=self.TTS_MODEL,
                contents=tts_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                            speaker_voice_configs=[
                                types.SpeakerVoiceConfig(
                                    speaker='Host',
                                    voice_config=types.VoiceConfig(
                                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                            voice_name=host_voice,
                                        )
                                    )
                                ),
                                types.SpeakerVoiceConfig(
                                    speaker='Cohost',
                                    voice_config=types.VoiceConfig(
                                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                            voice_name=cohost_voice,
                                        )
                                    )
                                ),
                            ]
                        )
                    )
                )
            )

            # Extract audio data
            if (not response.candidates
                    or not response.candidates[0].content.parts
                    or not response.candidates[0].content.parts[0].inline_data):
                raise ValueError("Gemini returned no audio data in response")
            audio_data = response.candidates[0].content.parts[0].inline_data.data

            # Check response mime type — use direct write for MP3, convert for PCM
            mime_type = response.candidates[0].content.parts[0].inline_data.mime_type
            if mime_type and 'mp3' in mime_type:
                with open(output_path, 'wb') as f:
                    f.write(audio_data)
            else:
                # Fallback: save as WAV then convert to MP3
                wav_path = output_path.replace('.mp3', '.wav')
                self._save_wav(wav_path, audio_data)
                if output_path.endswith('.mp3'):
                    return self._convert_to_mp3(wav_path, output_path)

            return True

        except Exception as e:
            print(f"Error generating audio: {e}")
            return False

    def _save_wav(self, path: str, pcm_data: bytes, rate: int = 24000):
        """Save raw PCM data as WAV file."""
        import wave
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm_data)

    def _convert_to_mp3(self, wav_path: str, mp3_path: str) -> bool:
        """Convert WAV to MP3 using ffmpeg."""
        try:
            subprocess.run(
                [
                    'ffmpeg', '-y', '-i', wav_path,
                    '-codec:a', 'libmp3lame', '-qscale:a', '2',
                    mp3_path
                ],
                check=True,
                capture_output=True
            )
            os.remove(wav_path)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error converting to MP3: {e}")
            return False
        except FileNotFoundError:
            print("ffmpeg not found. Please install ffmpeg.")
            return False

    def generate_podcast(
        self,
        paper_text: str,
        paper_title: str,
        output_path: str,
        summary: Optional[dict] = None,
        related: Optional[list] = None,
    ) -> Optional[PodcastResult]:
        """
        Generate a complete podcast episode from paper text.

        This is the main entry point - generates script (Claude) and audio
        (Gemini TTS) in one call.

        Args:
            paper_text: Full text content of the paper
            paper_title: Title of the paper
            output_path: Where to save the MP3 file
            summary: Optional fg-zettelkasten structured summary, passed to the
                script generator as a scaffold
            related: Optional connections brief (papers the show already
                covered that speak to this one), woven into the dialogue

        Returns:
            PodcastResult with audio path and episode title, or None if failed
        """
        print(f"Generating podcast for: {paper_title}")

        # Step 1: Generate conversation script (Claude)
        print("  Generating script...")
        script = self.script_generator.generate_script(
            paper_text,
            paper_title,
            host_name=self.VOICES.get('host', 'Kore'),
            cohost_name=self.VOICES.get('cohost', 'Charon'),
            summary=summary,
            related=related,
        )
        if not script:
            print("  Failed to generate script")
            return None

        # Step 2: Generate episode title from script (Claude)
        print("  Generating episode title...")
        episode_title = self.script_generator.generate_episode_title(script, paper_title)
        if episode_title:
            print(f"  Episode title: {episode_title}")
        else:
            print("  Warning: Failed to generate episode title, will use paper title")

        # Step 3: Convert to audio (Gemini TTS)
        print("  Converting to audio...")
        if self.generate_audio(script, output_path):
            print(f"  Saved to: {output_path}")
            return PodcastResult(audio_path=output_path, episode_title=episode_title)

        return None

    def get_audio_duration(self, file_path: str) -> int:
        """Get duration of audio file in seconds."""
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    file_path
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            return int(float(result.stdout.strip()))
        except Exception:
            # Estimate based on file size (~16KB per second for MP3)
            try:
                size = os.path.getsize(file_path)
                return size // 16000
            except Exception:
                return 600  # Default 10 minutes
