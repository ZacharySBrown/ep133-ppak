#!/usr/bin/env python3
"""
load_from_manifest — bulk-load a list of WAV loops to consecutive EP-133 slots
and assign them to pads in a project, with per-slot sound.bpm tagging.

Manifest format (JSON):

    {
      "track": "my_track",
      "bpm": 107.666,
      "stems": {
        "drums": {
          "loops": [
            {"position": 1, "file": "/abs/path/to/loop_001.wav"},
            {"position": 2, "file": "/abs/path/to/loop_002.wav"},
            ...
          ]
        },
        "bass":   {"loops": [...]},
        "vocals": {"loops": [...]},
        "other":  {"loops": [...]}
      }
    }

Each stem maps to one of the 4 pad groups (A/B/C/D). Each loop becomes a
sample slot + a pad assignment. The manifest's `bpm` is written to every
slot's `sound.bpm` along with `time.mode = "bpm"` so the device's stretch
engine plays each loop at its true tempo regardless of project tempo.

Pad placement: bottom-up, left-right (label "." first, "9" last) — matches
the user-facing pad order on the device.

Usage:
    python -m tools.load_from_manifest manifest.json \\
        --project 9 \\
        --groups A=drums B=bass C=vocals D=other \\
        --start-slot 300

Add `--no-bpm` to skip the `sound.bpm` tagging (slots will use device defaults).
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Bar index (0-based) → SysEx pad_num (top-down convention).
# Bar 0 lands at the bottom-left "." pad; bar 11 at the top-right "9" pad.
BAR_INDEX_TO_PAD_NUM = [10, 11, 12, 7, 8, 9, 4, 5, 6, 1, 2, 3]
BAR_INDEX_TO_LABEL   = [".", "0", "ENTER", "1", "2", "3", "4", "5", "6", "7", "8", "9"]


def parse_groups(group_args):
    """Parse ['A=drums', 'B=bass', ...] → [('A', 'drums'), ...]."""
    result = []
    for arg in group_args:
        if "=" not in arg:
            raise ValueError(f"--groups entries must be GROUP=stem (got {arg!r})")
        group, stem = arg.split("=", 1)
        group = group.upper()
        if group not in "ABCD":
            raise ValueError(f"group must be A-D (got {group!r})")
        result.append((group, stem))
    return result


def get_loops(manifest, stem_name):
    stems = manifest.get("stems", {})
    if stem_name not in stems:
        raise KeyError(f"stem {stem_name!r} not in manifest (available: {list(stems.keys())})")
    val = stems[stem_name]
    if isinstance(val, list):
        return val
    return val.get("loops", [])


def plan(manifest, groups, start_slot, n_pads):
    ops = []
    for g_idx, (group, stem) in enumerate(groups):
        loops = get_loops(manifest, stem)
        loops_sorted = sorted(loops, key=lambda l: l["position"])[:n_pads]
        for bar_i, loop in enumerate(loops_sorted):
            slot = start_slot + g_idx * n_pads + bar_i
            ops.append({
                "group": group,
                "stem": stem,
                "bar_index": bar_i,
                "pad_num": BAR_INDEX_TO_PAD_NUM[bar_i],
                "pad_label": BAR_INDEX_TO_LABEL[bar_i],
                "slot": slot,
                "wav_path": Path(loop["file"]),
            })
    return ops


def print_plan(ops, project, track):
    print(f"\n  Plan — {track!r} → Project {project}")
    print(f"  {'Group':<6} {'Stem':<10} {'Bar':<6} {'Pad':<7} {'Slot':<6} {'File'}")
    print(f"  {'-'*5} {'-'*9} {'-'*5} {'-'*6} {'-'*5} {'-'*30}")
    for op in ops:
        print(f"  {op['group']:<6} {op['stem']:<10} bar_{op['bar_index']+1:03d}  "
              f"{op['pad_label']:<7} {op['slot']:<6} {op['wav_path'].name}")
    print()


def run_load(ops, project, delay_ms, source_bpm=None):
    """Execute uploads + per-slot bpm tagging + pad assignments."""
    from ep133 import EP133Client
    from ep133.commands import TE_SYSEX_FILE
    from ep133.payloads import PadParams, SampleParams, build_slot_metadata_set

    with EP133Client.open(inter_message_delay_s=delay_ms / 1000.0) as client:
        for i, op in enumerate(ops):
            wav = op["wav_path"]
            slot = op["slot"]
            group = op["group"]
            pad_num = op["pad_num"]

            t0 = time.monotonic()
            print(f"  [{i+1:>2}/{len(ops)}] uploading {wav.name} → slot {slot} ...",
                  end=" ", flush=True)
            client.upload_sample(wav, slot=slot)
            print(f"done ({time.monotonic()-t0:.1f}s)", flush=True)

            if source_bpm is not None:
                t1 = time.monotonic()
                print(f"           sound.bpm={source_bpm:.2f}, time.mode=bpm ...",
                      end=" ", flush=True)
                params = SampleParams(bpm=source_bpm, time_mode="bpm")
                payload = build_slot_metadata_set(slot, params)
                request_id = client._send(TE_SYSEX_FILE, payload)
                client._await_response(request_id, timeout=5.0)
                print(f"done ({time.monotonic()-t1:.2f}s)", flush=True)

            t2 = time.monotonic()
            print(f"           assign P{project} {group}-{op['pad_label']} → slot {slot}",
                  end=" ", flush=True)
            pad_params = PadParams(time_mode="bpm") if source_bpm is not None else None
            client.assign_pad(project=project, group=group, pad_num=pad_num,
                              slot=slot, params=pad_params)
            print(f"done ({time.monotonic()-t2:.2f}s)", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-load loops from a JSON manifest into an EP-133 project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project",    "-P", type=int, default=8)
    parser.add_argument("--groups",     "-g", nargs="+", required=True,
                        metavar="GROUP=stem",
                        help="Group→stem mappings, e.g. A=drums B=bass C=vocals D=other")
    parser.add_argument("--start-slot", "-s", type=int, default=300)
    parser.add_argument("--pads",       "-n", type=int, default=12,
                        help="Bars/pads per group (1-12, default: 12)")
    parser.add_argument("--delay-ms", type=int, default=10)
    parser.add_argument("--no-bpm", action="store_true",
                        help="Skip writing sound.bpm + time.mode=bpm")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.manifest.exists():
        parser.error(f"manifest not found: {args.manifest}")

    with args.manifest.open() as f:
        manifest = json.load(f)
    track = manifest.get("track", args.manifest.parent.name)

    try:
        groups = parse_groups(args.groups)
    except ValueError as e:
        parser.error(str(e))

    try:
        ops = plan(manifest, groups, args.start_slot, args.pads)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print_plan(ops, args.project, track)

    bpm = manifest.get("bpm")
    source_bpm = None if args.no_bpm else bpm
    if source_bpm is not None:
        print(f"  Tagging slots with sound.bpm={source_bpm:.2f} + time.mode=bpm\n")

    if args.dry_run:
        print("  DRY RUN — no device I/O\n")
        return

    run_load(ops, args.project, args.delay_ms, source_bpm=source_bpm)
    print(f"\n  Done. {len(ops)} ops complete.")


if __name__ == "__main__":
    main()
