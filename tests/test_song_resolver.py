"""Tests for the EP-133 snapshot resolver.

The resolver takes an arrangement-shaped JSON dict (locators + tracks
A/B/C/D) and produces one Snapshot per locator. Nothing in the resolver
is Ableton-specific — any DAW or generated arrangement that produces
the same shape works.
"""

from __future__ import annotations

import pytest

from ep133.song.resolver import (
    ArrangementClip,
    ManifestLookupError,
    Snapshot,
    lookup_pad,
    resolve_scenes,
)


def test_resolve_scenes_emits_one_per_locator(arrangement, manifest):
    snaps = resolve_scenes(arrangement, manifest)
    assert len(snaps) == 3
    assert [s.locator_name for s in snaps] == ["Verse", "Chorus", "Outro"]
    assert [s.locator_time_sec for s in snaps] == [0.0, 8.0, 16.0]


def test_locators_returned_in_time_order_even_if_input_is_unsorted(manifest):
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 24.0,
        "locators": [
            {"time_sec": 16.0, "name": "Outro"},
            {"time_sec": 0.0, "name": "Verse"},
            {"time_sec": 8.0, "name": "Chorus"},
        ],
        "tracks": {"A": [], "B": [], "C": [], "D": []},
    }
    snaps = resolve_scenes(arrangement, manifest)
    assert [s.locator_name for s in snaps] == ["Verse", "Chorus", "Outro"]


def test_verse_snapshot_picks_loop_a1_and_bass_b1(arrangement, manifest):
    snaps = resolve_scenes(arrangement, manifest)
    verse = snaps[0]
    assert verse.a_clip is not None
    assert verse.a_clip.file_path == "/songs/test/A/loop_a1.wav"
    assert verse.b_clip is not None
    assert verse.b_clip.file_path == "/songs/test/B/bass_b1.wav"
    assert verse.c_clip is None
    assert verse.d_clip is None


def test_overlapping_clips_pick_latest_started(arrangement, manifest):
    """At t=8s, both loop_a1 (started 0) and loop_a2 (started 4) overlap.
    But loop_a1 ends at 8 exactly (strict <), so only loop_a2 is active.
    """
    snaps = resolve_scenes(arrangement, manifest)
    chorus = snaps[1]
    assert chorus.a_clip is not None
    assert chorus.a_clip.file_path == "/songs/test/A/loop_a2.wav"


def test_overlap_at_inner_time_picks_latest_started(manifest):
    """At t=5s, both loop_a1 (0-8) and loop_a2 (4-16) are active and neither
    has ended. Latest-started (loop_a2) wins — matches Ableton's playback
    semantics for arrangement-view clip overlap."""
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 24.0,
        "locators": [{"time_sec": 5.0, "name": "Overlap"}],
        "tracks": {
            "A": [
                {
                    "file_path": "/songs/test/A/loop_a1.wav",
                    "start_time_sec": 0.0,
                    "length_sec": 8.0,
                    "warping": 1,
                },
                {
                    "file_path": "/songs/test/A/loop_a2.wav",
                    "start_time_sec": 4.0,
                    "length_sec": 12.0,
                    "warping": 1,
                },
            ],
            "B": [],
            "C": [],
            "D": [],
        },
    }
    snaps = resolve_scenes(arrangement, manifest)
    assert snaps[0].a_clip.file_path == "/songs/test/A/loop_a2.wav"


def test_no_clip_at_locator_yields_silent_group(arrangement, manifest):
    snaps = resolve_scenes(arrangement, manifest)
    # Track D has no clips at all → silent in every snapshot.
    for snap in snaps:
        assert snap.d_clip is None
    # Verse: track C is silent (vox_c1 starts at 8.0).
    assert snaps[0].c_clip is None


def test_locator_at_clip_end_excludes_clip(manifest):
    """Locator exactly at clip end → clip is NOT active (strict <)."""
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 16.0,
        "locators": [{"time_sec": 8.0, "name": "End"}],
        "tracks": {
            "A": [
                {
                    "file_path": "/songs/test/A/loop_a1.wav",
                    "start_time_sec": 0.0,
                    "length_sec": 8.0,
                    "warping": 1,
                }
            ],
            "B": [],
            "C": [],
            "D": [],
        },
    }
    snaps = resolve_scenes(arrangement, manifest)
    assert snaps[0].a_clip is None


def test_locator_at_clip_start_includes_clip(manifest):
    """Locator at exact clip start → clip IS active (inclusive <=)."""
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 16.0,
        "locators": [{"time_sec": 0.0, "name": "Start"}],
        "tracks": {
            "A": [
                {
                    "file_path": "/songs/test/A/loop_a1.wav",
                    "start_time_sec": 0.0,
                    "length_sec": 8.0,
                    "warping": 1,
                }
            ],
            "B": [],
            "C": [],
            "D": [],
        },
    }
    snaps = resolve_scenes(arrangement, manifest)
    assert snaps[0].a_clip is not None
    assert snaps[0].a_clip.file_path == "/songs/test/A/loop_a1.wav"


def test_missing_file_in_session_tracks_raises_clear_error(manifest):
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 8.0,
        "locators": [{"time_sec": 0.0, "name": "Bad"}],
        "tracks": {
            "A": [
                {
                    "file_path": "/songs/test/A/MISSING.wav",
                    "start_time_sec": 0.0,
                    "length_sec": 4.0,
                    "warping": 1,
                }
            ],
            "B": [],
            "C": [],
            "D": [],
        },
    }
    with pytest.raises(ManifestLookupError) as exc_info:
        resolve_scenes(arrangement, manifest)
    msg = str(exc_info.value)
    assert "/songs/test/A/MISSING.wav" in msg
    assert "session_tracks" in msg
    assert "[A]" in msg


def test_no_locators_raises(manifest):
    arrangement = {
        "tempo": 120.0,
        "time_sig": [4, 4],
        "arrangement_length_sec": 8.0,
        "locators": [],
        "tracks": {"A": [], "B": [], "C": [], "D": []},
    }
    with pytest.raises(ValueError, match="locator"):
        resolve_scenes(arrangement, manifest)


def test_lookup_pad_uses_slot_plus_one(manifest):
    """pad = slot + 1 per spec. Session_tracks slots are 0-indexed; pads
    are 1-indexed."""
    pad = lookup_pad(manifest, "A", "/songs/test/A/loop_a1.wav")
    assert pad == 1  # slot 0 → pad 1
    pad = lookup_pad(manifest, "A", "/songs/test/A/loop_a3.wav")
    assert pad == 3  # slot 2 → pad 3


def test_lookup_pad_rejects_missing_file(manifest):
    with pytest.raises(ManifestLookupError):
        lookup_pad(manifest, "A", "/nope.wav")


def test_arrangement_clip_from_dict_round_trip():
    clip = ArrangementClip.from_dict(
        {"file_path": "/x.wav", "start_time_sec": 1.5, "length_sec": 4.0, "warping": 1}
    )
    assert clip.end_time_sec == pytest.approx(5.5)


def test_snapshot_clip_for_returns_correct_group(arrangement, manifest):
    snaps = resolve_scenes(arrangement, manifest)
    snap: Snapshot = snaps[1]
    assert snap.clip_for("A") is snap.a_clip
    assert snap.clip_for("B") is snap.b_clip
    assert snap.clip_for("C") is snap.c_clip
    assert snap.clip_for("D") is snap.d_clip
    assert snap.clip_for("a") is snap.a_clip  # case-insensitive


def test_session_tracks_accepts_either_file_or_file_path_key(arrangement):
    """Manifest entries may use 'file' (canonical loader output) or
    'file_path' (spec naming) — the resolver accepts both."""
    manifest_with_file_path_key = {
        "session_tracks": {
            "A": [{"slot": 0, "file_path": "/x.wav"}],
            "B": [],
            "C": [],
            "D": [],
        }
    }
    assert lookup_pad(manifest_with_file_path_key, "A", "/x.wav") == 1
