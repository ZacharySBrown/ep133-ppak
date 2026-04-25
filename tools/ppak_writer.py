#!/usr/bin/env python3
"""
ppak_writer — generate .ppak archives by patching a real Sample Tool backup.

The patch-from-real strategy guarantees format conformance: take a real
.ppak from Sample Tool's Backup as a base, modify only the bytes that
need to change (pad records, optionally meta timestamp), and repack.

Usage:
    python -m tools.ppak_writer \\
        --base ~/Downloads/EP-133_*_backup.ppak \\
        --preset matrix_tight \\
        --out ~/Desktop/my_project.ppak

Presets:
    mvp           — One pad (C-01), BPM 120, no override. Format sanity test.
    mvp_override  — Same as mvp but uses override-BPM encoding.
    matrix        — 12 pads at BPMs 60-200 (wide range; may produce blip
                    playback at low BPMs due to bar quantization).
    matrix_tight  — 12 pads at BPMs 120-180 (cleaner musical range).
"""

import argparse
import os
import sys

from ep133.ppak.writer import (
    PRESETS,
    build_from_base,
    get_sample_length_frames,
)


def main():
    parser = argparse.ArgumentParser(
        description="EP-133 .ppak generator (patch-from-real-backup)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base", required=True,
                        help="Real .ppak from Sample Tool Backup (used as format-clean base)")
    parser.add_argument("--preset", choices=PRESETS.keys(), default="mvp")
    parser.add_argument("--out", default=os.path.expanduser("~/Desktop/ep133_out.ppak"))
    parser.add_argument("--no-refresh-meta", action="store_true",
                        help="Skip refreshing the generated_at timestamp")
    args = parser.parse_args()

    sample_length = get_sample_length_frames(args.base)
    print(f"Base sample length: {sample_length:,} frames")

    spec = PRESETS[args.preset](sample_length)
    result = build_from_base(args.base, args.out, spec,
                             refresh_meta=not args.no_refresh_meta)

    print(f"✓ Wrote {result['path']}")
    print(f"  Patched: {result['project_tar']}")
    print(f"  Configured pads: {len(result['configured_pads'])}")
    print()
    for pad in result["configured_pads"]:
        bpm = pad.get("bpm", "—")
        tm = pad.get("time_mode", "off")
        ovr = " (override)" if pad.get("bpm_override") else ""
        print(f"  {pad['group']}-{pad['pad_num']:02d}  slot={pad['slot']:<4} bpm={bpm}{ovr}  time.mode={tm}")


if __name__ == "__main__":
    main()
