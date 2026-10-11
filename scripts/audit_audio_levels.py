#!/usr/bin/env python3
"""
Audit episode loudness — find episodes whose speech goes quiet part-way.

Measures mean volume in consecutive 20 s windows (ffmpeg volumedetect) for
every MP3 in the "audio" GitHub release, or for local files, and flags any
episode where a window after the first minute sits more than 10 dB below the
episode's own first-two-minute median for longer than 30 s. See
src/audio_levels.py for the detection rule; this script only feeds it files
and reports.

Usage:
    python scripts/audit_audio_levels.py                 # every asset of the release
    python scripts/audit_audio_levels.py --only Bruns2026-pn Ramos2026-qo
    python scripts/audit_audio_levels.py --files a.mp3 b.mp3
    python scripts/audit_audio_levels.py --dir repaired/  # every *.mp3 in a folder
    python scripts/audit_audio_levels.py --out audit.csv --all

Downloads are cached in --cache-dir (default: a folder under the system temp
dir), so a re-run after a repair measures the local copies unless --refresh.
Reads GITHUB_TOKEN when set (higher API rate limit); works anonymously too.
Exit status is 1 when any episode is flagged, so it can gate a workflow.
"""

import argparse
import csv
import json
import os
import sys
import tempfile
from typing import Optional

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from config import GITHUB_REPO, GITHUB_TOKEN, EPISODES_FILE  # noqa: E402
from audio_levels import (  # noqa: E402
    BASELINE_SECONDS,
    DROP_THRESHOLD_DB,
    MIN_DROP_DURATION,
    SKIP_SECONDS,
    WINDOW_SECONDS,
    AudioToolError,
    LevelProfile,
    analyze,
    format_time,
)

AUDIO_RELEASE_TAG = "audio"


def _default_repo() -> Optional[str]:
    """GITHUB_REPO from the environment, else the owner/repo the feed links to."""
    if GITHUB_REPO:
        return GITHUB_REPO
    try:
        with open(EPISODES_FILE) as fh:
            for ep in json.load(fh).get("episodes", []):
                url = ep.get("audio_url", "")
                marker = "github.com/"
                if marker in url and "/releases/download/" in url:
                    path = url.split(marker, 1)[1]
                    owner, repo = path.split("/")[:2]
                    return f"{owner}/{repo}"
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    s.headers["User-Agent"] = "research-radio-audit"
    if GITHUB_TOKEN:
        s.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return s


def list_release_assets(repo: str, session: requests.Session) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/releases/tags/{AUDIO_RELEASE_TAG}"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return [a for a in resp.json().get("assets", []) if a["name"].endswith(".mp3")]


def fetch_asset(asset: dict, cache_dir: str, session: requests.Session,
                refresh: bool = False) -> str:
    """Download one release asset into cache_dir unless an intact copy is there."""
    dest = os.path.join(cache_dir, asset["name"])
    if (not refresh and os.path.exists(dest)
            and os.path.getsize(dest) == asset["size"]):
        return dest
    tmp = dest + ".part"
    with session.get(asset["browser_download_url"], stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
    os.replace(tmp, dest)
    return dest


def episode_id(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def write_csv(path: str, profiles: list[LevelProfile], include_all: bool) -> int:
    """One row per flagged drop (or per episode with --all). Returns rows written."""
    fields = [
        "episode_id", "onset_seconds", "onset", "drop_db", "max_drop_db",
        "duration_affected_seconds", "baseline_db", "episode_duration_seconds",
        "flagged",
    ]
    rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for p in profiles:
            eid = episode_id(p.path)
            if p.drops:
                for d in p.drops:
                    w.writerow({
                        "episode_id": eid,
                        "onset_seconds": int(d.onset),
                        "onset": format_time(d.onset),
                        "drop_db": d.drop_db,
                        "max_drop_db": d.max_drop_db,
                        "duration_affected_seconds": int(d.duration),
                        "baseline_db": d.baseline_db,
                        "episode_duration_seconds": int(p.duration),
                        "flagged": 1,
                    })
                    rows += 1
            elif include_all:
                w.writerow({
                    "episode_id": eid,
                    "onset_seconds": "", "onset": "", "drop_db": "",
                    "max_drop_db": "", "duration_affected_seconds": "",
                    "baseline_db": (round(p.baseline_db, 1)
                                    if p.baseline_db is not None else ""),
                    "episode_duration_seconds": int(p.duration),
                    "flagged": 0,
                })
                rows += 1
    return rows


def print_summary(profiles: list[LevelProfile], errors: dict[str, str],
                  args: argparse.Namespace) -> None:
    flagged = [p for p in profiles if p.flagged]
    print("\n" + "=" * 72)
    print("AUDIO LEVEL AUDIT")
    print("=" * 72)
    print(f"  window {args.window:.0f}s | baseline median of first "
          f"{args.baseline:.0f}s | checked from {args.skip:.0f}s | "
          f"drop > {args.threshold:.0f} dB for > {args.min_duration:.0f}s")
    print(f"  episodes measured: {len(profiles)}   flagged: {len(flagged)}   "
          f"errors: {len(errors)}")
    if flagged:
        print("\n  FLAGGED")
        print(f"  {'episode':<28} {'onset':>6} {'drop':>8} {'worst':>8} "
              f"{'affected':>9} {'baseline':>9} {'length':>7}")
        for p in sorted(flagged, key=lambda p: -p.worst.duration):
            for d in p.drops:
                print(f"  {episode_id(p.path):<28} {format_time(d.onset):>6} "
                      f"{d.drop_db:>6.1f}dB {d.max_drop_db:>6.1f}dB "
                      f"{format_time(d.duration):>9} {d.baseline_db:>7.1f}dB "
                      f"{format_time(p.duration):>7}")
    else:
        print("\n  No episode shows a sustained late drop.")
    if errors:
        print("\n  COULD NOT MEASURE")
        for eid, msg in errors.items():
            print(f"  {eid}: {msg}")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flag episodes whose speech level drops part-way through."
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--files", nargs="+", metavar="MP3",
                     help="audit these local files instead of the release")
    src.add_argument("--dir", metavar="DIR",
                     help="audit every *.mp3 in this folder")
    ap.add_argument("--only", nargs="+", metavar="EPISODE_ID",
                    help="restrict to these episode ids (asset name sans .mp3)")
    ap.add_argument("--repo", default=_default_repo(),
                    help="owner/repo of the audio release (default: GITHUB_REPO "
                         "or the repo docs/episodes.json links to)")
    ap.add_argument("--cache-dir",
                    default=os.path.join(tempfile.gettempdir(),
                                         "research-radio-audio-audit"),
                    help="where downloaded assets are kept between runs")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download assets even if cached")
    ap.add_argument("--out", default="audio_level_audit.csv",
                    help="CSV path (default: audio_level_audit.csv)")
    ap.add_argument("--all", action="store_true",
                    help="write a CSV row for unflagged episodes too")
    ap.add_argument("--window", type=float, default=WINDOW_SECONDS)
    ap.add_argument("--baseline", type=float, default=BASELINE_SECONDS,
                    help="seconds whose window median is the reference level")
    ap.add_argument("--skip", type=float, default=SKIP_SECONDS,
                    help="ignore windows starting before this many seconds")
    ap.add_argument("--threshold", type=float, default=DROP_THRESHOLD_DB,
                    help="dB below baseline that counts as quiet")
    ap.add_argument("--min-duration", type=float, default=MIN_DROP_DURATION,
                    help="seconds a quiet stretch must exceed to be flagged")
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel ffmpeg processes per file")
    args = ap.parse_args()

    # Resolve the files to measure.
    if args.files:
        paths = list(args.files)
    elif args.dir:
        paths = sorted(
            os.path.join(args.dir, f) for f in os.listdir(args.dir)
            if f.lower().endswith(".mp3")
        )
    else:
        if not args.repo:
            print("Error: no repo known. Set GITHUB_REPO or pass --repo owner/repo.")
            return 2
        os.makedirs(args.cache_dir, exist_ok=True)
        session = _session()
        print(f"Listing assets of release '{AUDIO_RELEASE_TAG}' in {args.repo}...")
        assets = list_release_assets(args.repo, session)
        if args.only:
            wanted = set(args.only)
            assets = [a for a in assets if episode_id(a["name"]) in wanted]
        total_mb = sum(a["size"] for a in assets) / 1e6
        print(f"  {len(assets)} MP3 asset(s), {total_mb:.0f} MB; cache: {args.cache_dir}")
        paths = []
        for i, asset in enumerate(assets, 1):
            try:
                paths.append(fetch_asset(asset, args.cache_dir, session, args.refresh))
            except requests.RequestException as e:
                print(f"  [{i}/{len(assets)}] download failed for {asset['name']}: {e}")
            else:
                if i % 20 == 0 or i == len(assets):
                    print(f"  [{i}/{len(assets)}] fetched")
    if args.only and (args.files or args.dir):
        wanted = set(args.only)
        paths = [p for p in paths if episode_id(p) in wanted]

    if not paths:
        print("Nothing to audit.")
        return 2

    profiles: list[LevelProfile] = []
    errors: dict[str, str] = {}
    print(f"\nMeasuring {len(paths)} file(s)...")
    for i, path in enumerate(paths, 1):
        eid = episode_id(path)
        try:
            prof = analyze(
                path,
                window=args.window,
                baseline_seconds=args.baseline,
                skip_seconds=args.skip,
                threshold_db=args.threshold,
                min_duration=args.min_duration,
                workers=args.workers,
            )
        except AudioToolError as e:
            errors[eid] = str(e)
            print(f"  [{i}/{len(paths)}] {eid}: ERROR {e}")
            continue
        profiles.append(prof)
        if prof.flagged:
            d = prof.worst
            print(f"  [{i}/{len(paths)}] {eid}: FLAGGED onset {format_time(d.onset)}, "
                  f"{d.drop_db:.1f} dB down for {format_time(d.duration)}")
        elif i % 10 == 0 or i == len(paths):
            print(f"  [{i}/{len(paths)}] {eid}: ok")

    rows = write_csv(args.out, profiles, include_all=args.all)
    print_summary(profiles, errors, args)
    print(f"\nCSV: {args.out} ({rows} row(s))")

    return 1 if any(p.flagged for p in profiles) or errors else 0


if __name__ == "__main__":
    sys.exit(main())
