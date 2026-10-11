"""
Audio level profiling — detects an episode whose speech goes quiet part-way.

Bruns2026-pn (published 2026-07-19) played at a normal ~-18 dBFS for 3.5
minutes, faded over ~45 s, and spent its remaining six minutes ~30 dB lower.
Speech was still there, just inaudible next to the opening. Nothing in the
pipeline measured the output, so it shipped.

The check here is deliberately simple and runs on ffmpeg alone:

  1. Measure the mean volume (ffmpeg volumedetect) of consecutive windows,
     WINDOW_SECONDS long, across the whole file.
  2. Take the median of the windows inside the first BASELINE_SECONDS as the
     episode's own reference level — episodes differ in loudness, so the
     reference has to be relative, not an absolute dBFS figure.
  3. Any window that starts after SKIP_SECONDS and sits more than
     DROP_THRESHOLD_DB below that reference is "quiet". A run of quiet windows
     longer than MIN_DROP_DURATION seconds is a drop.

Used by scripts/audit_audio_levels.py over the back catalogue and by
gemini_audio as a publish gate on freshly rendered audio.
"""

import os
import re
import statistics
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

WINDOW_SECONDS = 20.0
BASELINE_SECONDS = 120.0
SKIP_SECONDS = 60.0
DROP_THRESHOLD_DB = 10.0
MIN_DROP_DURATION = 30.0

# A trailing fragment shorter than this is not measured: its mean is
# dominated by the final fade and the end-of-file silence, not by speech.
MIN_WINDOW_SECONDS = 5.0

# volumedetect leaves mean_volume out entirely for digital silence (there is
# nothing to take a log of). Treat that as this floor so the window still
# counts as quiet rather than vanishing from the profile.
SILENCE_FLOOR_DB = -91.0

_MEAN_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
_MAX_RE = re.compile(r"max_volume:\s*(-?[\d.]+)\s*dB")


class AudioToolError(RuntimeError):
    """ffmpeg/ffprobe missing or failed; the file could not be measured."""


@dataclass
class Window:
    start: float
    length: float
    mean_db: float
    max_db: float

    @property
    def end(self) -> float:
        return self.start + self.length


@dataclass
class LevelDrop:
    onset: float          # seconds; start of the first quiet window
    duration: float       # seconds of consecutive quiet windows
    drop_db: float        # median drop below the baseline over the run
    max_drop_db: float    # worst window in the run
    baseline_db: float    # the reference the drop is measured against


@dataclass
class LevelProfile:
    path: str
    duration: float
    windows: list[Window]
    baseline_db: Optional[float]
    drops: list[LevelDrop] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return bool(self.drops)

    @property
    def worst(self) -> Optional[LevelDrop]:
        if not self.drops:
            return None
        return max(self.drops, key=lambda d: d.duration)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise AudioToolError(f"{cmd[0]} not found; install ffmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise AudioToolError(f"{cmd[0]} timed out on {cmd[-1]}") from e


def probe_duration(path: str) -> float:
    """Duration in seconds via ffprobe."""
    proc = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ], timeout=30)
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise AudioToolError(
            f"ffprobe gave no duration for {path}: {proc.stderr.strip()[-200:]}"
        ) from e


def measure_window(path: str, start: float, length: float) -> tuple[float, float]:
    """(mean_db, max_db) of one window, measured by ffmpeg volumedetect.

    `-ss` before `-i` seeks on the input, so each call decodes only the window
    rather than everything before it.
    """
    proc = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats",
        "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
        "-i", path,
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ])
    if proc.returncode != 0:
        raise AudioToolError(
            f"ffmpeg failed on {path} @ {start:.0f}s: {proc.stderr.strip()[-200:]}"
        )
    mean = _MEAN_RE.search(proc.stderr)
    peak = _MAX_RE.search(proc.stderr)
    return (
        float(mean.group(1)) if mean else SILENCE_FLOOR_DB,
        float(peak.group(1)) if peak else SILENCE_FLOOR_DB,
    )


def measure_windows(
    path: str,
    window: float = WINDOW_SECONDS,
    duration: Optional[float] = None,
    workers: Optional[int] = None,
) -> list[Window]:
    """Mean/max volume of consecutive `window`-second slices of the file."""
    if duration is None:
        duration = probe_duration(path)
    starts = []
    t = 0.0
    while t < duration:
        length = min(window, duration - t)
        if length >= MIN_WINDOW_SECONDS:
            starts.append((t, length))
        t += window

    # Each window is its own ffmpeg process, so threads give real parallelism.
    workers = workers or min(8, (os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda s: measure_window(path, *s), starts))
    return [
        Window(start=s, length=l, mean_db=m, max_db=p)
        for (s, l), (m, p) in zip(starts, results)
    ]


def find_drops(
    windows: list[Window],
    baseline_seconds: float = BASELINE_SECONDS,
    skip_seconds: float = SKIP_SECONDS,
    threshold_db: float = DROP_THRESHOLD_DB,
    min_duration: float = MIN_DROP_DURATION,
) -> tuple[Optional[float], list[LevelDrop]]:
    """Return (baseline_db, drops) for a measured window profile.

    baseline_db is the median mean volume of the windows that start inside
    the first `baseline_seconds`. None (and no drops) if the file is too short
    to have any.
    """
    reference = [w.mean_db for w in windows if w.start < baseline_seconds]
    if not reference:
        return None, []
    baseline = statistics.median(reference)

    drops: list[LevelDrop] = []
    run: list[Window] = []

    def flush():
        if not run:
            return
        covered = sum(w.length for w in run)
        if covered > min_duration:
            diffs = [baseline - w.mean_db for w in run]
            drops.append(LevelDrop(
                onset=run[0].start,
                duration=covered,
                drop_db=round(statistics.median(diffs), 1),
                max_drop_db=round(max(diffs), 1),
                baseline_db=round(baseline, 1),
            ))
        run.clear()

    for w in windows:
        if w.start < skip_seconds:
            continue
        if baseline - w.mean_db > threshold_db:
            run.append(w)
        else:
            flush()
    flush()
    return baseline, drops


def analyze(
    path: str,
    window: float = WINDOW_SECONDS,
    baseline_seconds: float = BASELINE_SECONDS,
    skip_seconds: float = SKIP_SECONDS,
    threshold_db: float = DROP_THRESHOLD_DB,
    min_duration: float = MIN_DROP_DURATION,
    workers: Optional[int] = None,
) -> LevelProfile:
    """Full level profile of one file, with any late drops flagged."""
    duration = probe_duration(path)
    windows = measure_windows(path, window=window, duration=duration, workers=workers)
    baseline, drops = find_drops(
        windows,
        baseline_seconds=baseline_seconds,
        skip_seconds=skip_seconds,
        threshold_db=threshold_db,
        min_duration=min_duration,
    )
    return LevelProfile(
        path=path, duration=duration, windows=windows,
        baseline_db=baseline, drops=drops,
    )


def format_time(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"
