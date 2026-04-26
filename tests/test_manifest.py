"""Tests for the StemForge sample-manifest schema + lookup helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ep133.manifest import (
    BATCH_FILENAME,
    HASH_LENGTH,
    BatchManifest,
    SampleMeta,
    compute_audio_hash,
    find_batch,
    find_sidecar,
    load_batch,
    load_sidecar,
    lookup_in_batch,
    merge_batch_default_bpm,
    resolve_meta,
    sidecar_path_for,
)


@pytest.fixture
def wav_bytes() -> bytes:
    """Tiny 'fake WAV' payload — content is what matters, not WAV-validity."""
    return b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\xab" * 256


@pytest.fixture
def wav_path(tmp_path: Path, wav_bytes: bytes) -> Path:
    p = tmp_path / "kick.wav"
    p.write_bytes(wav_bytes)
    return p


@pytest.fixture
def expected_hash(wav_bytes: bytes) -> str:
    return hashlib.sha256(wav_bytes).hexdigest()[:HASH_LENGTH]


# ---------------------------------------------------------------------------
# Hash + filename helpers
# ---------------------------------------------------------------------------


def test_compute_audio_hash_is_first_16_hex_of_sha256(wav_path, expected_hash):
    assert compute_audio_hash(wav_path) == expected_hash
    assert len(expected_hash) == 16
    assert all(c in "0123456789abcdef" for c in expected_hash)


def test_sidecar_path_for_uses_correct_naming(wav_path, expected_hash):
    p = sidecar_path_for(wav_path)
    assert p.parent == wav_path.parent
    assert p.name == f".manifest_{expected_hash}.json"


def test_sidecar_path_for_accepts_precomputed_hash(wav_path, tmp_path):
    p = sidecar_path_for(wav_path, audio_hash="deadbeefdeadbeef")
    assert p == tmp_path / ".manifest_deadbeefdeadbeef.json"


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


def test_sample_meta_all_optional():
    SampleMeta()  # nothing required


def test_sample_meta_extra_fields_ignored():
    s = SampleMeta.model_validate({"bpm": 120.0, "future_field": "x"})
    assert s.bpm == 120.0


def test_sample_meta_validates_literals():
    with pytest.raises(Exception):
        SampleMeta(playmode="bogus")  # type: ignore[arg-type]
    with pytest.raises(Exception):
        SampleMeta(suggested_pad="Z")  # type: ignore[arg-type]


def test_batch_manifest_default_version():
    b = BatchManifest()
    assert b.version == 1
    assert b.samples == []


# ---------------------------------------------------------------------------
# find_sidecar / find_batch
# ---------------------------------------------------------------------------


def test_find_sidecar_returns_none_when_missing(wav_path):
    assert find_sidecar(wav_path) is None


def test_find_sidecar_returns_path_when_present(wav_path, expected_hash):
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(SampleMeta(bpm=120.0).model_dump_json())
    assert find_sidecar(wav_path) == side


def test_find_batch_returns_none_when_missing(wav_path):
    assert find_batch(wav_path) is None


def test_find_batch_returns_path_when_present(wav_path):
    batch_path = wav_path.parent / BATCH_FILENAME
    batch_path.write_text(BatchManifest().model_dump_json())
    assert find_batch(wav_path) == batch_path


# ---------------------------------------------------------------------------
# load_sidecar
# ---------------------------------------------------------------------------


def test_load_sidecar_parses_meta(wav_path, expected_hash):
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(json.dumps({"bpm": 107.6, "playmode": "oneshot", "name": "kick"}))
    meta = load_sidecar(wav_path)
    assert meta is not None
    assert meta.bpm == 107.6
    assert meta.playmode == "oneshot"
    assert meta.name == "kick"


def test_load_sidecar_returns_none_when_missing(wav_path):
    assert load_sidecar(wav_path) is None


# ---------------------------------------------------------------------------
# lookup_in_batch
# ---------------------------------------------------------------------------


def test_lookup_in_batch_by_hash_wins_over_filename(wav_path, expected_hash):
    batch = BatchManifest(samples=[
        SampleMeta(file="renamed.wav", audio_hash=expected_hash, bpm=99.0),
        SampleMeta(file=wav_path.name, bpm=200.0),
    ])
    found = lookup_in_batch(batch, wav_path)
    assert found is not None
    assert found.bpm == 99.0


def test_lookup_in_batch_filename_fallback(wav_path):
    batch = BatchManifest(samples=[
        SampleMeta(file="other.wav", bpm=99.0),
        SampleMeta(file=wav_path.name, bpm=200.0),
    ])
    found = lookup_in_batch(batch, wav_path)
    assert found is not None
    assert found.bpm == 200.0


def test_lookup_in_batch_no_match_returns_none(wav_path):
    batch = BatchManifest(samples=[SampleMeta(file="other.wav")])
    assert lookup_in_batch(batch, wav_path) is None


def test_lookup_in_batch_empty(wav_path):
    assert lookup_in_batch(BatchManifest(), wav_path) is None


# ---------------------------------------------------------------------------
# resolve_meta — the main lookup chain
# ---------------------------------------------------------------------------


def test_resolve_meta_prefers_sidecar_over_batch(wav_path, expected_hash):
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(json.dumps({"bpm": 107.6, "name": "from-sidecar"}))

    batch_path = wav_path.parent / BATCH_FILENAME
    batch_path.write_text(json.dumps({
        "samples": [{"file": wav_path.name, "bpm": 200.0, "name": "from-batch"}]
    }))

    meta = resolve_meta(wav_path)
    assert meta is not None
    assert meta.name == "from-sidecar"


def test_resolve_meta_falls_through_to_batch(wav_path):
    batch_path = wav_path.parent / BATCH_FILENAME
    batch_path.write_text(json.dumps({
        "samples": [{"file": wav_path.name, "bpm": 200.0, "playmode": "key"}]
    }))
    meta = resolve_meta(wav_path)
    assert meta is not None
    assert meta.bpm == 200.0
    assert meta.playmode == "key"


def test_resolve_meta_returns_none_when_no_manifest(wav_path):
    assert resolve_meta(wav_path) is None


def test_resolve_meta_explicit_override_sidecar_shape(wav_path, tmp_path):
    override = tmp_path / "explicit.json"
    override.write_text(json.dumps({"bpm": 60.0, "name": "explicit"}))
    meta = resolve_meta(wav_path, manifest_override=override)
    assert meta is not None
    assert meta.bpm == 60.0
    assert meta.name == "explicit"


def test_resolve_meta_explicit_override_batch_shape(wav_path, tmp_path):
    override = tmp_path / "explicit_batch.json"
    override.write_text(json.dumps({
        "version": 1,
        "samples": [{"file": wav_path.name, "bpm": 75.0}],
    }))
    meta = resolve_meta(wav_path, manifest_override=override)
    assert meta is not None
    assert meta.bpm == 75.0


def test_resolve_meta_explicit_override_skips_auto_detect(wav_path, tmp_path, expected_hash):
    # A real sidecar exists next to the wav...
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(json.dumps({"bpm": 107.6}))
    # ...but explicit override should win.
    override = tmp_path / "explicit.json"
    override.write_text(json.dumps({"bpm": 999.0}))
    meta = resolve_meta(wav_path, manifest_override=override)
    assert meta is not None
    assert meta.bpm == 999.0


def test_resolve_meta_use_sidecar_false_skips_sidecar(wav_path, expected_hash):
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(json.dumps({"bpm": 107.6}))
    batch_path = wav_path.parent / BATCH_FILENAME
    batch_path.write_text(json.dumps({
        "samples": [{"file": wav_path.name, "bpm": 200.0}]
    }))
    meta = resolve_meta(wav_path, use_sidecar=False)
    assert meta is not None
    assert meta.bpm == 200.0  # batch wins because sidecar is skipped


def test_resolve_meta_both_disabled_returns_none(wav_path, expected_hash):
    side = wav_path.parent / f".manifest_{expected_hash}.json"
    side.write_text(json.dumps({"bpm": 107.6}))
    assert resolve_meta(wav_path, use_sidecar=False, use_batch=False) is None


# ---------------------------------------------------------------------------
# merge_batch_default_bpm
# ---------------------------------------------------------------------------


def test_merge_batch_default_bpm_fills_when_unset():
    meta = SampleMeta(name="x")
    batch = BatchManifest(bpm=110.0)
    merged = merge_batch_default_bpm(meta, batch)
    assert merged.bpm == 110.0
    assert meta.bpm is None  # original untouched


def test_merge_batch_default_bpm_does_not_overwrite():
    meta = SampleMeta(bpm=120.0)
    batch = BatchManifest(bpm=110.0)
    merged = merge_batch_default_bpm(meta, batch)
    assert merged.bpm == 120.0


# ---------------------------------------------------------------------------
# load_batch
# ---------------------------------------------------------------------------


def test_load_batch_round_trip(tmp_path):
    p = tmp_path / "batch.json"
    p.write_text(json.dumps({
        "version": 1,
        "track": "demo",
        "bpm": 120.0,
        "samples": [
            {"file": "a.wav", "stem": "drums", "bpm": 120.0},
            {"file": "b.wav", "stem": "bass"},
        ],
    }))
    batch = load_batch(p)
    assert batch.track == "demo"
    assert len(batch.samples) == 2
    assert batch.samples[0].stem == "drums"
