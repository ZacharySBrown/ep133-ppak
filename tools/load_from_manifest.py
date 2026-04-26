#!/usr/bin/env python3
"""
load_from_manifest — bulk-load WAVs into EP-133 slots and assign pads.

Accepts two manifest schemas:

A) **Legacy stems-grouped** (back-compat):

    {
      "track": "my_track",
      "bpm": 107.666,
      "stems": {
        "drums": {"loops": [{"position": 1, "file": "/abs/path"}, ...]},
        "bass":  {"loops": [...]},
        ...
      }
    }

B) **StemForge `BatchManifest`** (new — see `ep133.manifest`):

    {
      "version": 1,
      "track": "my_track",
      "bpm": 107.666,
      "samples": [
        {"file": "drums_001.wav", "stem": "drums", "bpm": 107.666,
         "playmode": "oneshot", "name": "drums 1"},
        ...
      ]
    }

  Each sample's `stem` field routes it to a group (via `--groups`).
  Per-sample `bpm` / `time_mode` / `playmode` / `name` override the
  batch-level defaults. `file` paths are resolved relative to the
  manifest's directory if not absolute.

Each stem maps to one of the 4 pad groups (A/B/C/D). Each loop becomes a
sample slot + a pad assignment. Slots are tagged with `sound.bpm` +
`time.mode = "bpm"` so the device's stretch engine plays each loop at
its true tempo regardless of project tempo.

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

from ep133.manifest import BatchManifest, SampleMeta

# Bar index (0-based) → SysEx pad_num (top-down convention).
# Bar 0 lands at the bottom-left "." pad; bar 11 at the top-right "9" pad.
BAR_INDEX_TO_PAD_NUM = [10, 11, 12, 7, 8, 9, 4, 5, 6, 1, 2, 3]
BAR_INDEX_TO_LABEL   = [".", "0", "ENTER", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
BAR_LABEL_TO_INDEX   = {label: idx for idx, label in enumerate(BAR_INDEX_TO_LABEL)}


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


def detect_schema(raw: dict) -> str:
    """Return "new" for BatchManifest, "old" for legacy stems-grouped."""
    if isinstance(raw.get("samples"), list):
        return "new"
    if isinstance(raw.get("stems"), dict):
        return "old"
    raise ValueError(
        "manifest is neither a BatchManifest (has 'samples' list) nor "
        "legacy stems-grouped (has 'stems' object)"
    )


def get_loops_old(manifest, stem_name):
    stems = manifest.get("stems", {})
    if stem_name not in stems:
        raise KeyError(f"stem {stem_name!r} not in manifest (available: {list(stems.keys())})")
    val = stems[stem_name]
    if isinstance(val, list):
        return val
    return val.get("loops", [])


def _route_to_group(s: SampleMeta, stem_to_group: dict[str, str], requested: set[str]) -> str | None:
    """Resolve which group a sample lands in.

    `suggested_group` wins when set. Otherwise we route by stem via
    `--groups`. Samples that can't be routed (no suggested_group, no
    matching --groups entry) return None and are skipped.
    """
    if s.suggested_group is not None:
        return s.suggested_group if s.suggested_group in requested else None
    if s.stem and s.stem in stem_to_group:
        return stem_to_group[s.stem]
    return None


def build_ops_new(batch: BatchManifest, groups, start_slot: int, n_pads: int,
                  manifest_dir: Path) -> list[dict]:
    """Build ops from a `BatchManifest`, two-pass placement.

    Per-group placement:
      Pass 1 — every sample with `suggested_pad` claims its bar-index.
               Two samples claiming the same pad → ValueError.
      Pass 2 — samples without `suggested_pad` fill the unclaimed bar-indices
               in ascending order (bar 0 = '.', bar 1 = '0', ...).

    Group routing per sample:
      `suggested_group` (if set and in --groups) > stem→group via --groups.
      Samples that route to a group not requested via --groups are skipped.
    """
    stem_to_group = {stem: g for g, stem in groups}
    requested = {g for g, _ in groups}

    buckets: dict[str, list[SampleMeta]] = {g: [] for g in requested}
    for s in batch.samples:
        if not s.file:
            continue
        g = _route_to_group(s, stem_to_group, requested)
        if g is not None:
            buckets[g].append(s)

    ops: list[dict] = []
    for g_idx, (group, default_stem) in enumerate(groups):
        bucket = buckets[group]
        if len(bucket) > n_pads:
            raise ValueError(
                f"group {group}: too many samples ({len(bucket)}) for --pads {n_pads}"
            )
        if not bucket:
            continue

        claimed: dict[int, SampleMeta] = {}
        unclaimed: list[SampleMeta] = []
        for s in bucket:
            if s.suggested_pad is not None:
                bar_idx = BAR_LABEL_TO_INDEX.get(s.suggested_pad)
                if bar_idx is None:
                    raise ValueError(
                        f"group {group}: invalid suggested_pad={s.suggested_pad!r}"
                    )
                if bar_idx >= n_pads:
                    raise ValueError(
                        f"group {group}: suggested_pad={s.suggested_pad!r} "
                        f"(bar {bar_idx}) exceeds --pads {n_pads}"
                    )
                if bar_idx in claimed:
                    raise ValueError(
                        f"group {group}: suggested_pad={s.suggested_pad!r} "
                        f"claimed twice (by {claimed[bar_idx].file!r} and {s.file!r})"
                    )
                claimed[bar_idx] = s
            else:
                unclaimed.append(s)

        next_bar = 0
        for s in unclaimed:
            while next_bar < n_pads and next_bar in claimed:
                next_bar += 1
            if next_bar >= n_pads:
                raise ValueError(
                    f"group {group}: too many samples for --pads {n_pads} after "
                    f"honoring explicit suggested_pad claims"
                )
            claimed[next_bar] = s
            next_bar += 1

        for bar_idx in sorted(claimed):
            s = claimed[bar_idx]
            wav_path = Path(s.file)
            if not wav_path.is_absolute():
                wav_path = manifest_dir / wav_path
            slot = start_slot + g_idx * n_pads + bar_idx
            ops.append({
                "group": group,
                "stem": s.stem or default_stem,
                "bar_index": bar_idx,
                "pad_num": BAR_INDEX_TO_PAD_NUM[bar_idx],
                "pad_label": BAR_INDEX_TO_LABEL[bar_idx],
                "slot": slot,
                "wav_path": wav_path,
                "meta": s,
            })

    return ops


def plan(loops_by_stem, groups, start_slot, n_pads):
    """Build ops from per-stem loop lists."""
    ops = []
    for g_idx, (group, stem) in enumerate(groups):
        if stem not in loops_by_stem:
            raise KeyError(f"stem {stem!r} not loaded")
        loops = sorted(loops_by_stem[stem], key=lambda l: l["position"])[:n_pads]
        for bar_i, loop in enumerate(loops):
            slot = start_slot + g_idx * n_pads + bar_i
            ops.append({
                "group": group,
                "stem": stem,
                "bar_index": bar_i,
                "pad_num": BAR_INDEX_TO_PAD_NUM[bar_i],
                "pad_label": BAR_INDEX_TO_LABEL[bar_i],
                "slot": slot,
                "wav_path": Path(loop["file"]),
                "meta": loop.get("_meta"),  # SampleMeta or None
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


def _slot_meta_for_op(op, batch_bpm: float | None, no_bpm: bool):
    """Return (slot_kwargs, pad_kwargs, name) for one op.

    Per-sample meta overrides batch-level bpm. If `no_bpm` is set, BPM
    tagging is suppressed entirely (matching the legacy --no-bpm flag).
    """
    meta: SampleMeta | None = op.get("meta")

    bpm = None
    time_mode = None
    playmode = None
    name = None

    if meta is not None:
        bpm = meta.bpm
        time_mode = meta.time_mode
        playmode = meta.playmode
        name = meta.name

    if bpm is None and batch_bpm is not None:
        bpm = batch_bpm
    if no_bpm:
        bpm = None
        if time_mode == "bpm":
            time_mode = None
    if time_mode is None and bpm is not None:
        time_mode = "bpm"

    slot_kwargs = {}
    if bpm is not None:
        slot_kwargs["bpm"] = bpm
    if time_mode is not None:
        slot_kwargs["time_mode"] = time_mode
    if playmode is not None:
        slot_kwargs["playmode"] = playmode

    pad_kwargs = {}
    if time_mode is not None:
        pad_kwargs["time_mode"] = time_mode
    if playmode is not None:
        pad_kwargs["playmode"] = playmode

    return slot_kwargs, pad_kwargs, name


def run_load(ops, project, delay_ms, batch_bpm=None, no_bpm=False):
    """Execute uploads + per-slot metadata + pad assignments."""
    from ep133 import EP133Client
    from ep133.commands import TE_SYSEX_FILE
    from ep133.payloads import PadParams, SampleParams, build_slot_metadata_set

    with EP133Client.open(inter_message_delay_s=delay_ms / 1000.0) as client:
        for i, op in enumerate(ops):
            wav = op["wav_path"]
            slot = op["slot"]
            group = op["group"]
            pad_num = op["pad_num"]

            slot_kwargs, pad_kwargs, name = _slot_meta_for_op(op, batch_bpm, no_bpm)

            t0 = time.monotonic()
            print(f"  [{i+1:>2}/{len(ops)}] uploading {wav.name} → slot {slot} ...",
                  end=" ", flush=True)
            client.upload_sample(wav, slot=slot, name=name)
            print(f"done ({time.monotonic()-t0:.1f}s)", flush=True)

            if slot_kwargs:
                t1 = time.monotonic()
                desc = ", ".join(f"{k}={v}" for k, v in slot_kwargs.items())
                print(f"           slot meta ({desc}) ...", end=" ", flush=True)
                params = SampleParams(**slot_kwargs)
                payload = build_slot_metadata_set(slot, params)
                request_id = client._send(TE_SYSEX_FILE, payload)
                client._await_response(request_id, timeout=5.0)
                print(f"done ({time.monotonic()-t1:.2f}s)", flush=True)

            t2 = time.monotonic()
            pad_params = PadParams(**pad_kwargs) if pad_kwargs else None
            print(f"           assign P{project} {group}-{op['pad_label']} → slot {slot}",
                  end=" ", flush=True)
            client.assign_pad(project=project, group=group, pad_num=pad_num,
                              slot=slot, params=pad_params)
            print(f"done ({time.monotonic()-t2:.2f}s)", flush=True)


def build_ops(raw, groups, start_slot: int, n_pads: int, manifest_dir: Path):
    """Dispatch to old- or new-schema op-builder. Returns (ops, schema)."""
    schema = detect_schema(raw)
    if schema == "old":
        loops_by_stem = {}
        for _, stem in groups:
            loops_by_stem[stem] = get_loops_old(raw, stem)
        return plan(loops_by_stem, groups, start_slot, n_pads), schema
    batch = BatchManifest.model_validate(raw)
    return build_ops_new(batch, groups, start_slot, n_pads, manifest_dir), schema


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
        raw = json.load(f)

    manifest_dir = args.manifest.parent.resolve()
    track = raw.get("track", args.manifest.parent.name)

    try:
        groups = parse_groups(args.groups)
    except ValueError as e:
        parser.error(str(e))

    try:
        ops, schema = build_ops(raw, groups, args.start_slot, args.pads, manifest_dir)
    except (KeyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  (manifest schema: {schema})")
    print_plan(ops, args.project, track)

    batch_bpm = raw.get("bpm")
    if batch_bpm is not None and not args.no_bpm:
        print(f"  Default sound.bpm={batch_bpm:.2f} + time.mode=bpm "
              f"(per-sample values override)\n")

    if args.dry_run:
        print("  DRY RUN — no device I/O\n")
        return

    run_load(ops, args.project, args.delay_ms, batch_bpm=batch_bpm, no_bpm=args.no_bpm)
    print(f"\n  Done. {len(ops)} ops complete.")


if __name__ == "__main__":
    main()
