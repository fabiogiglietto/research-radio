#!/usr/bin/env python3
"""
Repair published episodes whose speech goes quiet part-way.

For each target episode: download the published MP3 from the "audio"
release, confirm the audit flags it (scripts/audit_audio_levels.py's rule),
master it with the same ffmpeg chain new renders go through
(src/audio_master.py), re-audit the result, replace the release asset under
the SAME filename (so audio_url and the feed's enclosure URLs stay valid),
and update the byte size / duration in docs/episodes.json and docs/feed.xml.
guid and pubDate are never touched, so podcast apps do not see new episodes.

Episodes whose drop is digital silence rather than quiet speech (a dead gap
in the render) are reported and skipped: no filter can recover audio that
is not there, they need regenerating.

Usage:
    python scripts/repair_audio_levels.py                   # dry run, every flagged episode
    python scripts/repair_audio_levels.py --ids Bruns2026-pn
    python scripts/repair_audio_levels.py --execute         # replace assets, update data files
    python scripts/repair_audio_levels.py --execute --skip-upload
                              # master + update data files only (upload by other means)

Needs ffmpeg, and for --execute a GITHUB_TOKEN that may edit releases
(the Actions token has it; see .github/workflows/repair_audio_levels.yml).
Exit status 1 when any target could not be repaired.
"""

import argparse
import json
import os
import re
import sys
import tempfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from config import EPISODES_FILE, FEED_FILE  # noqa: E402
from audio_levels import (  # noqa: E402
    AudioToolError, LevelDrop, LevelProfile, analyze, format_time, probe_duration,
)
from audio_master import master_audio  # noqa: E402
from audit_audio_levels import (  # noqa: E402
    AUDIO_RELEASE_TAG, _default_repo, _session, episode_id, fetch_asset,
    list_release_assets,
)

def format_duration(seconds: int) -> str:
    """H:MM:SS / M:SS, as feed_generator.format_duration writes itunes:duration.

    Copied rather than imported: feed_generator pulls in feedgen, which this
    script does not otherwise need.
    """
    hours, minutes, secs = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# A window whose peak is below this holds no speech at all. When most of a
# flagged run is like that, the render has a dead gap, not a level problem.
SILENCE_PEAK_DB = -60.0
SILENCE_FRACTION = 0.3


def silent_fraction(profile: LevelProfile, drop: LevelDrop) -> float:
    run = [w for w in profile.windows
           if drop.onset <= w.start < drop.onset + drop.duration]
    if not run:
        return 0.0
    return sum(1 for w in run if w.max_db < SILENCE_PEAK_DB) / len(run)


def describe(drop: LevelDrop) -> str:
    return (f"{drop.drop_db:.1f} dB down from {format_time(drop.onset)} "
            f"for {format_time(drop.duration)}")


def update_data_files(sizes: dict[str, tuple[int, int]]) -> tuple[int, int]:
    """Write new (bytes, seconds) into episodes.json and feed.xml.

    Only audio_size/duration and enclosure length/itunes:duration change;
    everything else, trailing newline included, is left as it was. Returns
    (episodes updated, feed items updated).
    """
    raw = open(EPISODES_FILE).read()
    data = json.loads(raw)
    n_eps = 0
    for ep in data.get("episodes", []):
        eid = ep["id"].replace("bibtex:", "")
        if eid in sizes:
            ep["audio_size"], ep["duration"] = sizes[eid]
            n_eps += 1
    with open(EPISODES_FILE, "w") as fh:
        fh.write(json.dumps(data, indent=2) + ("\n" if raw.endswith("\n") else ""))

    feed = open(FEED_FILE).read()
    n_items = 0
    for eid, (size, dur) in sizes.items():
        enc = re.compile(
            r'(<enclosure url="[^"]*/releases/download/' + AUDIO_RELEASE_TAG + '/'
            + re.escape(eid) + r'\.mp3" length=")(\d+)(")'
        )
        m = enc.search(feed)
        if not m:
            continue  # own-paper episodes are not in the public feed
        feed = feed[:m.start()] + m.group(1) + str(size) + m.group(3) + feed[m.end():]
        item_start = feed.rfind("<item>", 0, m.start())
        item_end = feed.find("</item>", m.start())
        item = feed[item_start:item_end]
        item = re.sub(r"<itunes:duration>[^<]*</itunes:duration>",
                      f"<itunes:duration>{format_duration(dur)}</itunes:duration>",
                      item, count=1)
        feed = feed[:item_start] + item + feed[item_end:]
        n_items += 1
    with open(FEED_FILE, "w") as fh:
        fh.write(feed)
    return n_eps, n_items


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-master published episodes that fail the level audit."
    )
    ap.add_argument("--ids", nargs="+", metavar="EPISODE_ID",
                    help="repair these episodes (default: every one the audit flags)")
    ap.add_argument("--exclude", nargs="+", default=[], metavar="EPISODE_ID")
    ap.add_argument("--repo", default=_default_repo())
    ap.add_argument("--work-dir",
                    default=os.path.join(tempfile.gettempdir(),
                                         "research-radio-audio-repair"))
    ap.add_argument("--execute", action="store_true",
                    help="replace release assets and update the data files "
                         "(default: dry run, nothing published changes)")
    ap.add_argument("--skip-upload", action="store_true",
                    help="with --execute: master and update the data files "
                         "but leave the release alone")
    ap.add_argument("--force", action="store_true",
                    help="master even if the audit does not flag the file")
    args = ap.parse_args()

    if not args.repo:
        print("Error: no repo known. Set GITHUB_REPO or pass --repo owner/repo.")
        return 2
    orig_dir = os.path.join(args.work_dir, "original")
    out_dir = os.path.join(args.work_dir, "repaired")
    os.makedirs(orig_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    session = _session()
    print(f"Listing assets of release '{AUDIO_RELEASE_TAG}' in {args.repo}...")
    assets = {episode_id(a["name"]): a for a in list_release_assets(args.repo, session)}
    if args.ids:
        unknown = [i for i in args.ids if i not in assets]
        if unknown:
            print(f"Error: not in the release: {unknown}")
            return 2
        targets = list(args.ids)
    else:
        targets = sorted(assets)
    targets = [t for t in targets if t not in set(args.exclude)]
    print(f"  {len(targets)} candidate(s); mode: "
          f"{'EXECUTE' if args.execute else 'dry run'}"
          f"{' (no upload)' if args.skip_upload else ''}")

    repaired: dict[str, tuple[str, LevelDrop, LevelDrop]] = {}  # id -> (path, before, after?)
    needs_regen: dict[str, str] = {}
    failed: dict[str, str] = {}
    unflagged = 0

    for i, eid in enumerate(targets, 1):
        try:
            src = fetch_asset(assets[eid], orig_dir, session)
        except requests.RequestException as e:
            failed[eid] = f"download failed: {e}"
            print(f"  [{i}/{len(targets)}] {eid}: {failed[eid]}")
            continue
        try:
            before = analyze(src)
        except AudioToolError as e:
            failed[eid] = f"could not measure: {e}"
            print(f"  [{i}/{len(targets)}] {eid}: {failed[eid]}")
            continue
        if not before.flagged and not args.force:
            unflagged += 1
            if args.ids:
                print(f"  [{i}/{len(targets)}] {eid}: not flagged; skipping (use --force)")
            continue
        drop = before.worst
        if drop and silent_fraction(before, drop) > SILENCE_FRACTION:
            needs_regen[eid] = (f"{describe(drop)} — mostly digital silence; "
                                f"needs regenerating, not mastering")
            print(f"  [{i}/{len(targets)}] {eid}: SKIP {needs_regen[eid]}")
            continue

        out = os.path.join(out_dir, f"{eid}.mp3")
        try:
            master_audio(src, out)
            after = analyze(out)
        except AudioToolError as e:
            failed[eid] = f"mastering failed: {e}"
            print(f"  [{i}/{len(targets)}] {eid}: {failed[eid]}")
            continue
        if after.flagged:
            failed[eid] = f"still flagged after mastering: {describe(after.worst)}"
            print(f"  [{i}/{len(targets)}] {eid}: {failed[eid]}")
            continue
        repaired[eid] = (out, drop, after)
        print(f"  [{i}/{len(targets)}] {eid}: repaired ({describe(drop) if drop else 'forced'}); "
              f"{os.path.getsize(src)} -> {os.path.getsize(out)} bytes")

    # --- publish -------------------------------------------------------------
    uploaded: dict[str, tuple[int, int]] = {}
    if args.execute and repaired:
        if args.skip_upload:
            for eid, (out, _, _) in repaired.items():
                uploaded[eid] = (os.path.getsize(out), int(probe_duration(out)))
        else:
            # Lazy: pulls GITHUB_TOKEN/GITHUB_REPO from config at import.
            from github_uploader import upload_audio_to_release
            for eid, (out, _, _) in repaired.items():
                if not upload_audio_to_release(out):
                    failed[eid] = "upload failed; release asset may be missing — re-run for this id"
                    continue
                uploaded[eid] = (os.path.getsize(out), int(probe_duration(out)))
            # Trust but verify: the asset the release now serves must be ours.
            live = {episode_id(a["name"]): a for a in list_release_assets(args.repo, session)}
            for eid in list(uploaded):
                a = live.get(eid)
                if not a or a["size"] != uploaded[eid][0]:
                    failed[eid] = (f"release asset size {a['size'] if a else 'missing'} "
                                   f"!= uploaded {uploaded[eid][0]}")
                    del uploaded[eid]
        if uploaded:
            n_eps, n_items = update_data_files(uploaded)
            print(f"\nUpdated {n_eps} episode(s) in {os.path.relpath(EPISODES_FILE, ROOT)} "
                  f"and {n_items} item(s) in {os.path.relpath(FEED_FILE, ROOT)}")

    # --- summary -------------------------------------------------------------
    print("\n" + "=" * 72)
    print("AUDIO LEVEL REPAIR" + ("" if args.execute else " (dry run)"))
    print("=" * 72)
    print(f"  candidates {len(targets)}   not flagged {unflagged}   "
          f"repaired {len(repaired)}   published {len(uploaded)}   "
          f"needs regeneration {len(needs_regen)}   failed {len(failed)}")
    if repaired:
        print(f"\n  {'episode':<26} {'before':<34} {'after':<10}"
              + ("  bytes" if args.execute else ""))
        for eid, (out, drop, after) in sorted(repaired.items()):
            tail = f"  {uploaded[eid][0]}" if eid in uploaded else ""
            print(f"  {eid:<26} {describe(drop) if drop else 'forced':<34} "
                  f"{'ok' if not after.flagged else 'FLAGGED':<10}{tail}")
    if needs_regen:
        print("\n  NEEDS REGENERATION (dead gap, not repairable by mastering)")
        for eid, why in needs_regen.items():
            print(f"  {eid}: {why}")
    if failed:
        print("\n  FAILED")
        for eid, why in failed.items():
            print(f"  {eid}: {why}")
    print("=" * 72)
    if not args.execute and repaired:
        print("Dry run: nothing published was changed. Re-run with --execute.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
