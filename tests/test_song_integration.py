"""End-to-end integration tests for EP-133 song-mode export.

Exercises the full pipeline:

    arrangement.json + manifest.json + reference.ppak
        → resolve_scenes() → synthesize() → build_ppak()
        → bytes
        → re-parse via in-Python ZIP/TAR walker
        → assert layout matches expectations

Inputs:

  * ``tests/fixtures/sample_arrangement.json``
  * ``tests/fixtures/sample_manifest.json``
  * ``tests/fixtures/captures/reference_minimal.ppak`` (always shipped)

The test is a *hard contract* check on the output ``.ppak``: every byte
we care about (pattern bytes, scene bytes, pad slots) is verified
against the same parse routines the EP-133's own firmware exercises
(per phones24's read reference).
"""

from __future__ import annotations

import io
import json
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from ep133.ppak.song_writer import build_ppak
from ep133.song.resolver import resolve_scenes
from ep133.song.synthesizer import synthesize


# ---------------------------------------------------------------------------
# In-Python parsers — standalone re-implementations of the format readers,
# used to verify the writer's output round-trips cleanly. These decode
# just enough to assert the contract.
# ---------------------------------------------------------------------------


def _zip_entries(ppak_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(ppak_bytes)) as zf:
        return {info.filename: zf.read(info.filename) for info in zf.infolist()}


def _tar_entries(tar_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                if f is not None:
                    out[member.name] = f.read()
    return out


def _parse_pattern(buf: bytes) -> dict:
    """Decode a pattern file into header + events.

    Format per spec:
        bytes [0,1,2,3] = (0x00, bars, event_count, 0x00)
        events: 8 bytes each — pos u16 LE, pad_indicator u8, note u8,
                 velocity u8, duration u16 LE, padding u8
    """
    if len(buf) < 4:
        raise ValueError(f"pattern too short: {len(buf)} bytes")
    bars = buf[1]
    n_events = buf[2]
    events = []
    for i in range(n_events):
        off = 4 + i * 8
        if off + 8 > len(buf):
            raise ValueError(f"truncated event {i} in pattern of {len(buf)} bytes")
        pos = struct.unpack_from("<H", buf, off)[0]
        pad_ind = buf[off + 2]
        note = buf[off + 3]
        vel = buf[off + 4]
        dur = struct.unpack_from("<H", buf, off + 5)[0]
        events.append(
            {
                "position_ticks": pos,
                "pad": (pad_ind // 8) + 1,
                "note": note,
                "velocity": vel,
                "duration_ticks": dur,
            }
        )
    return {"bars": bars, "events": events}


def _parse_scenes(buf: bytes) -> list[dict]:
    """Decode the scenes file. Layout:
        bytes 0..6 = header
        bytes 7..600 = 99 × 6-byte scene slots: [a, b, c, d, num, denom]
        bytes 601..  = 111-byte trailer (NOT scenes)
    """
    if len(buf) < 7:
        raise ValueError(f"scenes file too short: {len(buf)} bytes")
    chunks = []
    for i in range(99):
        pos = 7 + i * 6
        if pos + 6 > len(buf):
            break
        chunks.append(
            {"a": buf[pos], "b": buf[pos + 1], "c": buf[pos + 2], "d": buf[pos + 3]}
        )
    while chunks and chunks[-1] == {"a": 0, "b": 0, "c": 0, "d": 0}:
        chunks.pop()
    return chunks


def _parse_song_positions(buf: bytes) -> list[int]:
    """Trailer bytes 11.. hold position count + scene refs."""
    trailer_off = 7 + 99 * 6
    count = buf[trailer_off + 11]
    return [buf[trailer_off + 12 + i] for i in range(count)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def materialized_fixtures(tmp_path_factory) -> tuple[dict, dict]:
    """Rewrite fixture paths to point at on-disk stub WAVs.

    build_ppak reads each sound's bytes via Path.read_bytes(), so file_path
    entries must resolve. Fixtures use ``/songs/test/...`` placeholder paths;
    mirror them under tmp_path and rewrite both arrangement and manifest.
    """
    fixtures = Path(__file__).parent / "fixtures"
    arrangement_raw = json.loads((fixtures / "sample_arrangement.json").read_text())
    manifest_raw = json.loads((fixtures / "sample_manifest.json").read_text())

    base = tmp_path_factory.mktemp("song_export_int")
    path_map: dict[str, str] = {}

    for group, entries in (manifest_raw.get("session_tracks") or {}).items():
        gdir = base / "songs" / group
        gdir.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            old = entry.get("file_path") or entry.get("file")
            if old is None:
                continue
            new = gdir / Path(old).name
            new.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            path_map[old] = str(new)
            entry["file"] = str(new)

    for group_clips in arrangement_raw.get("tracks", {}).values():
        for clip in group_clips:
            old = clip.get("file_path")
            if old in path_map:
                clip["file_path"] = path_map[old]

    return arrangement_raw, manifest_raw


@pytest.fixture(scope="module")
def arrangement_materialized(materialized_fixtures) -> dict:
    return materialized_fixtures[0]


@pytest.fixture(scope="module")
def manifest_materialized(materialized_fixtures) -> dict:
    return materialized_fixtures[1]


@pytest.fixture(scope="module")
def reference_ppak() -> Path:
    """Module-scope variant of the session-scope reference_minimal_ppak
    fixture so module-scope fixtures below can depend on it."""
    path = Path(__file__).parent / "fixtures" / "captures" / "reference_minimal.ppak"
    if not path.is_file():
        pytest.fail(f"reference_minimal.ppak missing at {path}")
    return path


@pytest.fixture(scope="module")
def built_ppak_bytes(arrangement_materialized, manifest_materialized, reference_ppak) -> bytes:
    """Run the full song-export pipeline once per module."""
    snapshots = resolve_scenes(arrangement_materialized, manifest_materialized)
    spec = synthesize(
        snapshots,
        manifest_materialized,
        project_bpm=arrangement_materialized["tempo"],
        time_sig=tuple(arrangement_materialized["time_sig"]),
        project_slot=1,
    )
    return build_ppak(spec, reference_ppak)


@pytest.fixture(scope="module")
def project_tar_bytes(built_ppak_bytes) -> bytes:
    entries = _zip_entries(built_ppak_bytes)
    tar_entries = [
        name for name in entries if name.startswith("/projects/") and name.endswith(".tar")
    ]
    assert tar_entries, f"no /projects/PXX.tar in zip; entries={list(entries)}"
    assert len(tar_entries) == 1, f"expected one project tar, got {tar_entries}"
    return entries[tar_entries[0]]


@pytest.fixture(scope="module")
def tar_files(project_tar_bytes) -> dict[str, bytes]:
    return _tar_entries(project_tar_bytes)


# ---------------------------------------------------------------------------
# Tests — container layer
# ---------------------------------------------------------------------------


def test_ppak_is_valid_zip(built_ppak_bytes):
    assert zipfile.is_zipfile(io.BytesIO(built_ppak_bytes))


def test_ppak_entries_have_leading_slash(built_ppak_bytes):
    """Every entry starts with ``/`` — required or device shows
    'PAK FILE IS EMPTY'."""
    entries = _zip_entries(built_ppak_bytes)
    assert entries, "built .ppak has no entries"
    bad = [name for name in entries if not name.startswith("/")]
    assert not bad, f"entries missing leading slash: {bad}"


def test_ppak_contains_project_tar(built_ppak_bytes):
    entries = _zip_entries(built_ppak_bytes)
    project_paths = [
        name for name in entries if name.startswith("/projects/P") and name.endswith(".tar")
    ]
    assert project_paths


def test_ppak_meta_json_well_formed(built_ppak_bytes):
    entries = _zip_entries(built_ppak_bytes)
    assert "/meta.json" in entries
    meta = json.loads(entries["/meta.json"].decode("utf-8"))
    for key in (
        "info",
        "pak_version",
        "pak_type",
        "device_name",
        "device_sku",
        "device_version",
        "generated_at",
        "author",
        "base_sku",
    ):
        assert key in meta, f"meta.json missing required key: {key!r}"
    assert meta["info"] == "teenage engineering - pak file"
    assert meta["pak_version"] == 1
    assert meta["device_name"] == "EP-133"
    assert meta["device_sku"] == meta["base_sku"]


# ---------------------------------------------------------------------------
# Tests — TAR layer
# ---------------------------------------------------------------------------


def test_tar_has_pad_files_for_assigned_pads_only(tar_files):
    """Only assigned pads emit pad files; each is 26 bytes."""
    pad_files = {
        k: v
        for k, v in tar_files.items()
        if k.startswith("pads/") and k.count("/") == 2 and k.split("/")[-1].startswith("p")
    }
    assert len(pad_files) > 0, "expected at least one pad file in TAR"
    for name, blob in pad_files.items():
        assert len(blob) == 26, f"{name} is {len(blob)} bytes, expected 26"


def test_tar_omits_settings_file(tar_files):
    """Per PROTOCOL.md §8, populating settings has caused ERR 82."""
    assert "settings" not in tar_files


def test_tar_has_scenes(tar_files):
    assert "scenes" in tar_files
    assert len(tar_files["scenes"]) == 712


def test_tar_has_pattern_files(tar_files, arrangement_materialized):
    """At least one pattern per group that has clips in the arrangement.
    Format is ``patterns/{group}{NN}`` (no slash between group and number)."""
    expected_groups = set()
    for grp_name, clips in arrangement_materialized["tracks"].items():
        if clips:
            expected_groups.add(grp_name.lower())
    if not expected_groups:
        pytest.skip("arrangement has no clips on any track; nothing to assert")

    pattern_groups_present = set()
    for name in tar_files:
        if not name.startswith("patterns/"):
            continue
        basename = name[len("patterns/") :]
        if basename and basename[0].isalpha():
            pattern_groups_present.add(basename[0].lower())
    missing = expected_groups - pattern_groups_present
    assert not missing, (
        f"expected pattern dirs for groups {expected_groups}, missing {missing}; "
        f"present={pattern_groups_present}"
    )


# ---------------------------------------------------------------------------
# Tests — content layer
# ---------------------------------------------------------------------------


def test_scenes_count_matches_locator_count(tar_files, arrangement_materialized):
    """Populated scene chunks should match locator count. Scenes file is
    fixed-size 712 bytes; unused slots are zero-filled."""
    scenes = _parse_scenes(tar_files["scenes"])
    populated = [s for s in scenes if s["a"] != 0 or s["b"] != 0 or s["c"] != 0 or s["d"] != 0]
    expected = len(arrangement_materialized["locators"])
    assert len(populated) == expected, (
        f"got {len(populated)} populated scenes, expected {expected}"
    )


def test_patterns_decode_with_well_formed_events(tar_files):
    """Every pattern decodes; every event has pad ∈ 1..12 and note 0..127."""
    pattern_names = [n for n in tar_files if n.startswith("patterns/")]
    assert pattern_names
    for name in pattern_names:
        decoded = _parse_pattern(tar_files[name])
        assert decoded["bars"] >= 1, f"{name}: bars={decoded['bars']}"
        for ev in decoded["events"]:
            assert 1 <= ev["pad"] <= 12, f"{name}: pad {ev['pad']} out of 1..12"
            assert 0 <= ev["note"] <= 127, f"{name}: note {ev['note']}"
            assert 0 <= ev["velocity"] <= 127, f"{name}: vel {ev['velocity']}"


def test_pad_records_reference_sample_slots(tar_files):
    """Every emitted pad references a non-zero sample slot. Bytes 1..2 =
    sample slot uint16 LE. Unassigned pads are omitted from the TAR."""
    pad_files = {
        k: v
        for k, v in tar_files.items()
        if k.startswith("pads/") and k.count("/") == 2 and k.split("/")[-1].startswith("p")
    }
    assert len(pad_files) > 0
    for name, buf in pad_files.items():
        slot = struct.unpack_from("<H", buf, 1)[0]
        assert slot != 0, f"{name} has slot=0; unassigned pads should be omitted"


def test_song_positions_default_to_scene_order(tar_files, arrangement_materialized):
    """Default song-mode positions: play scenes 1..N in order."""
    positions = _parse_song_positions(tar_files["scenes"])
    expected = list(range(1, len(arrangement_materialized["locators"]) + 1))
    assert positions == expected


def test_pattern_bars_match_scene_lengths_from_locator_gaps(
    tar_files, arrangement_materialized
):
    """Pattern bars come from locator-gap-derived scene lengths. Fixture
    has locators at 0/8/16s @ 120 BPM = 4 bars apart, so every populated
    pattern should be bars=4."""
    pattern_names = [n for n in tar_files if n.startswith("patterns/")]
    populated = []
    for name in pattern_names:
        decoded = _parse_pattern(tar_files[name])
        if decoded["events"]:  # skip empty markers
            populated.append((name, decoded["bars"]))
    assert populated
    for name, bars in populated:
        assert bars == 4, f"{name}: bars={bars}, expected 4"


def test_built_pads_use_factory_native_size(tar_files):
    """Every emitted pad is 26 bytes (factory native, not Sample Tool's 27)."""
    pad_files = [
        v
        for k, v in tar_files.items()
        if k.startswith("pads/") and k.count("/") == 2 and k.split("/")[-1].startswith("p")
    ]
    assert pad_files
    for blob in pad_files:
        assert len(blob) == 26


def test_sounds_bundled_with_correct_naming(built_ppak_bytes):
    """Sounds entries use ``/sounds/{slot:03d} {slot:03d}_{name}.wav``."""
    entries = _zip_entries(built_ppak_bytes)
    sounds_entries = [n for n in entries if n.lstrip("/").startswith("sounds/")]
    assert sounds_entries, "no sounds entries in built ppak"
    # Each entry should match the {slot:03d} {slot:03d}_NAME.wav pattern.
    import re

    pattern = re.compile(r"^/sounds/(\d{3}) \1_.+\.wav$")
    for entry in sounds_entries:
        assert pattern.match(entry), f"entry {entry!r} doesn't match expected naming"
