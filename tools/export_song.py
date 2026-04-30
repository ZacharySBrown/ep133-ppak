#!/usr/bin/env python3
"""
export_song — build an EP-133 K.O. II song-mode .ppak from an arrangement snapshot.

Glue CLI that runs the song-export pipeline:

    arrangement.json + manifest.json
        -> resolve_scenes()
        -> synthesize()
        -> build_ppak()

If --reference-template is omitted, a minimal synthetic template is generated
on the fly (the device boots, but pad metadata is zero-filled). For real
device captures, pass --reference-template pointing at a known-good .ppak.

Examples:

    # Minimal: synthesize a template, write to song.ppak
    ppak-export-song \\
        --arrangement snapshot.json \\
        --manifest stems.json \\
        --out song.ppak

    # Use a captured reference template, project slot 3
    ppak-export-song \\
        --arrangement snapshot.json \\
        --manifest stems.json \\
        --reference-template tests/fixtures/reference.ppak \\
        --project 3 \\
        --out out/song.ppak
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def _existing_path(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: {value}")
    return p


def _project_slot(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--project must be an integer 1..9, got {value!r}") from exc
    if not (1 <= n <= 9):
        raise argparse.ArgumentTypeError(f"--project must be in 1..9, got {n}")
    return n


def main() -> None:
    p = argparse.ArgumentParser(
        description="Build an EP-133 K.O. II song-mode .ppak from an Ableton arrangement snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--arrangement",
        type=_existing_path,
        required=True,
        help="snapshot.json from the M4L arrangement reader (Track B output).",
    )
    p.add_argument(
        "--manifest",
        type=_existing_path,
        required=True,
        help="stems.json with a session_tracks block.",
    )
    p.add_argument(
        "--reference-template",
        type=_existing_path,
        default=None,
        help="Captured reference .ppak used as a byte template by the writer. "
             "If omitted, a minimal synthetic template is generated.",
    )
    p.add_argument(
        "--project",
        type=_project_slot,
        default=1,
        help="EP-133 project slot (1..9). Default: 1.",
    )
    p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output .ppak path.",
    )
    p.add_argument(
        "--mode",
        default="locator",
        choices=["locator"],
        help="Scene-derivation mode. v1 only supports 'locator'.",
    )

    args = p.parse_args()

    try:
        from ep133.ppak.song_writer import build_ppak, build_synthetic_template_ppak
        from ep133.song.resolver import resolve_scenes
        from ep133.song.synthesizer import synthesize
    except ImportError as exc:
        print(f"error: failed to import song-export modules: {exc}", file=sys.stderr)
        sys.exit(1)

    arrangement_path: Path = args.arrangement
    manifest_path: Path = args.manifest
    reference_template: Path | None = args.reference_template
    project_slot: int = args.project
    out_path: Path = args.out
    mode: str = args.mode

    print(f"export-song (mode={mode})")
    print(f"  Arrangement: {arrangement_path}")
    print(f"  Manifest:    {manifest_path}")
    print(f"  Project:     {project_slot}")
    print(f"  Output:      {out_path}")
    if reference_template is not None:
        print(f"  Template:    {reference_template}")
    else:
        print("  Template:    <none> — synthesizing minimal template "
              "(device boots, but pad metadata is zero-filled). "
              "Pass --reference-template for a real device capture.")

    try:
        arrangement = json.loads(arrangement_path.read_text())
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: failed to read JSON inputs: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        bpm = float(arrangement.get("tempo", 120.0))
        sig_raw = arrangement.get("time_sig", [4, 4])
        time_sig = (int(sig_raw[0]), int(sig_raw[1]))
    except (TypeError, ValueError) as exc:
        print(f"error: bad tempo/time_sig in arrangement: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  Tempo:       {bpm:.2f}  Time sig: {time_sig[0]}/{time_sig[1]}")

    try:
        snapshots = resolve_scenes(arrangement, manifest)
    except Exception as exc:
        print(f"error: resolve_scenes failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Snapshots:   {len(snapshots)}")

    arrangement_length_sec = arrangement.get("arrangement_length_sec")
    try:
        spec = synthesize(
            snapshots,
            manifest,
            bpm,
            time_sig,
            project_slot,
            arrangement_length_sec=(
                float(arrangement_length_sec) if arrangement_length_sec is not None else None
            ),
        )
    except Exception as exc:
        print(f"error: synthesize failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  Patterns:    {len(spec.patterns)}  "
          f"Scenes: {len(spec.scenes)}  "
          f"Pads: {len(spec.pads)}  "
          f"Sounds: {len(spec.sounds)}")

    try:
        if reference_template is None:
            with tempfile.TemporaryDirectory() as td:
                synth = Path(td) / "synthetic_template.ppak"
                build_synthetic_template_ppak(synth, project_slot=project_slot)
                payload = build_ppak(spec, synth)
        else:
            payload = build_ppak(spec, reference_template)
    except Exception as exc:
        print(f"error: build_ppak failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
    except OSError as exc:
        print(f"error: failed to write output: {exc}", file=sys.stderr)
        sys.exit(1)

    kb = len(payload) / 1024.0
    print(f"  Wrote {out_path} ({kb:.1f} KB)")


if __name__ == "__main__":
    main()
