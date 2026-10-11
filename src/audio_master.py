"""
Mastering — the one ffmpeg pass every published MP3 goes through.

Gemini's multi-speaker TTS has, on 38 of the 41 episodes published between
2026-07-11 and 2026-10-08, let the speech level sink 10-45 dB a few minutes in
and stay there (Bruns2026-pn: normal for 3.5 min, then ~30 dB down for the
remaining six). The same chain is used by gemini_audio on fresh renders and
by scripts/repair_audio_levels.py on already-published files, so a repaired
episode and a new one sound alike.

  dynaudnorm  lifts quiet stretches back towards the opening level: 500 ms
              frames, a 31-frame Gaussian window so the gain moves over ~15 s
              rather than pumping, peak target 0.9, up to 40x gain.
  loudnorm    then sets the whole episode to podcast loudness: -16 LUFS,
              -1.5 dBTP, loudness range 11.

Tested on Bruns2026-pn: the late section comes back to about -20 dBFS against
-17 at the start, from -55 before.
"""

import os
import subprocess

from audio_levels import AudioToolError

MASTER_FILTER = "dynaudnorm=f=500:g=31:p=0.9:m=40,loudnorm=I=-16:TP=-1.5:LRA=11"
MASTER_SAMPLE_RATE = 44100
MASTER_TIMEOUT_SECONDS = 10 * 60


def master_audio(
    raw_path: str,
    output_path: str,
    timeout: int = MASTER_TIMEOUT_SECONDS,
) -> None:
    """Master raw_path (MP3 or WAV) into output_path as a 44.1 kHz VBR-q2 MP3.

    Raises AudioToolError when ffmpeg is missing or fails; on failure no
    output file is left behind.
    """
    cmd = [
        'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', raw_path,
        '-vn',
        '-af', MASTER_FILTER,
        '-ar', str(MASTER_SAMPLE_RATE),
        '-codec:a', 'libmp3lame', '-qscale:a', '2',
        output_path,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as e:
        raise AudioToolError("ffmpeg not found. Please install ffmpeg.") from e
    except subprocess.TimeoutExpired as e:
        _discard(output_path)
        raise AudioToolError(
            f"ffmpeg exceeded {timeout}s mastering {raw_path}"
        ) from e
    if proc.returncode != 0 or not os.path.exists(output_path):
        _discard(output_path)
        raise AudioToolError(
            f"ffmpeg mastering failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[-300:]}"
        )


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
