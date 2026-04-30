"""Round-trip unit tests for ``ep133.song.format`` byte builders.

We mirror phones24's read logic (``parsers.ts``) in pure Python and
assert that bytes produced by our builders parse back to the input
values. The parsers here are deliberately minimal — they cover only
what we need to validate, not the full file format.
"""

from __future__ import annotations

import struct

import pytest

from ep133.song.format import (
    PAD_RECORD_SIZE,
    PATTERN_HEADER_SIZE,
    SCENES_HEADER_SIZE,
    SETTINGS_SIZE,
    TICKS_PER_BAR,
    Event,
    PadSpec,
    SceneSpec,
    build_pad,
    build_pattern,
    build_scenes,
    build_settings,
    pad_filename,
    pattern_filename,
)

# ----- Mini-parsers (mirror parsers.ts) -------------------------------------


def parse_pattern(data: bytes) -> dict:
    """Mirror ``parsers.ts:parsePatterns`` — header byte 1 = bars, 8-byte chunks."""
    bars = data[1]
    event_count = data[2]
    notes: dict[int, list[dict]] = {}
    # parsers.ts: chunkArray(data, 8, startOffset=4) — 8-byte chunks from offset 4
    for i in range(PATTERN_HEADER_SIZE, len(data), 8):
        chunk = data[i : i + 8]
        if len(chunk) < 8:
            break
        # parsers.ts: skip "weird chunks" where pad indicator isn't a multiple of 8
        if chunk[2] % 8 != 0:
            continue
        # Event encodes pad as 0-indexed (file paths use 1-indexed). Re-add 1
        # so callers can assert against the 1-indexed pad they passed in.
        pad = (chunk[2] // 8) + 1
        notes.setdefault(pad, []).append(
            {
                "note": chunk[3],
                "position": (chunk[1] << 8) + chunk[0],
                "duration": (chunk[6] << 8) + chunk[5],
                "velocity": chunk[4],
            }
        )
    return {"bars": bars, "event_count": event_count, "notes": notes}


def parse_scenes(data: bytes) -> dict:
    """Mirror ``parsers.ts:collectScenesAndPatterns`` — 6-byte chunks from offset 7."""
    chunks = []
    for i in range(SCENES_HEADER_SIZE, len(data), 6):
        chunk = data[i : i + 6]
        if len(chunk) < 6:
            break
        chunks.append({"a": chunk[0], "b": chunk[1], "c": chunk[2], "d": chunk[3]})
    # collectScenesSettings reads numerator at byte 11, denominator at byte 12.
    return {
        "numerator": data[11] if len(data) > 11 else 0,
        "denominator": data[12] if len(data) > 12 else 0,
        "scenes": chunks,
    }


def parse_pad(data: bytes) -> dict:
    """Decode the fields we patch in :func:`build_pad`."""
    if len(data) != PAD_RECORD_SIZE:
        raise AssertionError(f"pad must be {PAD_RECORD_SIZE} bytes, got {len(data)}")
    sample_slot = struct.unpack_from("<H", data, 1)[0]
    bpm = struct.unpack_from("<f", data, 12)[0]
    ts_mode = data[21]
    ts_bars_raw = data[25]
    play_mode_raw = data[23]
    play_mode = (
        "oneshot"
        if play_mode_raw == 0
        else "key"
        if play_mode_raw == 1
        else "legato"
        if play_mode_raw == 2
        else f"unknown({play_mode_raw})"
    )
    bars_decoded = (
        1
        if ts_bars_raw == 0
        else 2
        if ts_bars_raw == 1
        else 4
        if ts_bars_raw == 2
        else 0.5
        if ts_bars_raw == 255
        else 0.25
        if ts_bars_raw == 254
        else None
    )
    return {
        "sample_slot": sample_slot,
        "stretch_bpm": bpm,
        "stretch_mode": ts_mode,
        "play_mode": play_mode,
        "stretch_bars": bars_decoded,
    }


def parse_settings(data: bytes) -> dict:
    if len(data) != SETTINGS_SIZE:
        raise AssertionError(f"settings must be {SETTINGS_SIZE} bytes, got {len(data)}")
    return {"bpm": struct.unpack_from("<f", data, 4)[0]}


# ----- Pad record size assertion --------------------------------------------


def test_pad_record_size_is_26_bytes():
    """Factory native pad records are 26 bytes. Sample Tool emits 27-byte
    records (extra trailing 0x00) which the device tolerates on import but
    corrupts during scene-switch iteration (off-by-one stride → ERR PATTERN
    189). We standardise on the factory format."""
    assert PAD_RECORD_SIZE == 26


# ----- Pattern builder tests -------------------------------------------------


def test_build_pattern_empty_has_correct_header():
    blob = build_pattern([], bars=1)
    assert blob == bytes([0x00, 0x01, 0x00, 0x00])


def test_build_pattern_header_byte1_is_bars_not_constant():
    """Regression: header[1] must be `bars`, not 0x01 (DannyDesert bug)."""
    blob = build_pattern([], bars=4)
    assert blob[1] == 4
    blob2 = build_pattern([], bars=2)
    assert blob2[1] == 2


def test_build_pattern_single_event_round_trip():
    ev = Event(position_ticks=0, pad=5, note=60, velocity=127, duration_ticks=384)
    blob = build_pattern([ev], bars=1)
    parsed = parse_pattern(blob)
    assert parsed["bars"] == 1
    assert parsed["event_count"] == 1
    assert 5 in parsed["notes"]
    note = parsed["notes"][5][0]
    assert note["position"] == 0
    assert note["note"] == 60
    assert note["velocity"] == 127
    assert note["duration"] == 384


def test_build_pattern_multi_event_round_trip():
    events = [
        Event(0, 1, 60, 100, 96),
        Event(96, 7, 60, 90, 48),
        Event(192, 1, 60, 100, 96),
        Event(288, 7, 60, 90, 48),
    ]
    blob = build_pattern(events, bars=1)
    parsed = parse_pattern(blob)
    assert parsed["event_count"] == 4
    assert sorted(n["position"] for n in parsed["notes"][1]) == [0, 192]
    assert sorted(n["position"] for n in parsed["notes"][7]) == [96, 288]


def test_build_pattern_event_byte3_is_midi_note():
    """Regression: byte 3 of an event is a MIDI note, not a constant."""
    blob = build_pattern([Event(0, 1, 72, 100, 100)], bars=1)
    # Event begins at offset 4. Note is byte 3 of the event = absolute offset 7.
    assert blob[7] == 72


def test_build_pattern_event_bytes_5_6_are_duration():
    """Regression: bytes 5..6 are duration uint16 LE (not 0x10/0x00 flags)."""
    blob = build_pattern([Event(0, 1, 60, 100, 0x1234)], bars=1)
    assert blob[9] == 0x34
    assert blob[10] == 0x12


def test_build_pattern_position_is_uint16_le():
    blob = build_pattern([Event(0x0123, 1, 60, 100, 100)], bars=2)
    assert blob[4] == 0x23
    assert blob[5] == 0x01


def test_build_pattern_pad_indicator_is_pad_minus_one_times_8():
    """Pad indicator at event-byte 2 is (pad - 1) * 8 — events are 0-indexed
    while file paths are 1-indexed (pads/{group}/p01..p12). Verified against
    minimal device reference: pad file p10 fires from event byte 0x48
    (= 9 * 8 = (10-1) * 8)."""
    blob = build_pattern([Event(0, 5, 60, 100, 100)], bars=1)
    assert blob[6] == (5 - 1) * 8  # 0x20
    blob2 = build_pattern([Event(0, 12, 60, 100, 100)], bars=1)
    assert blob2[6] == (12 - 1) * 8  # 0x58


def test_build_pattern_event_byte7_is_zero_padding():
    blob = build_pattern([Event(0, 1, 60, 100, 100)], bars=1)
    assert blob[11] == 0x00


def test_build_pattern_sorts_events_by_position():
    out_of_order = [
        Event(192, 1, 60, 100, 96),
        Event(0, 1, 60, 100, 96),
        Event(96, 1, 60, 100, 96),
    ]
    blob = build_pattern(out_of_order, bars=1)
    positions = [struct.unpack_from("<H", blob, off)[0] for off in (4, 12, 20)]
    assert positions == [0, 96, 192]


def test_build_pattern_rejects_too_many_events():
    events = [Event(i, 1, 60, 100, 1) for i in range(256)]
    with pytest.raises(ValueError, match="too many events"):
        build_pattern(events, bars=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"position_ticks": -1},
        {"position_ticks": 0x10000},
        {"pad": 0},
        {"pad": 13},
        {"note": -1},
        {"note": 128},
        {"velocity": -1},
        {"velocity": 128},
        {"duration_ticks": -1},
        {"duration_ticks": 0x10000},
    ],
)
def test_build_pattern_rejects_out_of_range(kwargs):
    base = {"position_ticks": 0, "pad": 1, "note": 60, "velocity": 100, "duration_ticks": 100}
    base.update(kwargs)
    with pytest.raises(ValueError):
        build_pattern([Event(**base)], bars=1)


@pytest.mark.parametrize("bars", [0, 256, -1])
def test_build_pattern_rejects_invalid_bars(bars):
    with pytest.raises(ValueError):
        build_pattern([], bars=bars)


def test_build_pattern_two_bars_event_at_far_position():
    pos = 2 * TICKS_PER_BAR - 1
    blob = build_pattern([Event(pos, 1, 60, 100, 50)], bars=2)
    parsed = parse_pattern(blob)
    assert parsed["bars"] == 2
    assert parsed["notes"][1][0]["position"] == pos


# ----- Scenes builder tests --------------------------------------------------


def test_build_scenes_empty_header_only():
    """Device requires fixed 712-byte scenes file even when no scenes are
    populated (7 header + 99 × 6 scene slots + 111 trailer)."""
    blob = build_scenes([], (4, 4))
    assert len(blob) == 712
    assert blob[5] == 4
    assert blob[6] == 4


def test_build_scenes_single_scene_round_trip():
    sc = SceneSpec(a=1, b=2, c=0, d=3)
    blob = build_scenes([sc], (4, 4))
    parsed = parse_scenes(blob)
    assert parsed["numerator"] == 4
    assert parsed["denominator"] == 4
    assert parsed["scenes"][0] == {"a": 1, "b": 2, "c": 0, "d": 3}


def test_build_scenes_multiple_round_trip():
    scenes = [
        SceneSpec(1, 1, 1, 1),
        SceneSpec(2, 2, 0, 2),
        SceneSpec(0, 3, 3, 3),
    ]
    blob = build_scenes(scenes, (3, 4))
    parsed = parse_scenes(blob)
    assert parsed["scenes"][0] == {"a": 1, "b": 1, "c": 1, "d": 1}
    assert parsed["scenes"][2] == {"a": 0, "b": 3, "c": 3, "d": 3}
    assert parsed["scenes"][3] == {"a": 0, "b": 0, "c": 0, "d": 0}
    assert parsed["numerator"] == 3
    assert parsed["denominator"] == 4


def test_build_scenes_time_sig_at_bytes_11_12():
    """Per spec, byte 11 = numerator, byte 12 = denominator (these fall
    within chunk 0 at chunk-offsets 4..5)."""
    blob = build_scenes([SceneSpec(1, 1, 1, 1)], (7, 8))
    assert blob[11] == 7
    assert blob[12] == 8


def test_build_scenes_total_size_is_712_with_full_99_slots():
    """7-byte header + 99 × 6-byte scene slots + 111-byte trailer = 712 total.
    A short scenes file causes the device to error on load (ERR PATTERN ...)."""
    blob = build_scenes([SceneSpec(i + 1, 0, 0, 0) for i in range(50)], (4, 4))
    assert len(blob) == 712


def test_build_scenes_song_positions_in_trailer():
    """Default song-position is [1] (1-position song pointing at scene 1).
    Trailer byte 11 holds the position count; bytes 12.. hold scene refs."""
    scenes = [SceneSpec(1, 0, 0, 0), SceneSpec(2, 0, 0, 0), SceneSpec(3, 0, 0, 0)]
    blob = build_scenes(scenes, (4, 4), song_positions=[1, 2, 3, 2, 1])
    # Trailer starts at offset 7 + 99*6 = 601. Position count at trailer[11]
    # = byte 612; positions at trailer[12..] = bytes 613..
    assert blob[612] == 5
    assert list(blob[613:618]) == [1, 2, 3, 2, 1]


def test_build_scenes_trailer_scene_count_at_offset_604():
    """Trailer bytes 0..3 hold scene count as BE uint32; effectively a single
    byte at trailer[3] = file byte 604 since count < 256."""
    scenes = [SceneSpec(i + 1, 0, 0, 0) for i in range(7)]
    blob = build_scenes(scenes, (4, 4))
    # 7 scenes
    assert blob[604] == 7


def test_build_scenes_default_song_position_is_scene_one():
    """When song_positions is None, default to a 1-position song pointing
    at scene 1 (matches device's default state)."""
    scenes = [SceneSpec(1, 0, 0, 0)]
    blob = build_scenes(scenes, (4, 4))
    # trailer[11] = position count = 1; trailer[12] = scene index = 1
    assert blob[612] == 1
    assert blob[613] == 1


def test_build_scenes_rejects_too_many():
    scenes = [SceneSpec(1, 0, 0, 0)] * 100
    with pytest.raises(ValueError, match="too many scenes"):
        build_scenes(scenes, (4, 4))


def test_build_scenes_rejects_pattern_index_out_of_range():
    with pytest.raises(ValueError):
        build_scenes([SceneSpec(100, 0, 0, 0)], (4, 4))


def test_build_scenes_rejects_song_position_out_of_range():
    scenes = [SceneSpec(1, 0, 0, 0)]
    with pytest.raises(ValueError, match="song_positions"):
        build_scenes(scenes, (4, 4), song_positions=[100])
    with pytest.raises(ValueError, match="song_positions"):
        build_scenes(scenes, (4, 4), song_positions=[0])


def test_build_scenes_rejects_too_many_song_positions():
    scenes = [SceneSpec(1, 0, 0, 0)]
    with pytest.raises(ValueError, match="too many song positions"):
        build_scenes(scenes, (4, 4), song_positions=[1] * 100)


# ----- Pad builder tests -----------------------------------------------------


def test_build_pad_zero_template_round_trip():
    """BARS mode: explicit stretch_mode='bars' to test BPM/bars encoding.
    Default mode is 'none' (one-shot) per ERR PATTERN 189 debug."""
    blob = build_pad(
        sample_slot=42,
        play_mode="oneshot",
        time_stretch_bars=1,
        template=None,
        project_bpm=120.0,
        stretch_mode="bars",
    )
    parsed = parse_pad(blob)
    assert parsed["sample_slot"] == 42
    assert parsed["play_mode"] == "oneshot"
    assert parsed["stretch_bars"] == 1
    assert parsed["stretch_mode"] == 2  # BARS mode
    assert parsed["stretch_bpm"] == pytest.approx(120.0)


def test_build_pad_default_size_is_26_bytes():
    """A pad built from a None template should land at exactly PAD_RECORD_SIZE."""
    blob = build_pad(
        sample_slot=1,
        play_mode="oneshot",
        time_stretch_bars=1,
        project_bpm=120.0,
        stretch_mode="bars",
    )
    assert len(blob) == 26


def test_build_pad_play_mode_encoding():
    for mode, code in (("oneshot", 0), ("key", 1), ("legato", 2)):
        blob = build_pad(sample_slot=1, play_mode=mode, time_stretch_bars=1, project_bpm=120.0)
        assert blob[23] == code


def test_build_pad_time_stretch_bars_encoding():
    encoding = {1: 0, 2: 1, 4: 2}
    for bars, raw in encoding.items():
        blob = build_pad(
            sample_slot=1,
            play_mode="oneshot",
            time_stretch_bars=bars,
            project_bpm=120.0,
            stretch_mode="bars",
        )
        assert blob[25] == raw


def test_build_pad_preserves_template_bytes_outside_patches():
    """Mark every byte uniquely so we can detect over-patching."""
    template = bytes(range(PAD_RECORD_SIZE))
    blob = build_pad(
        sample_slot=0xABCD,
        play_mode="key",
        time_stretch_bars=2,
        template=template,
        project_bpm=140.0,
    )
    # Bytes we patch: 1, 2 (sample_slot), 12..15 (bpm), 21 (mode), 23 (play_mode), 25 (bars).
    patched = {1, 2, 12, 13, 14, 15, 21, 23, 25}
    for i in range(PAD_RECORD_SIZE):
        if i in patched:
            continue
        assert blob[i] == template[i], f"unexpected change at byte {i}"


def test_build_pad_no_bpm_does_not_touch_bytes_12_15():
    """In BARS mode without project_bpm, bytes 12..15 stay from template."""
    template = bytes(range(PAD_RECORD_SIZE))
    blob = build_pad(
        sample_slot=1,
        play_mode="oneshot",
        time_stretch_bars=1,
        template=template,
        project_bpm=None,
        stretch_mode="bars",
    )
    assert blob[12:16] == template[12:16]


def test_build_pad_rejects_invalid_template_size():
    """27-byte input rejected — Sample Tool's non-canonical format."""
    with pytest.raises(ValueError, match="pad template must be"):
        build_pad(
            sample_slot=1,
            play_mode="oneshot",
            time_stretch_bars=1,
            template=b"\x00" * 27,
        )


def test_build_pad_bpm_mode_sets_byte_21_and_writes_source_bpm():
    """stretch_mode='bpm' → byte 21 = 1 (BPM), bytes 12..15 = float32 source BPM."""
    blob = build_pad(
        sample_slot=42,
        play_mode="oneshot",
        time_stretch_bars=1,
        stretch_mode="bpm",
        sound_bpm=135.99,
    )
    parsed = parse_pad(blob)
    assert parsed["sample_slot"] == 42
    assert parsed["play_mode"] == "oneshot"
    assert parsed["stretch_mode"] == 1  # BPM mode
    assert parsed["stretch_bpm"] == pytest.approx(135.99)
    assert blob[25] == 0  # bars byte cleared in BPM mode


def test_build_pad_bpm_mode_requires_sound_bpm():
    with pytest.raises(ValueError, match="stretch_mode='bpm' requires sound_bpm"):
        build_pad(
            sample_slot=1,
            play_mode="oneshot",
            time_stretch_bars=1,
            stretch_mode="bpm",
        )


def test_build_pad_bpm_mode_rejects_out_of_range_bpm():
    """Device rejects sound.bpm outside 1..200 (PROTOCOL.md §5)."""
    for bad in (0.5, 250.0, -10.0):
        with pytest.raises(ValueError, match="sound_bpm"):
            build_pad(
                sample_slot=1,
                play_mode="oneshot",
                time_stretch_bars=1,
                stretch_mode="bpm",
                sound_bpm=bad,
            )


def test_build_pad_rejects_unknown_stretch_mode():
    with pytest.raises(ValueError, match="stretch_mode"):
        build_pad(
            sample_slot=1,
            play_mode="oneshot",
            time_stretch_bars=1,
            stretch_mode="bogus",
        )


def test_build_pad_one_shot_default_clears_stretch_bytes():
    """Default mode 'none' clears bytes 21 and 25 — required for the device
    to treat the pad as a one-shot and not attempt stretch math on it."""
    blob = build_pad(
        sample_slot=1,
        play_mode="oneshot",
        time_stretch_bars=1,
    )
    assert blob[21] == 0
    assert blob[25] == 0


def test_build_pad_writes_sample_length_frames():
    """Bytes 8..11 = sample length in frames (uint32 LE). Required for
    transitions per PROTOCOL.md §7."""
    blob = build_pad(
        sample_slot=1,
        play_mode="oneshot",
        time_stretch_bars=1,
        sample_length_frames=46875,  # 1 second at native rate
    )
    assert struct.unpack_from("<I", blob, 8)[0] == 46875


def test_build_pad_rejects_oversized_sample_length():
    with pytest.raises(ValueError, match="sample_length_frames"):
        build_pad(
            sample_slot=1,
            play_mode="oneshot",
            time_stretch_bars=1,
            sample_length_frames=0x100000000,
        )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"sample_slot": -1}, "sample_slot"),
        ({"sample_slot": 0x10000}, "sample_slot"),
        ({"play_mode": "bogus"}, "play_mode"),
        ({"time_stretch_bars": 8}, "time_stretch_bars"),
    ],
)
def test_build_pad_rejects_invalid_args(kwargs, expected):
    """Use stretch_mode='bars' so time_stretch_bars validation kicks in."""
    base = {
        "sample_slot": 1,
        "play_mode": "oneshot",
        "time_stretch_bars": 1,
        "project_bpm": 120.0,
        "stretch_mode": "bars",
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=expected):
        build_pad(**base)


# ----- Settings builder tests ------------------------------------------------


def test_build_settings_patches_bpm_only():
    template = bytes(range(SETTINGS_SIZE))
    blob = build_settings(140.0, template)
    parsed = parse_settings(blob)
    assert parsed["bpm"] == pytest.approx(140.0)
    # All bytes outside 4..7 must be untouched.
    for i in range(SETTINGS_SIZE):
        if 4 <= i <= 7:
            continue
        assert blob[i] == template[i], f"settings template clobbered at byte {i}"


def test_build_settings_rejects_wrong_template_size():
    with pytest.raises(ValueError, match="settings template must be"):
        build_settings(120.0, bytes(SETTINGS_SIZE + 1))


def test_settings_size_is_222_bytes():
    assert SETTINGS_SIZE == 222


# ----- Path helpers ----------------------------------------------------------


def test_pattern_filename_zero_pads_index():
    """Device requires patterns/{group}{NN} (no slash between group and
    number). Verified from captured backup; nested-path entries
    (patterns/a/01) are silently ignored by the device."""
    assert pattern_filename("a", 1) == "patterns/a01"
    assert pattern_filename("d", 99) == "patterns/d99"


def test_pad_filename_zero_pads():
    assert pad_filename("a", 1) == "pads/a/p01"
    assert pad_filename("c", 12) == "pads/c/p12"


def test_path_helpers_reject_bad_input():
    with pytest.raises(ValueError):
        pattern_filename("e", 1)
    with pytest.raises(ValueError):
        pattern_filename("a", 0)
    with pytest.raises(ValueError):
        pad_filename("a", 13)


# ----- Dataclass smoke -------------------------------------------------------


def test_pad_spec_construction():
    """Just verify the dataclass exists and accepts the spec'd fields."""
    pd = PadSpec(group="a", pad=1, sample_slot=100, play_mode="oneshot", time_stretch_bars=2)
    assert pd.sample_slot == 100
    assert pd.time_stretch_bars == 2
