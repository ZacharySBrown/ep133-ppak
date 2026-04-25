"""Tests for SampleParams — sample-slot metadata for EP-133 SysEx writes.

Schema verified 2026-04-24 from paginated FILE_METADATA_GET on slot 100.
Unlike PadParams (full-snapshot write), SampleParams is partial: only emits
set fields; unlisted fields retain their current device-side value.
"""
import json

import pytest

from ep133 import payloads as P


# ── to_json: partial emission ─────────────────────────────────────────

def test_sample_params_empty_raises_on_build():
    """An empty SampleParams can't be used for a write (nothing to send)."""
    with pytest.raises(ValueError, match="empty"):
        P.build_slot_metadata_set(slot=100, params=P.SampleParams())


def test_sample_params_emits_only_set_fields():
    """to_json should only include fields the caller set."""
    j = json.loads(P.SampleParams(bpm=120.0).to_json())
    assert j == {"sound.bpm": 120.0}

    j = json.loads(P.SampleParams(bpm=92.0, bars=2.0).to_json())
    assert j == {"sound.bpm": 92.0, "sound.bars": 2.0}

    j = json.loads(P.SampleParams(playmode="key", time_mode="bpm", rootnote=64).to_json())
    assert j == {"sound.playmode": "key", "sound.rootnote": 64, "time.mode": "bpm"}


def test_sample_params_all_fields():
    """Exhaustive — every field gets the correct JSON key."""
    params = P.SampleParams(
        bpm=120.0, bars=4.0, playmode="legato", time_mode="bar",
        rootnote=60, amplitude=100, pan=-8, pitch=2.5,
        loopstart=0, loopend=44100, attack=10, release=200,
        name="test_sample",
    )
    j = json.loads(params.to_json())
    assert j["sound.bpm"] == 120.0
    assert j["sound.bars"] == 4.0
    assert j["sound.playmode"] == "legato"
    assert j["time.mode"] == "bar"
    assert j["sound.rootnote"] == 60
    assert j["sound.amplitude"] == 100
    assert j["sound.pan"] == -8
    assert j["sound.pitch"] == 2.5
    assert j["sound.loopstart"] == 0
    assert j["sound.loopend"] == 44100
    assert j["envelope.attack"] == 10
    assert j["envelope.release"] == 200
    assert j["name"] == "test_sample"


# ── validation ────────────────────────────────────────────────────────

def test_sample_params_validation():
    """Out-of-range values are rejected client-side before hitting the device."""
    with pytest.raises(ValueError, match="playmode"):
        P.SampleParams(playmode="bogus")
    with pytest.raises(ValueError, match="time_mode"):
        P.SampleParams(time_mode="bogus")
    with pytest.raises(ValueError, match="rootnote"):
        P.SampleParams(rootnote=128)
    with pytest.raises(ValueError, match="amplitude"):
        P.SampleParams(amplitude=101)
    with pytest.raises(ValueError, match="pan"):
        P.SampleParams(pan=17)
    with pytest.raises(ValueError, match="attack"):
        P.SampleParams(attack=256)
    with pytest.raises(ValueError, match="release"):
        P.SampleParams(release=-1)
    with pytest.raises(ValueError, match="loopstart"):
        P.SampleParams(loopstart=-2)
    with pytest.raises(ValueError, match="loopend"):
        P.SampleParams(loopend=-2)
    with pytest.raises(ValueError, match="name"):
        P.SampleParams(name="x" * 21)  # over 20 bytes
    with pytest.raises(ValueError, match="bpm"):
        P.SampleParams(bpm=240)  # device rejects with status=1 above ~200


def test_sample_params_bpm_edge_values():
    """1-200 BPM accepted (conservative cap from on-device observations)."""
    P.SampleParams(bpm=1.0)
    P.SampleParams(bpm=120.0)
    P.SampleParams(bpm=200.0)
    # Loopstart=-1 / loopend=-1 = "no loop" — valid sentinels
    P.SampleParams(loopstart=-1, loopend=-1)


# ── build_slot_metadata_set ───────────────────────────────────────────

def test_build_slot_metadata_set_layout():
    """Framing: 07 01 [fileId:u16 BE] [json] 00 — same wire as pad writes."""
    payload = P.build_slot_metadata_set(slot=100, params=P.SampleParams(bpm=120.0))
    # Header: 07 01
    assert payload[0] == 0x07
    assert payload[1] == 0x01
    # fileId = 100 = 0x0064
    assert payload[2] == 0x00
    assert payload[3] == 0x64
    # JSON follows
    json_start = 4
    json_end = payload.index(0, json_start)
    assert json.loads(payload[json_start:json_end]) == {"sound.bpm": 120.0}
    # Null terminator
    assert payload[-1] == 0x00


def test_build_slot_metadata_set_range_check():
    """Slot must fit in u16 and be >= 1."""
    with pytest.raises(ValueError, match="slot"):
        P.build_slot_metadata_set(slot=0, params=P.SampleParams(bpm=120.0))
    with pytest.raises(ValueError, match="slot"):
        P.build_slot_metadata_set(slot=70000, params=P.SampleParams(bpm=120.0))
