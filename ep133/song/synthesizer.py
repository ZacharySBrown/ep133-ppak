"""Snapshot → PpakSpec synthesizer.

Turns a list of :class:`Snapshot` (from the resolver) into a :class:`PpakSpec`
ready for the .ppak writer's byte builder.

Algorithm:
- For every (group, pad, bars) tuple appearing in any snapshot, emit ONE
  :class:`Pattern` containing a single trigger event at position 0 over the
  whole pattern length. Patterns are deduped across snapshots.
- For every (group, pad) used, emit ONE :class:`PadSpec` carrying the sample
  slot from ``manifest.session_tracks``.
- For every snapshot, emit ONE :class:`SceneSpec` mapping each group to the
  pattern index it triggers (or 0 if silent).

See ``docs/PORT_PLAN.md`` for the full pipeline contract.
"""

from __future__ import annotations

from pathlib import Path

from .format import Event, PadSpec, Pattern, PpakSpec, SceneSpec
from .resolver import (
    ArrangementClip,
    GROUPS,
    Snapshot,
    _index_session_tracks,
    lookup_pad,
)


# EP-133 limits
MAX_SCENES = 99
MAX_PATTERNS_PER_GROUP = 99
MAX_PADS_PER_GROUP = 12

# Pattern-index sentinel used in scene chunks where a group has no clip
# in a given scene. Setting the chunk byte to 0 was the natural-looking
# encoding ("no pattern"), but the device errors with `err pattern 189`
# on scene transition when a group transitions from a real pattern to 0.
# Reference song-mode captures NEVER use 0 — every scene fires every
# group, with silent groups pointing at an empty pattern. We emit one
# empty pattern per group (`patterns/{group}{99:02d}` = `00 02 00 00`)
# and reference index 99 wherever a clip is absent.
EMPTY_PATTERN_INDEX = 99

# Global sample-slot base. Per Zak's convention, song-export writes always
# land at slot 700+ so they don't clobber the user's 1..699 sample library.
# Each group gets a 20-slot window so manifest's per-group 0..19 indices
# stay isolated:
#   A → 700..719   B → 720..739   C → 740..759   D → 760..779
SAMPLE_SLOT_BASE = 700
SAMPLE_SLOT_PER_GROUP = 20
_GROUP_SLOT_OFFSET = {"a": 0, "b": 20, "c": 40, "d": 60}


def global_sample_slot(group: str, manifest_slot: int) -> int:
    """Map (group, per-group manifest slot) → global EP-133 sample slot."""
    g = group.lower()
    if g not in _GROUP_SLOT_OFFSET:
        raise ValueError(f"group must be one of a/b/c/d, got {group!r}")
    if not (0 <= manifest_slot < SAMPLE_SLOT_PER_GROUP):
        raise ValueError(
            f"manifest_slot must be 0..{SAMPLE_SLOT_PER_GROUP - 1}, got {manifest_slot}"
        )
    return SAMPLE_SLOT_BASE + _GROUP_SLOT_OFFSET[g] + manifest_slot


# Pattern timing
TICKS_PER_BAR = 384

# Bars inference. The EP-133's time-stretch bar field accepts only
# {0.25, 0.5, 1, 2, 4} (per phones24 parsers.ts and the .ppak writer's
# validation). Longer clips snap to the 4-bar maximum and let the EP's
# stretch slow the playback to fit.
_BARS_TOLERANCE_SEC = 0.4
_BARS_CANDIDATES_SNAP = (1, 2, 4)
_BARS_CANDIDATES_FALLBACK = (1, 2, 4)


def infer_bars(clip_length_sec: float, project_bpm: float) -> int:
    """Pick the EP-133 ``time.bars`` value for a clip.

    Two-stage decision (matches the hybrid loader's ``detect_bars_value``):

    1. If the clip duration is within ±400ms of an integer bar count at
       project BPM, snap to that bar count (chosen from {1, 2, 4}).
    2. Otherwise pick the closest of {1, 2, 4} bars and let the EP's stretch
       absorb the difference.
    """
    if project_bpm <= 0:
        raise ValueError(f"project_bpm must be positive, got {project_bpm!r}")
    bar_dur_sec = 60.0 * 4.0 / project_bpm
    for bars in _BARS_CANDIDATES_SNAP:
        if abs(clip_length_sec - bars * bar_dur_sec) <= _BARS_TOLERANCE_SEC:
            return bars
    return min(
        _BARS_CANDIDATES_FALLBACK,
        key=lambda b: abs(clip_length_sec - b * bar_dur_sec),
    )


# Cap on multi-event tiling density so a tiny slice (e.g. a single click
# at 1/64-bar) doesn't produce a pathological pattern. 32 events per
# pattern is a 32nd-note grid at 4/4 — the finest density that's
# musically useful. Beyond that the slice is shorter than typical
# rhythmic resolution and a single trigger sounds the same.
_MAX_EVENTS_PER_PATTERN = 32

# Total trigger counts per pattern that we snap multi-event tiling to.
# These are subdivisions of the WHOLE pattern, not per-bar — chosen so
# spacing always lands on a familiar grid:
#   1 = single fire, 2 = halves, 4 = quarters, 8 = eighths, 16 = sixteenths,
#   32 = thirty-seconds (relative to pattern length).
# A slice that lands between these snaps to the closest. Never produces
# a 6- or 7-tuplet feel that fights the underlying tempo.
_MUSICAL_TRIGGER_COUNTS = (1, 2, 4, 8, 16, 32)


def _event_positions_bars(slice_bars: float, pattern_bars: int) -> list[float]:
    """Compute event positions (in bars) for a multi-event pattern.

    A clip whose slice is shorter than the pattern needs to fire multiple
    times to mimic Ableton's loop-fill behavior. We snap the trigger
    count to the nearest power-of-2 subdivision of the pattern length so
    the result lands on a familiar rhythmic grid instead of an awkward
    6- or 7-tuplet. Slices that are roughly pattern-length or longer
    return a single trigger at position 0 (the device plays the full
    slice in BPM mode).

    Examples:
      pattern_bars=1, slice_bars=1.0     → [0.0]                 (1× whole)
      pattern_bars=1, slice_bars=0.5     → [0.0, 0.5]            (halves)
      pattern_bars=1, slice_bars=0.156   → 8 events (eighth-grid)
      pattern_bars=4, slice_bars=2.0     → [0.0, 2.0]            (every 2 bars)
      pattern_bars=4, slice_bars=1.0     → 4 events (one per bar)
    """
    if slice_bars <= 0 or pattern_bars <= 0:
        return [0.0]
    raw_count = pattern_bars / slice_bars
    # Clip slices that are roughly pattern-length (or longer) → single
    # fire. Threshold at 1.5 keeps the snap from picking n=2 below it.
    if raw_count < 1.5:
        return [0.0]
    candidates = [c for c in _MUSICAL_TRIGGER_COUNTS if c <= _MAX_EVENTS_PER_PATTERN]
    # Tie-break to the smaller count when raw_count sits exactly between
    # two subdivisions (musically the more conservative choice).
    n = min(candidates, key=lambda c: (abs(c - raw_count), c))
    if n == 1:
        return [0.0]
    spacing = pattern_bars / n
    return [i * spacing for i in range(n)]


def _entry_for_path(manifest: dict, group: str, file_path: str) -> dict:
    session = manifest.get("session_tracks") or {}
    entries = session.get(group) or session.get(group.lower()) or []
    for entry in entries:
        path = entry.get("file_path") or entry.get("file")
        if path == file_path:
            return entry
    raise KeyError(f"no session_tracks entry for {file_path!r} on group {group!r}")


def _wav_path_for_pad(manifest: dict, group: str, pad: int) -> Path:
    """Return the WAV path for the entry whose pad ( = slot + 1) matches."""
    session = manifest.get("session_tracks") or {}
    entries = session.get(group) or session.get(group.lower()) or []
    target_slot = pad - 1
    for entry in entries:
        if int(entry.get("slot", -1)) != target_slot:
            continue
        path = entry.get("file_path") or entry.get("file")
        if path is None:
            raise KeyError(f"session_tracks[{group}] slot={target_slot} has no file path")
        return Path(path)
    raise KeyError(f"no session_tracks[{group}] entry for pad {pad} (slot {target_slot})")


def _scene_lengths_in_bars(
    snapshots: list[Snapshot],
    project_bpm: float,
    arrangement_length_sec: float | None,
) -> list[int]:
    """Derive each scene's length in bars from locator gaps.

    Strategy (per user request 2026-04-28):
      1. Quantize each locator's time to its nearest integer-bar
         position (so a drag-imprecise locator at t=3.6s on a 1.765s/bar
         project snaps to bar 2 instead of producing a fractional gap).
      2. Scene N's length in bars = quantized_bar(N+1) - quantized_bar(N).
      3. For the trailing scene, end at ``arrangement_length_sec``
         (also quantized) if provided; else fall back to the median of
         preceding gaps; else default to 2 bars.

    Any positive integer bar count is allowed (1, 2, 3, 4, 5, ...) — we
    do NOT snap to powers of 2 here. That's earmarked as future option
    (b): a "musical conformity" mode that'd snap odd gaps (e.g. 3 bars)
    up to the next power of 2 (4). Keep it opt-in if/when added; default
    behavior preserves intentional 3- or 5-bar sections.

    Result is clamped to 1..255 (pattern-header uint8 range).
    """
    if not snapshots:
        return []
    bar_dur_sec = 240.0 / project_bpm

    def to_bars(t: float) -> int:
        return int(round(t / bar_dur_sec))

    quantized_bars = [to_bars(s.locator_time_sec) for s in snapshots]

    if arrangement_length_sec is not None:
        end_bar = to_bars(arrangement_length_sec)
    else:
        end_bar = None

    bar_gaps: list[int] = []
    for i in range(len(snapshots)):
        if i + 1 < len(snapshots):
            bar_gaps.append(quantized_bars[i + 1] - quantized_bars[i])
        elif end_bar is not None:
            bar_gaps.append(end_bar - quantized_bars[i])
        else:
            bar_gaps.append(-1)
    # Fix non-positive trailing gap (single-locator with no length, or
    # an end_bar that landed on/before the last locator due to rounding).
    if bar_gaps[-1] <= 0 and len(bar_gaps) > 1:
        prior = sorted(g for g in bar_gaps[:-1] if g > 0)
        bar_gaps[-1] = prior[len(prior) // 2] if prior else 2
    elif bar_gaps[-1] <= 0:
        bar_gaps[-1] = 2  # single-locator default: 2 bars
    return [max(1, min(255, g)) for g in bar_gaps]


def synthesize(
    snapshots: list[Snapshot],
    manifest: dict,
    project_bpm: float,
    time_sig: tuple[int, int],
    project_slot: int,
    *,
    arrangement_length_sec: float | None = None,
) -> PpakSpec:
    """Convert resolver output into a :class:`PpakSpec`.

    - Patterns deduped by ``(group, pad, bars)``.
    - One :class:`PadSpec` per ``(group, pad)`` actually used.
    - One :class:`SceneSpec` per snapshot.
    - ``sounds`` maps ``sample_slot`` → wav path.

    ``sample_slot`` in this implementation is the manifest's ``slot`` value
    (the per-group 0-indexed position) — the .ppak writer maps it to whatever
    on-device global slot scheme it uses. ``pad`` is the EP-133 pad number
    (1..12), computed as ``slot + 1`` per the spec.

    Raises ``ValueError`` if the snapshot list exceeds the EP-133's 99-scene
    limit, or if any group exceeds 99 distinct patterns / 12 pads.
    """
    if len(snapshots) > MAX_SCENES:
        raise ValueError(
            f"too many scenes ({len(snapshots)} > {MAX_SCENES}). EP-133 song "
            "mode supports at most 99 scenes — drop fewer locators."
        )
    if not (1 <= project_slot <= 9):
        raise ValueError(f"project_slot must be 1..9, got {project_slot!r}")

    # Force a manifest scan up-front so we fail fast on missing files.
    _index_session_tracks(manifest)

    pattern_indices: dict[tuple[str, int, int], int] = {}
    per_group_counts: dict[str, int] = {g.lower(): 0 for g in GROUPS}
    pad_records: dict[tuple[str, int], PadSpec] = {}

    # Source BPM for time.mode=bpm playback. Upstream curation renders all
    # stems at the source song's tempo (manifest top-level `bpm`); the
    # device computes playback_speed = project_bpm / sound_bpm to stretch
    # them to the arrangement tempo (`spec.bpm`, from the snapshot). When
    # the manifest is missing a top-level bpm we fall back to
    # ``project_bpm`` (1.0× playback — matches the bars-mode default).
    source_bpm: float | None = None
    raw = manifest.get("bpm")
    if raw is not None:
        try:
            source_bpm = float(raw)
        except (TypeError, ValueError):
            source_bpm = None
    if source_bpm is None:
        source_bpm = float(project_bpm)
    # Clamp to the device's accepted sound.bpm range (PROTOCOL.md §5).
    if not (1.0 <= source_bpm <= 200.0):
        source_bpm = max(1.0, min(200.0, source_bpm))
    # Round to 2 decimals so the pad-record float32 and the slot WAV's
    # JSON sound.bpm don't drift apart (round(135.999, 2) = 136.0 in JSON
    # but the unrounded float32 would land at 135.9992).
    source_bpm = round(source_bpm, 2)

    def _ensure_pattern(group_lower: str, pad: int, bars: int) -> int:
        key = (group_lower, pad, bars)
        if key in pattern_indices:
            return pattern_indices[key]
        per_group_counts[group_lower] += 1
        if per_group_counts[group_lower] > MAX_PATTERNS_PER_GROUP:
            raise ValueError(
                f"group {group_lower!r} would emit "
                f"{per_group_counts[group_lower]} patterns "
                f"(> {MAX_PATTERNS_PER_GROUP} EP-133 limit)."
            )
        idx = per_group_counts[group_lower]
        pattern_indices[key] = idx
        return idx

    scenes: list[SceneSpec] = []
    # slot → (start_offset_sec, end_offset_sec) carried through to the
    # writer so it slices each WAV to the manifest-specified region
    # before upload.
    slot_slices: dict[int, tuple[float, float]] = {}
    # (group, pad) → slice length in bars-of-source-render. Used at
    # pattern-emission time to fan out short slices into multi-event
    # patterns (mimics Ableton's clip-loop behavior).
    slice_bars_by_pad: dict[tuple[str, int], float] = {}

    # Each scene's length comes from its locator gap, NOT from any
    # individual clip's slice length. This is what makes a 4-bar Ableton
    # section sound like a 4-bar section on the device — every pad's
    # pattern is sized to the scene length and the slice fan-out tiles
    # across it. Without this, scenes truncated to the longest slice
    # (e.g. 2 bars), and the chain advanced too quickly.
    scene_bars_list = _scene_lengths_in_bars(snapshots, project_bpm, arrangement_length_sec)

    # Per-(group, scene_bars) empty-pattern indices. Each silent group in
    # a scene needs an empty marker whose bars match the scene's length —
    # otherwise the device's scene-length rule (apparently min-pattern-
    # bars-across-groups, observed 2026-04-28: a scene with a 2-bar empty
    # marker truncated 4-bar real patterns to their first 2 bars) bites.
    # Allocate marker indices from 99 down so they don't collide with
    # real patterns (numbered from 1 up).
    empty_indices: dict[tuple[str, int], int] = {}
    empty_next_idx: dict[str, int] = {g.lower(): 99 for g in GROUPS}

    def _empty_index(group_lower: str, scene_bars: int) -> int:
        key = (group_lower, scene_bars)
        if key not in empty_indices:
            idx = empty_next_idx[group_lower]
            if idx <= per_group_counts[group_lower]:
                raise ValueError(
                    f"empty-marker index {idx} would collide with real "
                    f"patterns in group {group_lower!r} ({per_group_counts[group_lower]} "
                    "real patterns); arrangement is too dense."
                )
            empty_indices[key] = idx
            empty_next_idx[group_lower] -= 1
        return empty_indices[key]

    for scene_idx, snap in enumerate(snapshots):
        per_scene: dict[str, int] = {}
        scene_bars = scene_bars_list[scene_idx]
        for group in GROUPS:
            clip: ArrangementClip | None = snap.clip_for(group)
            if clip is None:
                per_scene[group.lower()] = _empty_index(group.lower(), scene_bars)
                continue
            pad = lookup_pad(manifest, group, clip.file_path)
            entry = _entry_for_path(manifest, group, clip.file_path)
            # Pattern bars = scene length (from locator gaps). The
            # underlying slice (in render-tempo bars) controls how many
            # times we tile the trigger across the pattern, not how long
            # the pattern is.
            slice_dur_sec = float(entry.get("clip_length_sec", clip.length_sec))
            slice_bars_by_pad[(group.lower(), pad)] = slice_dur_sec * source_bpm / 240.0
            bars = scene_bars
            idx = _ensure_pattern(group.lower(), pad, bars)
            per_scene[group.lower()] = idx
            pad_key = (group.lower(), pad)
            if pad_key not in pad_records:
                pad_records[pad_key] = PadSpec(
                    group=group.lower(),
                    pad=pad,
                    sample_slot=global_sample_slot(group, int(entry["slot"])),
                    play_mode="oneshot",
                    time_stretch_bars=bars,
                    stretch_mode="bpm",
                    sound_bpm=source_bpm,
                )
            # Stash the slice offsets for the writer. Manifest entries
            # carry start_offset_sec / end_offset_sec for sub-region
            # uploads, but some are inconsistent — `end - start` doesn't
            # always match `clip_length_sec`. Trust clip_length_sec
            # (what the synthesizer used to size the pattern) and derive
            # end from start + clip_length_sec so audio duration tracks
            # bar count. Fall back to explicit end_offset_sec only when
            # clip_length_sec is absent.
            slot = global_sample_slot(group, int(entry["slot"]))
            start = entry.get("start_offset_sec")
            end = entry.get("end_offset_sec")
            length = entry.get("clip_length_sec")
            if start is not None and length is not None:
                slot_slices[slot] = (float(start), float(start) + float(length))
            elif start is not None and end is not None:
                slot_slices[slot] = (float(start), float(end))

        scenes.append(
            SceneSpec(
                a=per_scene["a"],
                b=per_scene["b"],
                c=per_scene["c"],
                d=per_scene["d"],
            )
        )

    # Build patterns in deterministic order (insertion order of pattern_indices).
    # Sub-bar slices fan out into multiple events tiled across the
    # pattern (Ableton "loop a 1-beat slice across 2 bars" → 8 events),
    # quantized to the nearest integer count that fits cleanly. Captured
    # patterns ALL use note=60 (0x3c), vel=100 (0x64), duration=96 ticks
    # (a short one-shot trigger; the slice plays its own length in BPM
    # mode regardless of duration_ticks).
    patterns: list[Pattern] = []
    for (group_lower, pad, bars), idx in pattern_indices.items():
        slice_bars = slice_bars_by_pad.get((group_lower, pad), float(bars))
        positions = _event_positions_bars(slice_bars, bars)
        events = [
            Event(
                position_ticks=int(round(pos * TICKS_PER_BAR)),
                pad=pad,
                note=60,
                velocity=100,
                duration_ticks=96,
            )
            for pos in positions
        ]
        patterns.append(Pattern(group=group_lower, index=idx, bars=bars, events=events))

    # Emit one empty pattern per (group, scene_bars) actually referenced.
    # Each empty marker is sized to the scene's bars so the device's
    # scene-length rule doesn't truncate real patterns to the empty's
    # length (verified on hardware 2026-04-28: a 2-bar empty alongside
    # 4-bar real patterns cut every group to 2 bars of playback).
    for (group_lower, scene_bars), idx in sorted(empty_indices.items()):
        patterns.append(Pattern(group=group_lower, index=idx, bars=scene_bars, events=[]))

    # Validate per-group pad count.
    per_group_pads: dict[str, set[int]] = {g.lower(): set() for g in GROUPS}
    for (group_lower, pad), _ in pad_records.items():
        per_group_pads[group_lower].add(pad)
    for group_lower, pads in per_group_pads.items():
        if len(pads) > MAX_PADS_PER_GROUP:
            raise ValueError(
                f"group {group_lower!r} uses {len(pads)} pads "
                f"(> {MAX_PADS_PER_GROUP} EP-133 limit)."
            )

    # Build sounds dict: sample_slot → wav path.
    sounds: dict[int, Path] = {}
    for (group_lower, pad), spec in pad_records.items():
        wav = _wav_path_for_pad(manifest, group_lower.upper(), pad)
        sounds[spec.sample_slot] = wav

    pads_sorted = sorted(pad_records.values(), key=lambda p: (p.group, p.pad))

    # Default song-mode positions: play scenes 1..N in order. The user can
    # always edit the song list on-device after import.
    song_positions = list(range(1, len(scenes) + 1)) if scenes else None

    return PpakSpec(
        project_slot=project_slot,
        bpm=float(project_bpm),
        time_sig=(int(time_sig[0]), int(time_sig[1])),
        patterns=patterns,
        scenes=scenes,
        pads=pads_sorted,
        sounds=sounds,
        song_positions=song_positions,
        slot_slices=slot_slices,
    )
