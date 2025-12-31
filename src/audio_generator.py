"""
Audio Generator - Uses Google Cloud Text-to-Speech API with MultiSpeaker support.
"""

import base64
import os
from typing import Optional

from google.cloud import texttospeech_v1beta1 as texttospeech
from google.oauth2 import service_account

from config import (
    GOOGLE_APPLICATION_CREDENTIALS,
    TTS_VOICE,
    TTS_LANGUAGE,
    TTS_EFFECTS_PROFILE,
    AUDIO_DIR,
)


def get_tts_client() -> texttospeech.TextToSpeechClient:
    """Get an authenticated TTS client."""
    if GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_APPLICATION_CREDENTIALS
        )
        return texttospeech.TextToSpeechClient(credentials=credentials)
    else:
        # Use default credentials (e.g., from gcloud auth)
        return texttospeech.TextToSpeechClient()


def generate_audio(script: dict, output_path: str) -> bool:
    """
    Generate audio from a multiSpeakerMarkup script.

    Args:
        script: The script dict with multiSpeakerMarkup format
        output_path: Path to save the MP3 file

    Returns:
        True if successful, False otherwise
    """
    try:
        client = get_tts_client()

        # Build the synthesis input with multi-speaker markup
        multi_speaker_markup = texttospeech.MultiSpeakerMarkup()

        for turn in script.get('multiSpeakerMarkup', {}).get('turns', []):
            turn_obj = texttospeech.MultiSpeakerMarkup.Turn(
                text=turn['text'],
                speaker=turn['speaker']
            )
            multi_speaker_markup.turns.append(turn_obj)

        synthesis_input = texttospeech.SynthesisInput(
            multi_speaker_markup=multi_speaker_markup
        )

        # Configure the voice
        voice = texttospeech.VoiceSelectionParams(
            language_code=TTS_LANGUAGE,
            name=TTS_VOICE
        )

        # Configure audio output
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            effects_profile_id=[TTS_EFFECTS_PROFILE],
            speaking_rate=1.0,
        )

        # Generate the audio
        print("Generating audio with Google TTS...")
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Save the audio
        with open(output_path, 'wb') as f:
            f.write(response.audio_content)

        print(f"Audio saved to: {output_path}")
        return True

    except Exception as e:
        print(f"Error generating audio: {e}")
        return False


def generate_audio_from_script_json(script: dict, paper_id: str) -> Optional[str]:
    """
    Generate audio from a script and save it with a paper-based filename.

    Args:
        script: The multiSpeakerMarkup script
        paper_id: The paper ID (used for filename)

    Returns:
        Path to the generated audio file, or None if failed
    """
    # Create a safe filename from paper ID
    safe_id = "".join(c if c.isalnum() or c in '-_' else '_' for c in paper_id)
    safe_id = safe_id[:100]  # Limit length

    output_path = os.path.join(AUDIO_DIR, f"{safe_id}.mp3")

    if generate_audio(script, output_path):
        return output_path
    return None


def get_audio_duration(file_path: str) -> int:
    """
    Get the duration of an MP3 file in seconds.

    This is a simple estimation based on file size.
    For accurate duration, you'd need to parse the MP3.
    """
    try:
        file_size = os.path.getsize(file_path)
        # Rough estimate: 128kbps MP3 = ~16KB per second
        duration = file_size / (128 * 1024 / 8)
        return int(duration)
    except Exception:
        return 0
