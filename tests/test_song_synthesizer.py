"""Tests for the EP-133 song synthesizer."""

from __future__ import annotations

import json

import pytest

from ep133.song.resolver import ArrangementClip, Snapshot, resolve_scenes
from ep133.song.synthesizer import (
    EMPTY_PATTERN_INDEX,
    MAX_PADS_PER_GROUP,
    MAX_SCENES,
    SAMPLE_SLOT_BASE,
    TICKS_PER_BAR,
    _event_positions_bars,
    _MAX_EVENTS_PER_PATTERN,
    _scene_lengths_in_bars,
    global_sample_slot,
    infer_bars,
    synthesize,
)


@pytest.fixture
def snapshots(arrangement, manifest):
    return resolve_scenes(arrangement, manifest)


# ── infer_bars ──────────────────────────────────────────────────────────────


def test_infer_bars_snaps_to_exact_match():
    """120 BPM → 1 bar = 2.0 sec. EP-133 max is 4 bars; longer clips snap
    to 4 (let the device's stretch absorb the difference)."""
    assert infer_bars(2.0, 120.0) == 1
    assert infer_bars(4.0, 120.0) == 2
    assert infer_bars(8.0, 120.0) == 4
    assert infer_bars(16.0, 120.0) == 4


def test_infer_bars_within_tolerance():
    """1 bar at 120 BPM = 2.0 sec; ±400ms still snaps."""
    assert infer_bars(2.39, 120.0) == 1
    assert infer_bars(1.61, 120.0) == 1


def test_infer_bars_falls_back_to_closest_of_1_2_4():
    """12 sec at 120 BPM = 6 bars (no exact match in {1,2,4}). Falls back
    to closest by absolute distance — closest to 6 is 4."""
    assert infer_bars(12.0, 120.0) == 4
    assert infer_bars(0.5, 120.0) == 1


def test_infer_bars_rejects_zero_or_negative_bpm():
    with pytest.raises(ValueError):
        infer_bars(2.0, 0)
    with pytest.raises(ValueError):
        infer_bars(2.0, -120.0)


# ── _event_positions_bars (multi-event tiling) ──────────────────────────────


def test_event_positions_bar_length_slice_single_event():
    """A 1-bar slice in a 1-bar pattern fires once at position 0."""
    assert _event_positions_bars(1.0, 1) == [0.0]


def test_event_positions_two_bar_slice_in_two_bar_pattern_single_event():
    """A 2-bar slice in a 2-bar pattern is a single trigger."""
    assert _event_positions_bars(2.0, 2) == [0.0]


def test_event_positions_half_bar_slice_in_one_bar_pattern_two_events():
    """½-bar slice in 1-bar pattern → 2 events at 0 and ½."""
    positions = _event_positions_bars(0.5, 1)
    assert positions == pytest.approx([0.0, 0.5])


def test_event_positions_eighth_bar_slice_quantizes_to_8_per_bar():
    """⅛-bar slice in 1-bar pattern → 8 events on the 16th-note grid."""
    positions = _event_positions_bars(0.125, 1)
    assert len(positions) == 8
    assert positions[0] == 0.0
    assert positions[-1] == pytest.approx(0.875)


def test_event_positions_one_beat_slice_at_136_snaps_to_eighth_note_grid():
    """A 0.156-bar slice (≈1 beat @ 136 BPM) in a 1-bar pattern lands closer
    to 8 events (eighth-note grid) than to 4 (quarter-note). Previously
    produced a 6-tuplet feel — now snaps to 8/bar."""
    positions = _event_positions_bars(0.156, 1)
    assert len(positions) == 8
    spacings = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    assert all(s == pytest.approx(spacings[0]) for s in spacings)


def test_event_positions_snaps_to_quarter_note_grid_for_tweener():
    """raw_count exactly between two subdivisions tie-breaks to the smaller
    for predictability. ⅓-bar slice → raw=3, candidates {1,2,4,8,16,32}.
    |3-2|=1 == |3-4|=1 → 2 (smaller). ⅖-bar slice (raw=2.5) → equidistant
    to 2 and 4? |2.5-2|=0.5 == |2.5-4|=1.5 → 2."""
    assert len(_event_positions_bars(1 / 3, 1)) == 2
    assert len(_event_positions_bars(2 / 5, 1)) == 2


def test_event_positions_caps_at_max_events_per_pattern():
    """Pathologically tiny slice (1/100 bar) caps at _MAX_EVENTS_PER_PATTERN
    so we never emit a pattern with hundreds of triggers."""
    positions = _event_positions_bars(0.01, 1)
    assert len(positions) == _MAX_EVENTS_PER_PATTERN


def test_event_positions_handles_slightly_short_slice_as_single_event():
    """raw_count<1.5 → single event. 0.8 bar slice in 1 bar pattern →
    raw_count=1.25, < 1.5 → single event."""
    assert _event_positions_bars(0.8, 1) == [0.0]


def test_event_positions_handles_zero_or_negative_slice():
    """Defensive: degenerate inputs return a safe single trigger."""
    assert _event_positions_bars(0.0, 1) == [0.0]
    assert _event_positions_bars(-1.0, 1) == [0.0]


# ── _scene_lengths_in_bars ──────────────────────────────────────────────────


def _snap(t: float) -> Snapshot:
    return Snapshot(
        locator_time_sec=t,
        locator_name="",
        a_clip=None,
        b_clip=None,
        c_clip=None,
        d_clip=None,
    )


def test_scene_lengths_uniform_4_bar_scenes_at_120_bpm():
    """Locators every 8 sec @ 120 BPM = 4 bars between → all scenes 4 bars."""
    snaps = [_snap(0.0), _snap(8.0), _snap(16.0)]
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=24.0)
    assert bars == [4, 4, 4]


def test_scene_lengths_last_scene_uses_arrangement_length():
    """Last scene's length comes from arrangement_length - last locator."""
    snaps = [_snap(0.0), _snap(4.0)]  # 2-bar gap @ 120
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=12.0)
    assert bars == [2, 4]


def test_scene_lengths_last_scene_falls_back_to_median_gap_when_no_length():
    """Without arrangement_length, last scene matches the median of prior gaps."""
    snaps = [_snap(0.0), _snap(8.0), _snap(16.0)]
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=None)
    assert bars == [4, 4, 4]


def test_scene_lengths_single_locator_defaults_to_two_bars():
    """A 1-locator arrangement with no length info defaults to a 2-bar scene."""
    snaps = [_snap(0.0)]
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=None)
    assert bars == [2]


def test_scene_lengths_clamps_to_uint8_range():
    """Pattern bars header is uint8; very long scenes clamp at 255."""
    snaps = [_snap(0.0), _snap(1000.0)]  # ~500 bars @ 120 BPM
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=2000.0)
    assert all(1 <= b <= 255 for b in bars)
    assert bars[0] == 255  # clamped


def test_scene_lengths_quantize_drag_imprecise_locators_to_nearest_bar():
    """A locator dragged ~50ms off a bar boundary still produces clean
    integer bar gaps. Without quantization, two locators each off by a
    few ms could compound into the wrong bar count."""
    snaps = [_snap(0.04), _snap(4.06), _snap(7.92)]
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=12.0)
    assert bars == [2, 2, 2]


def test_scene_lengths_preserves_odd_bar_counts_no_pow2_snap():
    """Non-power-of-2 gaps stay as-is. A 3-bar scene is a valid musical
    section (Take Five, etc) and we do NOT silently round up to 4."""
    snaps = [_snap(0.0), _snap(6.0)]  # 3 bars @ 120 BPM
    bars = _scene_lengths_in_bars(snaps, 120.0, arrangement_length_sec=12.0)
    assert bars == [3, 3]


def test_scene_lengths_at_136_bpm_with_uneven_locators():
    """Real arrangement check: 136 BPM, 6 locators landing on bar boundaries
    1/3/6/10/14/16, arrangement length ≈16.07 bars (28.36s). Gaps should
    read [2, 3, 4, 4, 2, 1] — the trailing 1.07-bar fragment quantizes
    down to 1 bar."""
    bar_dur = 240.0 / 136.0
    locator_bars = [1, 3, 6, 10, 14, 16]
    snaps = [_snap((b - 1) * bar_dur) for b in locator_bars]
    bars = _scene_lengths_in_bars(snaps, 136.0, arrangement_length_sec=28.36)
    assert bars == [2, 3, 4, 4, 2, 1]


# ── global_sample_slot ──────────────────────────────────────────────────────


def test_global_sample_slot_default_layout():
    """Default StemForge convention: 700+ base, 20 slots per group, A/B/C/D
    offset 0/20/40/60. Avoids clobbering the 1..699 user sample range."""
    assert global_sample_slot("A", 0) == 700
    assert global_sample_slot("B", 0) == 720
    assert global_sample_slot("C", 0) == 740
    assert global_sample_slot("D", 0) == 760
    assert global_sample_slot("A", 19) == 719  # last valid manifest slot


def test_global_sample_slot_rejects_invalid_group():
    with pytest.raises(ValueError, match="group"):
        global_sample_slot("E", 0)


def test_global_sample_slot_rejects_out_of_range_manifest_slot():
    with pytest.raises(ValueError, match="manifest_slot"):
        global_sample_slot("A", 20)
    with pytest.raises(ValueError, match="manifest_slot"):
        global_sample_slot("A", -1)


def test_sample_slot_base_is_700():
    """The default base lives above the 1..699 user library range."""
    assert SAMPLE_SLOT_BASE == 700


# ── synthesize ──────────────────────────────────────────────────────────────


def test_synthesize_emits_one_scene_per_snapshot(snapshots, manifest):
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    assert len(spec.scenes) == 3


def test_synthesize_dedups_patterns_by_group_pad_bars(snapshots, manifest):
    """Group A: pad 1/4bars, pad 2/4bars, pad 3/4bars → 3 patterns.
    Group B: pad 1/8bars, pad 2/2bars → 2 patterns.
    Group C: pad 1/4bars → 1 pattern."""
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    real = [p for p in spec.patterns if p.events]
    by_group: dict[str, list] = {}
    for p in real:
        by_group.setdefault(p.group, []).append(p)
    assert sorted(by_group.keys()) == ["a", "b", "c"]
    assert len(by_group["a"]) == 3
    assert len(by_group["b"]) == 2
    assert len(by_group["c"]) == 1


def test_synthesize_pattern_indices_are_per_group_starting_at_one(snapshots, manifest):
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    real = [p for p in spec.patterns if p.events]
    by_group: dict[str, list] = {}
    for p in real:
        by_group.setdefault(p.group, []).append(p)
    for group, patterns in by_group.items():
        indices = sorted(p.index for p in patterns)
        assert indices == list(range(1, len(patterns) + 1)), group


def test_synthesize_scene_mapping_matches_expected_layout(snapshots, manifest):
    """Silent groups reference EMPTY_PATTERN_INDEX (99), not 0 — the device
    throws ERR PATTERN 189 on scene transition when a group transitions
    from a real pattern to 0."""
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    E = EMPTY_PATTERN_INDEX
    # Verse: A=loop_a1 → 1; B=bass_b1 → 1; C silent; D silent.
    assert (spec.scenes[0].a, spec.scenes[0].b, spec.scenes[0].c, spec.scenes[0].d) == (1, 1, E, E)
    # Chorus: A=loop_a2 → 2; B=bass_b1 → 1; C=vox_c1 → 1.
    assert (spec.scenes[1].a, spec.scenes[1].b, spec.scenes[1].c, spec.scenes[1].d) == (2, 1, 1, E)
    # Outro: A=loop_a3 → 3; B=bass_b2 → 2; C silent.
    assert (spec.scenes[2].a, spec.scenes[2].b, spec.scenes[2].c, spec.scenes[2].d) == (3, 2, E, E)


def test_synthesize_pad_records_use_session_tracks_slot(snapshots, manifest):
    """Slot mapping: SAMPLE_SLOT_BASE (700) + group offset (0/20/40/60) +
    manifest's per-group 0-indexed slot."""
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    pad_map = {(p.group, p.pad): p.sample_slot for p in spec.pads}
    assert pad_map[("a", 1)] == 700  # loop_a1 slot=0  → 700 + 0 + 0
    assert pad_map[("a", 2)] == 701
    assert pad_map[("a", 3)] == 702
    assert pad_map[("b", 1)] == 720
    assert pad_map[("b", 2)] == 721
    assert pad_map[("c", 1)] == 740


def test_synthesize_pads_default_to_oneshot(snapshots, manifest):
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    for pad in spec.pads:
        assert pad.play_mode == "oneshot"


def test_synthesize_sounds_dict_maps_sample_slot_to_wav(snapshots, manifest):
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    for pad in spec.pads:
        assert pad.sample_slot in spec.sounds
        assert str(spec.sounds[pad.sample_slot])


def test_synthesize_event_position_zero(snapshots, manifest):
    """Every real pattern fires a slice at evenly-spaced positions starting
    at 0. Empty marker patterns at EMPTY_PATTERN_INDEX have zero events.
    Note/velocity/duration values match captured-reference one-shots:
    note=60, vel=100, dur=96 ticks."""
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    for pattern in spec.patterns:
        if pattern.index == EMPTY_PATTERN_INDEX:
            assert pattern.events == []
            continue
        assert pattern.events, f"pattern {pattern.group}{pattern.index} has no events"
        assert pattern.events[0].position_ticks == 0
        pad = pattern.events[0].pad
        assert 1 <= pad <= 12
        for e in pattern.events:
            assert e.pad == pad
            assert e.duration_ticks == 96
            assert e.note == 60
            assert e.velocity == 100


def test_synthesize_carries_through_project_metadata(snapshots, manifest):
    spec = synthesize(snapshots, manifest, 132.5, (3, 4), 7)
    assert spec.bpm == pytest.approx(132.5)
    assert spec.time_sig == (3, 4)
    assert spec.project_slot == 7


def test_synthesize_pads_use_bpm_stretch_mode_with_manifest_bpm(snapshots, manifest):
    """The synthesizer should tag every pad with stretch_mode='bpm' and the
    source BPM from the manifest, so the device computes
    playback_speed = project_bpm / sound_bpm."""
    enriched = dict(manifest)
    enriched["bpm"] = 135.99
    spec = synthesize(snapshots, enriched, 90.67, (4, 4), 1)
    assert spec.pads, "fixture should produce at least one pad"
    for pad in spec.pads:
        assert pad.stretch_mode == "bpm"
        assert pad.sound_bpm == pytest.approx(135.99)


def test_synthesize_falls_back_to_project_bpm_when_manifest_lacks_bpm(snapshots, manifest):
    """No manifest bpm → fall back to project_bpm (1.0× playback)."""
    assert "bpm" not in manifest, "fixture manifest must omit bpm for this test"
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    for pad in spec.pads:
        assert pad.stretch_mode == "bpm"
        assert pad.sound_bpm == pytest.approx(120.0)


def test_synthesize_pattern_bars_match_scene_length_from_locator_gaps(
    snapshots, manifest, arrangement
):
    """Pattern bars come from the scene's locator-to-locator gap, not from
    any individual slice's length. The fixture has locators at 0/8/16s @
    120 BPM (= 4 bars apart), so every populated pattern should be bars=4."""
    spec = synthesize(
        snapshots,
        manifest,
        arrangement["tempo"],
        tuple(arrangement["time_sig"]),
        1,
        arrangement_length_sec=arrangement["arrangement_length_sec"],
    )
    populated = [p for p in spec.patterns if p.index != EMPTY_PATTERN_INDEX]
    assert populated, "fixture should produce populated patterns"
    for p in populated:
        assert p.bars == 4, f"pattern {p.group}{p.index} has bars={p.bars}, expected 4"


def test_synthesize_tiles_one_bar_slice_across_four_bar_scene(snapshots, manifest, arrangement):
    """A 1-bar render played in a 4-bar scene tiles 4× across the pattern
    (positions 0, 1, 2, 3 bars). This is the durable fix for "scene plays
    for the locator gap, not just the slice length"."""
    enriched = json.loads(json.dumps(manifest))
    enriched["bpm"] = 120.0
    enriched["session_tracks"]["A"][0]["clip_length_sec"] = 2.0  # 1 bar @ 120 BPM
    spec = synthesize(
        snapshots,
        enriched,
        arrangement["tempo"],
        tuple(arrangement["time_sig"]),
        1,
        arrangement_length_sec=arrangement["arrangement_length_sec"],
    )
    pat = next(p for p in spec.patterns if p.group == "a" and p.events and p.events[0].pad == 1)
    assert pat.bars == 4
    assert len(pat.events) == 4
    expected = [i * TICKS_PER_BAR for i in range(4)]
    assert [e.position_ticks for e in pat.events] == expected


def test_synthesize_two_bar_slice_in_four_bar_scene_fires_twice(
    snapshots, manifest, arrangement
):
    """A 2-bar slice tiles to 2 events at positions 0 and 2 bars."""
    enriched = json.loads(json.dumps(manifest))
    enriched["bpm"] = 120.0
    enriched["session_tracks"]["A"][0]["clip_length_sec"] = 4.0  # 2 bars @ 120
    spec = synthesize(
        snapshots,
        enriched,
        arrangement["tempo"],
        tuple(arrangement["time_sig"]),
        1,
        arrangement_length_sec=arrangement["arrangement_length_sec"],
    )
    pat = next(p for p in spec.patterns if p.group == "a" and p.events and p.events[0].pad == 1)
    assert pat.bars == 4
    assert len(pat.events) == 2
    assert [e.position_ticks for e in pat.events] == [0, 2 * TICKS_PER_BAR]


def test_synthesize_threads_slot_slices_from_manifest(snapshots, manifest):
    """Manifest entries with start_offset_sec + clip_length_sec must show up
    in PpakSpec.slot_slices keyed by global sample slot, so the writer can
    slice the WAV at upload time. Slice end is derived from
    start_offset_sec + clip_length_sec, not end_offset_sec, because some
    real manifests have inconsistent end_offset_sec values."""
    enriched = json.loads(json.dumps(manifest))
    enriched["session_tracks"]["A"][0]["start_offset_sec"] = 4.0
    enriched["session_tracks"]["A"][0]["clip_length_sec"] = 1.765
    enriched["session_tracks"]["A"][0]["end_offset_sec"] = 99.0  # bogus, should be ignored
    spec = synthesize(snapshots, enriched, 90.67, (4, 4), 1)
    assert 700 in spec.slot_slices
    assert spec.slot_slices[700] == pytest.approx((4.0, 5.765))
    other_slots = set(spec.sounds.keys()) - {700}
    for slot in other_slots:
        assert slot not in spec.slot_slices


def test_synthesize_rejects_invalid_project_slot(snapshots, manifest):
    with pytest.raises(ValueError, match="project_slot"):
        synthesize(snapshots, manifest, 120.0, (4, 4), 0)
    with pytest.raises(ValueError, match="project_slot"):
        synthesize(snapshots, manifest, 120.0, (4, 4), 10)


def test_synthesize_rejects_more_than_99_scenes(manifest):
    snaps = [
        Snapshot(
            locator_time_sec=float(i),
            locator_name=f"loc{i}",
            a_clip=None,
            b_clip=None,
            c_clip=None,
            d_clip=None,
        )
        for i in range(MAX_SCENES + 1)
    ]
    with pytest.raises(ValueError, match="too many scenes"):
        synthesize(snaps, manifest, 120.0, (4, 4), 1)


def test_synthesize_at_max_scenes_succeeds(manifest):
    snaps = [
        Snapshot(
            locator_time_sec=float(i),
            locator_name=f"loc{i}",
            a_clip=None,
            b_clip=None,
            c_clip=None,
            d_clip=None,
        )
        for i in range(MAX_SCENES)
    ]
    spec = synthesize(snaps, manifest, 120.0, (4, 4), 1)
    assert len(spec.scenes) == MAX_SCENES


def test_synthesize_silent_groups_reference_empty_pattern(manifest):
    """All four groups silent → one empty pattern emitted per group, all at
    EMPTY_PATTERN_INDEX (99). No pads, no sounds."""
    snaps = [
        Snapshot(
            locator_time_sec=0.0,
            locator_name="silent",
            a_clip=None,
            b_clip=None,
            c_clip=None,
            d_clip=None,
        )
    ]
    spec = synthesize(snaps, manifest, 120.0, (4, 4), 1)
    E = EMPTY_PATTERN_INDEX
    assert (spec.scenes[0].a, spec.scenes[0].b, spec.scenes[0].c, spec.scenes[0].d) == (E, E, E, E)
    assert sorted((p.group, p.index, len(p.events)) for p in spec.patterns) == [
        ("a", E, 0),
        ("b", E, 0),
        ("c", E, 0),
        ("d", E, 0),
    ]
    assert spec.pads == []
    assert spec.sounds == {}


def test_synthesize_rejects_more_than_12_pads_per_group():
    """13 distinct slots on group A blows the 12-pad cap."""
    manifest = {
        "session_tracks": {
            "A": [
                {"slot": i, "file": f"/x/{i}.wav", "clip_length_sec": 2.0}
                for i in range(MAX_PADS_PER_GROUP + 1)
            ],
            "B": [],
            "C": [],
            "D": [],
        }
    }
    snaps = [
        Snapshot(
            locator_time_sec=float(i),
            locator_name=f"loc{i}",
            a_clip=ArrangementClip(
                file_path=f"/x/{i}.wav",
                start_time_sec=0.0,
                length_sec=2.0,
                warping=1,
            ),
            b_clip=None,
            c_clip=None,
            d_clip=None,
        )
        for i in range(MAX_PADS_PER_GROUP + 1)
    ]
    with pytest.raises(ValueError, match="pads"):
        synthesize(snaps, manifest, 120.0, (4, 4), 1)


def test_synthesize_same_pad_reused_across_scenes_emits_one_pattern(manifest):
    """Same (group, pad, bars) across multiple scenes → one Pattern."""
    snaps = [
        Snapshot(
            locator_time_sec=0.0,
            locator_name="A",
            a_clip=ArrangementClip(
                file_path="/songs/test/A/loop_a1.wav",
                start_time_sec=0.0,
                length_sec=8.0,
                warping=1,
            ),
            b_clip=None,
            c_clip=None,
            d_clip=None,
        ),
        Snapshot(
            locator_time_sec=8.0,
            locator_name="B",
            a_clip=ArrangementClip(
                file_path="/songs/test/A/loop_a1.wav",
                start_time_sec=0.0,
                length_sec=8.0,
                warping=1,
            ),
            b_clip=None,
            c_clip=None,
            d_clip=None,
        ),
    ]
    spec = synthesize(snaps, manifest, 120.0, (4, 4), 1)
    real = [p for p in spec.patterns if p.events]
    assert len(real) == 1
    assert spec.scenes[0].a == 1
    assert spec.scenes[1].a == 1


def test_synthesize_empty_markers_match_scene_bars(snapshots, manifest, arrangement):
    """Silent groups in a scene must reference an empty pattern whose bars
    match the scene's length. Otherwise the device's scene-length rule
    (apparently min-bars-across-groups) truncates real patterns to the
    empty's bars. Verified on hardware 2026-04-28."""
    spec = synthesize(
        snapshots,
        manifest,
        arrangement["tempo"],
        tuple(arrangement["time_sig"]),
        1,
        arrangement_length_sec=arrangement["arrangement_length_sec"],
    )
    for scene_idx, sc in enumerate(spec.scenes):
        for group, idx in (("a", sc.a), ("b", sc.b), ("c", sc.c), ("d", sc.d)):
            if not spec.patterns:
                continue
            ref_pat = next(
                (p for p in spec.patterns if p.group == group and p.index == idx),
                None,
            )
            assert ref_pat is not None, (
                f"scene {scene_idx + 1} references undefined pattern {group}{idx:02d}"
            )
            assert ref_pat.bars == 4, (
                f"scene {scene_idx + 1}, group {group}: pattern bars={ref_pat.bars}, "
                f"expected 4 (scene length)"
            )


def test_synthesize_allocates_distinct_empty_indices_per_scene_bars():
    """Two scenes with different bar counts that both have silent groups
    get DIFFERENT empty-pattern indices, each sized to its own scene."""
    arr = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 12.0,  # 6 bars
        "locators": [
            {"time_sec": 0.0, "name": "short"},
            {"time_sec": 4.0, "name": "long"},  # gap 1 = 2 bars; gap 2 = 4 bars
        ],
        "tracks": {
            "A": [
                {
                    "file_path": "/songs/test/A/loop_a1.wav",
                    "start_time_sec": 0.0,
                    "length_sec": 12.0,
                    "warping": 1,
                },
            ],
            "B": [],
            "C": [],
            "D": [],
        },
    }
    manifest = {
        "session_tracks": {
            "A": [
                {
                    "slot": 0,
                    "file": "/songs/test/A/loop_a1.wav",
                    "clip_length_sec": 2.0,
                    "mode": "trim",
                }
            ],
            "B": [],
            "C": [],
            "D": [],
        }
    }
    snaps = resolve_scenes(arr, manifest)
    spec = synthesize(snaps, manifest, 120.0, (4, 4), 1, arrangement_length_sec=12.0)
    pat_by_id = {(p.group, p.index): p for p in spec.patterns}
    sc1, sc2 = spec.scenes[0], spec.scenes[1]
    for grp_name, idx in (("b", sc1.b), ("c", sc1.c), ("d", sc1.d)):
        assert pat_by_id[(grp_name, idx)].bars == 2
    for grp_name, idx in (("b", sc2.b), ("c", sc2.c), ("d", sc2.d)):
        assert pat_by_id[(grp_name, idx)].bars == 4
    # Indices for the two scene_bars must differ (per group).
    assert sc1.b != sc2.b
    assert sc1.c != sc2.c
    assert sc1.d != sc2.d


def test_synthesize_default_song_positions_play_scenes_in_order(snapshots, manifest):
    """Default song-mode positions: play scenes 1..N in order. The user can
    edit the song list on-device after import."""
    spec = synthesize(snapshots, manifest, 120.0, (4, 4), 1)
    assert spec.song_positions == [1, 2, 3]
