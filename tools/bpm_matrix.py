#!/usr/bin/env python3
"""
ep133_bpm_matrix.py — Per-pad BPM via SysEx slot replication
============================================================

Plays the same audio file across 12 pads, each at a different perceived
tempo, using the SysEx-only path (no Sample Tool, no .ppak).

Method
------
EP-133's per-pad BPM lives in the project TAR's binary record (no SysEx
write path). Workaround: replicate the WAV into 12 sample slots, set each
slot's `sound.bpm` to a different value, then assign one pad per slot
with `time.mode = "bpm"`.

Each pad then plays the sample stretched relative to project tempo:
  stretch_ratio = project_bpm / sound.bpm

Lower sound.bpm → faster playback (= higher perceived tempo).
Higher sound.bpm → slower playback (= lower perceived tempo).

The BPMs we set on slots map directly to "this pad sounds like X BPM"
when project tempo == default 120, since the device equates source-BPM
with perceived BPM in time.mode=bpm.

Usage
-----
    uv run --with python-rtmidi --with mido python tools/ep133_bpm_matrix.py \\
        /Users/zak/.../bar_002.wav \\
        --project 7 --group C \\
        --start-slot 100 \\
        --bpms 60,80,100,120,130,140,150,160,170,180,190,200

Default BPM matrix mirrors the .ppak `--preset matrix` for direct comparison.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Bar index (0-based) → pad_num (visual position, 1-indexed top-to-bottom left-to-right).
# Same convention as tools/ep133_load_project.py.
BAR_INDEX_TO_PAD_NUM = [10, 11, 12, 7, 8, 9, 4, 5, 6, 1, 2, 3]
BAR_INDEX_TO_LABEL   = [".", "0", "ENTER", "1", "2", "3", "4", "5", "6", "7", "8", "9"]

DEFAULT_BPMS = [60, 80, 100, 120, 130, 140, 150, 160, 170, 180, 190, 200]


def main() -> None:
    p = argparse.ArgumentParser(
        description="Per-pad BPM via SysEx slot replication.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("wav", type=Path, help="WAV file to replicate across slots")
    p.add_argument("--project",    "-P", type=int, default=7)
    p.add_argument("--group",      "-g", default="C", choices=list("ABCD"))
    p.add_argument("--start-slot", "-s", type=int, default=100)
    p.add_argument("--bpms",       default=",".join(str(b) for b in DEFAULT_BPMS),
                   help="Comma-separated BPM list (1-12 values, default: 60..200 matrix)")
    p.add_argument("--delay-ms",   type=int, default=10)
    p.add_argument("--dry-run",    action="store_true",
                   help="Print plan without sending anything")
    args = p.parse_args()

    if not args.wav.exists():
        print(f"Error: {args.wav} not found", file=sys.stderr)
        sys.exit(1)

    bpms = [float(b.strip()) for b in args.bpms.split(",") if b.strip()]
    if not (1 <= len(bpms) <= 12):
        print(f"Error: --bpms must list 1-12 values (got {len(bpms)})", file=sys.stderr)
        sys.exit(1)
    for b in bpms:
        if not (1.0 <= b <= 200.0):
            print(f"Error: BPM {b} outside device-accepted range 1.0..200.0", file=sys.stderr)
            sys.exit(1)

    # Plan
    print(f"\n  EP-133 BPM matrix — {args.wav.name} → P{args.project} {args.group}")
    print(f"  {'idx':<5} {'pad':<7} {'pad_num':<8} {'slot':<6} {'sound.bpm':<10}")
    print(f"  {'-'*4} {'-'*6} {'-'*7} {'-'*5} {'-'*9}")
    for i, bpm in enumerate(bpms):
        pad_num = BAR_INDEX_TO_PAD_NUM[i]
        label = BAR_INDEX_TO_LABEL[i]
        slot = args.start_slot + i
        print(f"  {i+1:<5} {label:<7} {pad_num:<8} {slot:<6} {bpm:<10}")
    print()

    if args.dry_run:
        print("  DRY RUN — no device I/O performed\n")
        return

    
    from ep133 import EP133Client
    from ep133.commands import TE_SYSEX_FILE
    from ep133.payloads import (
        PadParams, SampleParams, build_slot_metadata_set,
    )

    with EP133Client.open(inter_message_delay_s=args.delay_ms / 1000.0) as client:
        for i, bpm in enumerate(bpms):
            pad_num = BAR_INDEX_TO_PAD_NUM[i]
            label = BAR_INDEX_TO_LABEL[i]
            slot = args.start_slot + i

            # 1. Upload sample to slot
            t0 = time.monotonic()
            print(f"  [{i+1:>2}/{len(bpms)}] upload  → slot {slot}    ", end="", flush=True)
            client.upload_sample(args.wav, slot=slot)
            print(f"({time.monotonic()-t0:.1f}s)")

            # 2. Set sound.bpm + time.mode=bpm on the slot's metadata
            t0 = time.monotonic()
            print(f"           sound.bpm={bpm:.1f}, time.mode=bpm    ", end="", flush=True)
            params = SampleParams(bpm=bpm, time_mode="bpm")
            payload = build_slot_metadata_set(slot, params)
            request_id = client._send(TE_SYSEX_FILE, payload)
            client._await_response(request_id, timeout=5.0)
            print(f"({time.monotonic()-t0:.2f}s)")

            # 3. Assign pad to slot, with pad-level time.mode=bpm too
            t0 = time.monotonic()
            print(
                f"           assign  P{args.project} {args.group}-{label} (pad_num={pad_num}) → slot {slot}",
                end="", flush=True,
            )
            pad_params = PadParams(time_mode="bpm")
            client.assign_pad(
                project=args.project, group=args.group, pad_num=pad_num,
                slot=slot, params=pad_params,
            )
            print(f"  ({time.monotonic()-t0:.2f}s)")

    print()
    print("  ✓ All slots loaded + pads assigned.")
    print(f"    On the device: hit Project {args.project}, Group {args.group}, then tap pads.")
    print( "    Lowest-BPM pad should sound fastest; highest-BPM should sound slowest.")
    print( "    (because stretch_ratio = project_bpm / sound.bpm)")


if __name__ == "__main__":
    main()
