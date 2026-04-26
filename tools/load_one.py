#!/usr/bin/env python3
"""
load_one — upload a single WAV to a slot and assign it to one pad.

The simplest case of EP-133 sample loading: one file, one pad. Useful
for quick tests, single-pad workflows, or scripting against a known
project layout.

Examples:

    # Upload kick.wav to slot 100, assign to Project 1, Group A, pad "7"
    ppak-load-one kick.wav --project 1 --group A --pad 7 --slot 100

    # Same, but tag with a source BPM (so time.mode=bpm stretching works
    # cleanly when the project tempo differs from the source)
    ppak-load-one loop.wav -P 9 -g C --pad . --slot 220 --bpm 107.666

    # Use a numeric pad_num (top-down, 1-12) instead of a label
    ppak-load-one kick.wav -P 1 -g A --pad-num 1 --slot 100

    # Auto-pull bpm/playmode/name/suggested-pad from a sidecar manifest
    # next to the wav (see ep133.manifest for the schema). CLI flags
    # always override manifest values.
    ppak-load-one kick.wav --slot 100                         # auto-detects sidecar/batch
    ppak-load-one kick.wav --slot 100 --manifest meta.json    # explicit manifest path
    ppak-load-one kick.wav --slot 100 --pad 7 --no-manifest   # ignore any manifest

Pad labels: "7" "8" "9" "4" "5" "6" "1" "2" "3" "." "0" "ENTER"
(matches the physical keypad's number/dot/enter layout).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ep133 import EP133Client
from ep133.commands import TE_SYSEX_FILE
from ep133.manifest import SampleMeta, resolve_meta
from ep133.payloads import (
    PAD_LABEL_TO_NUM,
    PadParams,
    SampleParams,
    build_slot_metadata_set,
    pad_num_from_label,
)


def _coalesce(*values):
    """Return the first non-None value, or None if all are None."""
    for v in values:
        if v is not None:
            return v
    return None


def main():
    p = argparse.ArgumentParser(
        description="Upload a single WAV and assign it to one pad.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("wav", type=Path, help="WAV file to upload")
    p.add_argument("--project", "-P", type=int, default=None,
                   help="Project number 1..99 (default: 1, or manifest-suggested)")
    p.add_argument("--group", "-g", default=None, choices=list("ABCDabcd"),
                   help="Group A/B/C/D (default: A, or manifest-suggested)")

    pad_group = p.add_mutually_exclusive_group()
    pad_group.add_argument("--pad", default=None,
                           help=f"Physical pad label, one of: {sorted(PAD_LABEL_TO_NUM)}")
    pad_group.add_argument("--pad-num", type=int, choices=range(1, 13),
                           metavar="1..12", default=None,
                           help="SysEx pad_num (top-down, left-right): 1=top-left '7', 12=bottom-right 'ENTER'")

    p.add_argument("--slot", "-s", type=int, required=True,
                   help="Sample-library slot 1..999")

    # Optional metadata (CLI > manifest > device default)
    p.add_argument("--bpm", type=float, default=None,
                   help="Tag the slot with sound.bpm = BPM and time.mode = bpm")
    p.add_argument("--time-mode", choices=["off", "bar", "bpm"], default=None,
                   help="Override time.mode (default: 'bpm' if --bpm given, else not written)")
    p.add_argument("--playmode", choices=["oneshot", "key", "legato"], default=None,
                   help="Set sound.playmode + paired envelope.release on the pad")
    p.add_argument("--name", default=None,
                   help="Display name for the slot (default: derived from filename)")

    # Manifest controls
    p.add_argument("--manifest", type=Path, default=None,
                   help="Explicit path to a SampleMeta sidecar OR BatchManifest. "
                        "If omitted, auto-detects .manifest_<hash>.json next to the wav, "
                        "then .manifest.json in the wav's directory.")
    p.add_argument("--no-manifest", action="store_true",
                   help="Skip all manifest auto-detection. CLI flags + device defaults only.")

    p.add_argument("--delay-ms", type=int, default=10,
                   help="Inter-message delay in ms (default: 10)")
    p.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    if not args.wav.exists():
        p.error(f"WAV not found: {args.wav}")
    if not (1 <= args.slot <= 999):
        p.error(f"slot {args.slot} must be 1..999")

    # --- Resolve manifest (sidecar / batch / explicit override) ---
    meta: SampleMeta | None = None
    if not args.no_manifest:
        try:
            meta = resolve_meta(
                args.wav,
                manifest_override=args.manifest,
                use_sidecar=True,
                use_batch=True,
            )
        except Exception as e:
            p.error(f"manifest read failed: {e}")
    elif args.manifest is not None:
        p.error("--manifest and --no-manifest are mutually exclusive")

    # --- Layer: CLI > manifest > default ---
    project = _coalesce(args.project, None, 1)  # no project field in manifest yet
    group = _coalesce(
        args.group.upper() if args.group else None,
        meta.suggested_group if meta else None,
        "A",
    )

    pad_label: str | None = None
    pad_num: int | None = None
    if args.pad is not None:
        pad_label = args.pad
        pad_num = pad_num_from_label(args.pad)
    elif args.pad_num is not None:
        pad_num = args.pad_num
        pad_label = next((lbl for lbl, n in PAD_LABEL_TO_NUM.items() if n == pad_num), str(pad_num))
    elif meta and meta.suggested_pad is not None:
        pad_label = meta.suggested_pad
        pad_num = pad_num_from_label(meta.suggested_pad)
    else:
        p.error("must specify --pad or --pad-num (no suggested_pad in manifest)")

    bpm = _coalesce(args.bpm, meta.bpm if meta else None)
    time_mode = _coalesce(
        args.time_mode,
        meta.time_mode if meta else None,
        "bpm" if bpm is not None else None,
    )
    playmode = _coalesce(args.playmode, meta.playmode if meta else None)
    name = _coalesce(args.name, meta.name if meta else None)

    # --- Plan output ---
    print(f"\n  Plan:")
    print(f"    {args.wav.name}  →  slot {args.slot}")
    print(f"    Project {project}, Group {group}, pad '{pad_label}' (pad_num {pad_num})")
    if name is not None:
        print(f"    name = {name!r}")
    if bpm is not None:
        print(f"    sound.bpm = {bpm}")
    if time_mode:
        print(f"    time.mode = {time_mode}")
    if playmode:
        print(f"    sound.playmode = {playmode}")
    if meta is not None:
        src = "explicit --manifest" if args.manifest else "auto-detected sidecar/batch"
        print(f"    (defaults sourced from {src})")
    print()

    if args.dry_run:
        print("  DRY RUN — no device I/O\n")
        return

    with EP133Client.open(inter_message_delay_s=args.delay_ms / 1000.0) as client:
        # 1. Upload
        t0 = time.monotonic()
        print(f"  uploading {args.wav.name} → slot {args.slot} ...", end=" ", flush=True)
        client.upload_sample(args.wav, slot=args.slot, name=name)
        print(f"done ({time.monotonic()-t0:.1f}s)", flush=True)

        # 2. Slot metadata (only if we have something to write)
        slot_kwargs = {}
        if bpm is not None:
            slot_kwargs["bpm"] = bpm
        if time_mode is not None:
            slot_kwargs["time_mode"] = time_mode
        if playmode is not None:
            slot_kwargs["playmode"] = playmode

        if slot_kwargs:
            t1 = time.monotonic()
            desc = ", ".join(f"{k}={v}" for k, v in slot_kwargs.items())
            print(f"  slot metadata ({desc}) ...", end=" ", flush=True)
            params = SampleParams(**slot_kwargs)
            payload = build_slot_metadata_set(args.slot, params)
            request_id = client._send(TE_SYSEX_FILE, payload)
            client._await_response(request_id, timeout=5.0)
            print(f"done ({time.monotonic()-t1:.2f}s)", flush=True)

        # 3. Assign pad
        t2 = time.monotonic()
        pad_kwargs = {}
        if time_mode is not None:
            pad_kwargs["time_mode"] = time_mode
        if playmode is not None:
            pad_kwargs["playmode"] = playmode
        pad_params = PadParams(**pad_kwargs) if pad_kwargs else None

        print(f"  assign  P{project} {group}-{pad_label} (pad_num={pad_num}) → slot {args.slot} ...",
              end=" ", flush=True)
        client.assign_pad(project=project, group=group,
                          pad_num=pad_num, slot=args.slot, params=pad_params)
        print(f"done ({time.monotonic()-t2:.2f}s)", flush=True)

    print("\n  ✓ Loaded.")


if __name__ == "__main__":
    main()
