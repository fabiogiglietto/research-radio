"""
Gemini Audio Generator - Generates podcast audio directly from paper text.

Uses Gemini to generate a conversation script, then Gemini TTS for multi-speaker audio.
"""

import os
import wave
import subprocess
from typing import Optional
from google import genai
from google.genai import types


class GeminiAudioGenerator:
    """Generates podcast audio using Gemini AI."""

    # Available voices for multi-speaker TTS
    VOICES = {
        'host': 'Kore',      # First host - warm, engaging
        'cohost': 'Charon',  # Second host - analytical, curious
    }

    # Model IDs
    SCRIPT_MODEL = 'gemini-2.0-flash'  # For generating conversation
    TTS_MODEL = 'gemini-2.5-flash-preview-tts'  # For multi-speaker audio

    def __init__(self, api_key: str):
        """Initialize with Gemini API key."""
        self.client = genai.Client(api_key=api_key)

    def generate_script(self, paper_text: str, paper_title: str) -> Optional[str]:
        """
        Generate a podcast conversation script from paper text.

        Returns formatted dialogue like:
        Host: Welcome to Research Radio...
        Cohost: Great to be here...
        """
        host_name = self.VOICES.get('host', 'Kore')
        cohost_name = self.VOICES.get('cohost', 'Charon')

        prompt = f"""You are a podcast script writer. Create an engaging episode of "FG's Research Radio",
a podcast featuring deep dive discussions on recent academic papers in computational social science,
platform studies, misinformation research, and the evolving landscape of social media and AI.

The conversation should be between two hosts:
- Host (named {host_name}): The main host who guides the discussion and provides context
- Cohost (named {cohost_name}): A co-host who offers analysis, asks probing questions, and adds perspective

Important: This is a discussion ABOUT the paper by two podcast hosts. They are NOT the authors
and should not pretend to be. They should refer to the authors in third person (e.g., "The
researchers found..." or "According to the authors...").

Guidelines:
- Start by welcoming listeners to Research Radio, have the hosts briefly introduce themselves by name, then introduce the paper's topic and authors
- Explain the key findings and methodology in accessible terms
- Have both hosts share insights and build on each other's points
- Discuss implications and significance for the field
- End with takeaways for the audience
- At the very end, the host should remind listeners that if they want to read the full paper, they can find the complete reference in the episode description, and encourage them to subscribe on Spotify and Apple Podcasts
- Use natural, conversational language
- Target length: 8-12 minutes of dialogue (roughly 1200-1800 words)
- Format each line exactly as "Host: [dialogue]" or "Cohost: [dialogue]"

Paper Title: {paper_title}

Paper Content:
{paper_text[:60000]}

Generate the podcast script now:"""

        try:
            response = self.client.models.generate_content(
                model=self.SCRIPT_MODEL,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"Error generating script: {e}")
            return None

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
            response = self.client.models.generate_content(
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
            audio_data = response.candidates[0].content.parts[0].inline_data.data

            # Save as WAV first
            wav_path = output_path.replace('.mp3', '.wav')
            self._save_wav(wav_path, audio_data)

            # Convert to MP3
            if output_path.endswith('.mp3'):
                return self._convert_to_mp3(wav_path, output_path)

            return True

        except Exception as e:
            print(f"Error generating audio: {e}")
            return False

    def _save_wav(self, path: str, pcm_data: bytes, rate: int = 24000):
        """Save raw PCM data as WAV file."""
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
            # Clean up WAV file
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
    ) -> Optional[str]:
        """
        Generate a complete podcast episode from paper text.

        This is the main entry point - generates script and audio in one call.

        Args:
            paper_text: Full text content of the paper
            paper_title: Title of the paper
            output_path: Where to save the MP3 file

        Returns:
            Path to generated audio file, or None if failed
        """
        print(f"Generating podcast for: {paper_title}")

        # Step 1: Generate conversation script
        print("  Generating script...")
        script = self.generate_script(paper_text, paper_title)
        if not script:
            print("  Failed to generate script")
            return None

        # Step 2: Convert to audio
        print("  Converting to audio...")
        if self.generate_audio(script, output_path):
            print(f"  Saved to: {output_path}")
            return output_path

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
                text=True
            )
            return int(float(result.stdout.strip()))
        except Exception:
            # Estimate based on file size (~16KB per second for MP3)
            try:
                size = os.path.getsize(file_path)
                return size // 16000
            except Exception:
                return 600  # Default 10 minutes
