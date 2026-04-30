"""Tests for ``ep133.ppak.song_writer.build_ppak`` and
``build_synthetic_template_ppak``.

These verify the container-layer guarantees:

- ZIP entries have leading slashes (else device shows "PAK FILE IS EMPTY")
- meta.json is well-formed
- inner TAR has the expected file layout (factory P06 minimal: only
  assigned pads, no settings file, scenes always present, patterns named
  ``patterns/{group}{NN}`` with no slash)
- pad records are 26 bytes (factory native, not Sample Tool's 27)
- sounds entries follow the ``/sounds/{slot:03d} {slot:03d}_{name}.wav``
  naming pattern

We use ``build_synthetic_template_ppak`` for most tests so the suite runs
without external fixtures; the captured ``reference_minimal.ppak`` is
exercised in a separate "golden" suite at the bottom.
"""

from __future__ import annotations

import io
import json
import re
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from ep133.ppak.song_writer import (
    META_DEFAULTS,
    build_ppak,
    build_synthetic_template_ppak,
)
from ep133.song.format import (
    PAD_RECORD_SIZE,
    SETTINGS_SIZE,
    Event,
    PadSpec,
    Pattern,
    PpakSpec,
    SceneSpec,
)


# ---- helpers ---------------------------------------------------------------


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


def _project_tar_from(ppak_bytes: bytes) -> bytes:
    entries = _zip_entries(ppak_bytes)
    tar_paths = [n for n in entries if n.lstrip("/").startswith("projects/")]
    assert tar_paths, "no project tar in zip"
    return entries[tar_paths[0]]


@pytest.fixture
def synthetic_template(tmp_path) -> Path:
    """Build a minimal synthetic template at tmp_path/template.ppak."""
    return build_synthetic_template_ppak(tmp_path / "template.ppak")


@pytest.fixture
def make_wav(tmp_path):
    """Factory: write a stub WAV to ``tmp_path/<name>`` and return the Path.

    build_ppak only checks ``.is_file()`` and reads bytes; any file works
    for tests that don't need real audio decoding (those tests catch
    wave.Error and pass the bytes through)."""

    def _make(name: str, content: bytes = b"RIFF\x00\x00\x00\x00WAVE") -> Path:
        p = tmp_path / name
        p.write_bytes(content)
        return p

    return _make


def _minimal_spec(*, sounds: dict[int, Path], project_slot: int = 1) -> PpakSpec:
    """Build a 1-pattern, 1-scene, 1-pad spec on group A.

    Note: ``song_positions`` is left None by default. Setting it triggers
    the writer to emit a ``patterns/d05`` song-mode marker pattern — fine
    for song-mode tests, but other tests (e.g. ones asserting the
    pattern-file list) shouldn't have to know about it. Override per-test
    when song-mode behavior matters.
    """
    return PpakSpec(
        project_slot=project_slot,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[
            Pattern(
                group="a",
                index=1,
                bars=1,
                events=[Event(0, 1, 60, 100, 96)],
            )
        ],
        scenes=[SceneSpec(a=1, b=0, c=0, d=0)],
        pads=[
            PadSpec(
                group="a",
                pad=1,
                sample_slot=next(iter(sounds.keys())),
                play_mode="oneshot",
                time_stretch_bars=1,
                stretch_mode="bpm",
                sound_bpm=120.0,
            )
        ],
        sounds=sounds,
    )


# ---- build_synthetic_template_ppak ----------------------------------------


def test_synthetic_template_produces_valid_zip(synthetic_template):
    assert synthetic_template.exists()
    assert zipfile.is_zipfile(synthetic_template)


def test_synthetic_template_entries_have_leading_slash(synthetic_template):
    """The big gotcha — device shows 'PAK FILE IS EMPTY' without leading /."""
    entries = _zip_entries(synthetic_template.read_bytes())
    assert entries
    bad = [n for n in entries if not n.startswith("/")]
    assert not bad, f"entries missing leading slash: {bad}"


def test_synthetic_template_has_meta_and_project_tar(synthetic_template):
    entries = _zip_entries(synthetic_template.read_bytes())
    assert "/meta.json" in entries
    assert any(re.fullmatch(r"/projects/P\d{2}\.tar", n) for n in entries)


def test_synthetic_template_inner_tar_has_48_pad_files(synthetic_template):
    """Synthetic template ships factory-default pads for all 48 (4×12)
    slots; build_ppak then drops the unassigned ones."""
    tar = _tar_entries(_project_tar_from(synthetic_template.read_bytes()))
    pad_files = [k for k in tar if k.startswith("pads/") and k.count("/") == 2]
    assert len(pad_files) == 48


def test_synthetic_template_pad_files_are_26_bytes(synthetic_template):
    """Factory native is 26 bytes."""
    tar = _tar_entries(_project_tar_from(synthetic_template.read_bytes()))
    for k, blob in tar.items():
        if k.startswith("pads/") and k.count("/") == 2:
            assert len(blob) == 26, f"{k} is {len(blob)} bytes, expected 26"


def test_synthetic_template_has_222_byte_settings(synthetic_template):
    tar = _tar_entries(_project_tar_from(synthetic_template.read_bytes()))
    assert "settings" in tar
    assert len(tar["settings"]) == SETTINGS_SIZE


def test_synthetic_template_has_no_patterns_scenes_or_sounds(synthetic_template):
    """The synthetic template is a *template* — patterns/scenes/sounds are
    authored fresh by build_ppak, not preserved from a template."""
    entries = _zip_entries(synthetic_template.read_bytes())
    sounds_entries = [n for n in entries if n.lstrip("/").startswith("sounds/")]
    assert not sounds_entries
    tar = _tar_entries(_project_tar_from(synthetic_template.read_bytes()))
    pattern_entries = [k for k in tar if k.startswith("patterns/")]
    assert not pattern_entries
    assert "scenes" not in tar


def test_synthetic_template_project_slot_is_configurable(tmp_path):
    out = build_synthetic_template_ppak(tmp_path / "p07.ppak", project_slot=7)
    entries = _zip_entries(out.read_bytes())
    assert "/projects/P07.tar" in entries


# ---- build_ppak: container layer ------------------------------------------


def test_build_ppak_returns_valid_zip(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    assert blob.startswith(b"PK\x03\x04")
    assert zipfile.is_zipfile(io.BytesIO(blob))


def test_build_ppak_writes_to_out_path(synthetic_template, make_wav, tmp_path):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    out = tmp_path / "song.ppak"
    blob = build_ppak(spec, synthetic_template, out_path=out)
    assert out.exists()
    assert out.read_bytes() == blob


def test_build_ppak_entries_have_leading_slash(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    entries = _zip_entries(blob)
    bad = [n for n in entries if not n.startswith("/")]
    assert not bad, f"entries missing leading slash: {bad}"


def test_build_ppak_meta_json_required_keys(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    meta = json.loads(_zip_entries(blob)["/meta.json"].decode("utf-8"))
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
    assert meta["info"] == META_DEFAULTS["info"]
    assert meta["pak_version"] == META_DEFAULTS["pak_version"]
    assert meta["device_name"] == META_DEFAULTS["device_name"]
    assert meta["device_sku"] == meta["base_sku"]


def test_build_ppak_meta_json_author_override(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template, author="alice")
    meta = json.loads(_zip_entries(blob)["/meta.json"].decode("utf-8"))
    assert meta["author"] == "alice"


def test_build_ppak_meta_json_device_sku_override(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template, device_sku="TE032ABC123")
    meta = json.loads(_zip_entries(blob)["/meta.json"].decode("utf-8"))
    assert meta["device_sku"] == "TE032ABC123"
    assert meta["base_sku"] == "TE032ABC123"


def test_build_ppak_project_tar_matches_project_slot(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds, project_slot=5)
    blob = build_ppak(spec, synthetic_template)
    entries = _zip_entries(blob)
    assert "/projects/P05.tar" in entries


# ---- build_ppak: TAR layer ------------------------------------------------


def test_built_pad_files_are_26_bytes(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    pad_files = [(k, v) for k, v in tar.items() if k.startswith("pads/") and k.count("/") == 2]
    assert pad_files, "no pad files in tar"
    for name, data in pad_files:
        assert len(data) == PAD_RECORD_SIZE == 26, f"{name} is {len(data)} bytes"


def test_built_tar_only_emits_assigned_pads(synthetic_template, make_wav):
    """Factory P06 layout: only assigned pads emit pad files. Was bug
    pre-2026-04-27: shipping all 48 default pads bloated the project."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    pad_files = [k for k in tar if k.startswith("pads/") and k.count("/") == 2]
    assert pad_files == ["pads/a/p01"]


def test_built_tar_omits_settings_file(synthetic_template, make_wav):
    """Per PROTOCOL.md §8, populating settings has caused ERR 82
    (wedge-class) on import. The entry must be omitted."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    assert "settings" not in tar


def test_built_tar_has_scenes_file(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    assert "scenes" in tar
    # 7-byte header + 99 × 6-byte slots + 111-byte trailer = 712.
    assert len(tar["scenes"]) == 712


def test_built_pattern_files_use_no_slash_naming(synthetic_template, make_wav):
    """Device requires patterns/{group}{NN} with NO slash between group
    and number. Verified from captured backup; nested-path entries are
    silently ignored — patterns never play."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    pattern_keys = [k for k in tar if k.startswith("patterns/")]
    assert pattern_keys == ["patterns/a01"]


def test_built_pattern_byte_layout_matches_spec(synthetic_template, make_wav):
    """One trigger event in a 1-bar pattern → 4-byte header + 8-byte event.
    Header: 0x00 bars=1 event_count=1 0x00."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    pattern = tar["patterns/a01"]
    assert pattern[0] == 0x00
    assert pattern[1] == 1  # bars
    assert pattern[2] == 1  # event count
    assert pattern[3] == 0x00


def test_built_pad_record_writes_sample_slot_at_byte_1(synthetic_template, make_wav):
    """Bytes 1..2 carry sample_slot as uint16 LE."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    pad_blob = tar["pads/a/p01"]
    assert struct.unpack_from("<H", pad_blob, 1)[0] == 700


# ---- build_ppak: sounds bundling ------------------------------------------


def test_sounds_naming_includes_slot_space_slot_underscore_name(synthetic_template, make_wav):
    """Device requires /sounds/{slot:03d} {slot:03d}_{name}.wav (literal
    space between slot and display name). Verified from real device
    backup. Without the space: sample stays unloaded."""
    wav = make_wav("kick.wav")
    sounds = {700: wav}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, synthetic_template)
    entries = _zip_entries(blob)
    expected = "/sounds/700 700_kick.wav"
    assert expected in entries, (
        f"missing expected sounds entry. Got: {[n for n in entries if 'sounds' in n]}"
    )


def test_missing_wav_soft_skips_with_warning(synthetic_template, tmp_path):
    """Upstream curation can leave gaps; export shouldn't crash. Missing
    slots are filtered out of bundled audio AND pad records."""
    bogus_path = tmp_path / "missing.wav"  # never created
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[],
        scenes=[],
        pads=[
            PadSpec(
                group="a",
                pad=1,
                sample_slot=701,
                play_mode="oneshot",
                time_stretch_bars=1,
            )
        ],
        sounds={701: bogus_path},
    )
    with pytest.warns(UserWarning, match="WAV missing"):
        blob = build_ppak(spec, synthetic_template)
    tar = _tar_entries(_project_tar_from(blob))
    # Pad record dropped (slot 701 was missing).
    pad_files = [k for k in tar if k.startswith("pads/") and k.count("/") == 2]
    assert pad_files == []
    # Sounds entry dropped.
    entries = _zip_entries(blob)
    sounds_entries = [n for n in entries if n.lstrip("/").startswith("sounds/")]
    assert not sounds_entries


# ---- build_ppak: validation ------------------------------------------------


def test_rejects_invalid_project_slot(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    base = _minimal_spec(sounds=sounds)
    bad = PpakSpec(
        project_slot=0,
        bpm=base.bpm,
        time_sig=base.time_sig,
        patterns=base.patterns,
        scenes=base.scenes,
        pads=base.pads,
        sounds=base.sounds,
    )
    with pytest.raises(ValueError, match="project_slot"):
        build_ppak(bad, synthetic_template)


def test_rejects_zero_or_negative_bpm(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    base = _minimal_spec(sounds=sounds)
    bad = PpakSpec(
        project_slot=base.project_slot,
        bpm=0.0,
        time_sig=base.time_sig,
        patterns=base.patterns,
        scenes=base.scenes,
        pads=base.pads,
        sounds=base.sounds,
    )
    with pytest.raises(ValueError, match="bpm"):
        build_ppak(bad, synthetic_template)


def test_rejects_duplicate_patterns(synthetic_template, make_wav):
    """Two patterns with the same (group, index) — invalid."""
    sounds = {700: make_wav("a1.wav")}
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[
            Pattern(group="a", index=1, bars=1, events=[]),
            Pattern(group="a", index=1, bars=2, events=[]),
        ],
        scenes=[],
        pads=[],
        sounds=sounds,
    )
    with pytest.raises(ValueError, match="duplicate pattern"):
        build_ppak(spec, synthetic_template)


def test_rejects_duplicate_pads(synthetic_template, make_wav):
    """Two pad records with the same (group, pad)."""
    sounds = {700: make_wav("a1.wav")}
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[],
        scenes=[],
        pads=[
            PadSpec(
                group="a",
                pad=1,
                sample_slot=700,
                play_mode="oneshot",
                time_stretch_bars=1,
            ),
            PadSpec(
                group="a",
                pad=1,
                sample_slot=701,
                play_mode="oneshot",
                time_stretch_bars=1,
            ),
        ],
        sounds=sounds,
    )
    with pytest.raises(ValueError, match="duplicate pad"):
        build_ppak(spec, synthetic_template)


def test_rejects_scene_referencing_undefined_pattern(synthetic_template, make_wav):
    """Scene chunk referencing a (group, index) that isn't in patterns."""
    sounds = {700: make_wav("a1.wav")}
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[Pattern(group="a", index=1, bars=1, events=[])],
        scenes=[SceneSpec(a=99, b=0, c=0, d=0)],  # 99 isn't defined
        pads=[],
        sounds=sounds,
    )
    with pytest.raises(ValueError, match="undefined pattern"):
        build_ppak(spec, synthetic_template)


def test_rejects_invalid_pattern_group(synthetic_template, make_wav):
    sounds = {700: make_wav("a1.wav")}
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[Pattern(group="e", index=1, bars=1, events=[])],
        scenes=[],
        pads=[],
        sounds=sounds,
    )
    with pytest.raises(ValueError, match="group"):
        build_ppak(spec, synthetic_template)


# ---- BPM-mode integration: sample_bpm consistency check -------------------


def test_conflicting_sound_bpm_per_slot_raises(synthetic_template, make_wav):
    """Two pads referencing the same sample_slot but with different
    sound_bpm values — sound.bpm is a slot-level property, so this is
    inconsistent and the writer should refuse."""
    sounds = {700: make_wav("shared.wav")}
    spec = PpakSpec(
        project_slot=1,
        bpm=120.0,
        time_sig=(4, 4),
        patterns=[],
        scenes=[],
        pads=[
            PadSpec(
                group="a",
                pad=1,
                sample_slot=700,
                play_mode="oneshot",
                time_stretch_bars=1,
                stretch_mode="bpm",
                sound_bpm=120.0,
            ),
            PadSpec(
                group="a",
                pad=2,
                sample_slot=700,
                play_mode="oneshot",
                time_stretch_bars=1,
                stretch_mode="bpm",
                sound_bpm=140.0,
            ),
        ],
        sounds=sounds,
    )
    with pytest.raises(ValueError, match="sound_bpm"):
        build_ppak(spec, synthetic_template)


# ---- Reference-template loading -------------------------------------------


def test_reference_template_path_must_exist(make_wav, tmp_path):
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    with pytest.raises(FileNotFoundError):
        build_ppak(spec, tmp_path / "does_not_exist.ppak")


def test_reference_minimal_ppak_loads_cleanly(reference_minimal_ppak, make_wav):
    """The shipped 177KB capture is a valid reference template — verifies
    our loader handles real-device output."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    blob = build_ppak(spec, reference_minimal_ppak)
    assert blob.startswith(b"PK\x03\x04")


def test_reference_minimal_ppak_pad_templates_get_truncated_to_26_bytes(
    reference_minimal_ppak, make_wav
):
    """Sample Tool emits 27-byte pad records; we must truncate to 26 on read.
    Verifies the loader's truncation (else build_pad raises 'pad template
    must be 26 bytes')."""
    sounds = {700: make_wav("a1.wav")}
    spec = _minimal_spec(sounds=sounds)
    # If the truncation isn't happening, this raises ValueError from build_pad.
    blob = build_ppak(spec, reference_minimal_ppak)
    tar = _tar_entries(_project_tar_from(blob))
    pad_files = [(k, v) for k, v in tar.items() if k.startswith("pads/") and k.count("/") == 2]
    for name, data in pad_files:
        assert len(data) == 26, f"{name}: {len(data)} bytes"
