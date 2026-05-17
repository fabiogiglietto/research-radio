"""
Gemini Audio Generator - Generates podcast audio from a paper.

The dialogue script is written by Claude (see claude_script_generator);
Gemini is used only for multi-speaker TTS.
"""

import os
import subprocess
import time
from typing import Optional
from dataclasses import dataclass
from google import genai
from google.genai import types

from config import GEMINI_TTS_MODEL, ANTHROPIC_API_KEY, CLAUDE_SCRIPT_MODEL
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

    def __init__(self, api_key: str):
        """Initialize with the Gemini API key.

        The Claude script generator is built from config (ANTHROPIC_API_KEY,
        CLAUDE_SCRIPT_MODEL).
        """
        self.client = genai.Client(api_key=api_key)
        self.script_generator = ClaudeScriptGenerator(
            ANTHROPIC_API_KEY, CLAUDE_SCRIPT_MODEL
        )

    def _generate_with_retry(self, **kwargs):
        """Call generate_content with retry on transient errors."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return self.client.models.generate_content(**kwargs)
            except Exception as e:
                error_str = str(e)
                is_transient = any(code in error_str for code in ['429', '500', '502', '503', '504', 'RESOURCE_EXHAUSTED'])
                if is_transient and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"  Retrying in {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(delay)
                    continue
                raise

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
