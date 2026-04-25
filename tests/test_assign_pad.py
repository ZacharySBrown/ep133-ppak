"""
Byte-identical reproduction of the 8 Sample Tool pad-assign captures.

Same gate as the upload tests: if we can generate every assign message
byte-for-byte from (project, group, pad_num, slot), the protocol is
locked. Request IDs differ per session (random seeds) so we compare
unpacked payloads, not framed bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ep133 import payloads as P
from ep133.sysex import parse_sysex

FIX = Path(__file__).parent / "fixtures" / "pad"


# (fixture_name, project, group, pad_num, slot, expected_file_id)
CASES = [
    # Triangulation captures (1-4)
    ("assign_p01_A_pad10_sym1.syx",  1, "A", 10, 1,   3210),
    ("assign_p01_A_pad7_sym1.syx",   1, "A",  7, 1,   3207),
    ("assign_p01_B_pad10_sym1.syx",  1, "B", 10, 1,   3310),
    ("assign_p02_A_pad10_sym1.syx",  2, "A", 10, 1,   4210),
    # Validation captures (5-8)
    ("assign_p03_C_pad5_sym19.syx",  3, "C",  5, 19,  5405),
    ("assign_p07_D_pad3_sym24.syx",  7, "D",  3, 24,  9503),
    ("assign_p05_A_pad12_sym11.syx", 5, "A", 12, 11,  7212),
    ("assign_p02_B_pad1_sym415.syx", 2, "B",  1, 415, 4301),
]


@pytest.mark.parametrize("fixture,project,group,pad_num,slot,expected_fid", CASES)
def test_pad_file_id_formula(fixture, project, group, pad_num, slot, expected_fid):
    """The (project, group, pad_num) → fileId formula matches every capture."""
    assert P.pad_file_id(project, group, pad_num) == expected_fid


@pytest.mark.parametrize("fixture,project,group,pad_num,slot,expected_fid", CASES)
def test_assign_pad_byte_identical(fixture, project, group, pad_num, slot, expected_fid):
    """Our generated payload matches the captured wire bytes exactly."""
    frame = (FIX / fixture).read_bytes()
    parsed = parse_sysex(frame)
    assert parsed is not None, f"unparseable frame in {fixture}"
    assert parsed.command == 5

    generated = P.build_assign_pad(project, group, pad_num, slot)
    assert generated == parsed.raw_data, (
        f"{fixture}: payload mismatch\n"
        f"  want: {parsed.raw_data.hex()}\n"
        f"  got:  {generated.hex()}"
    )


def test_pad_label_mapping_is_phone_keypad():
    """Physical pad labels → visual pad_num sanity check."""
    assert P.pad_num_from_label("7") == 1
    assert P.pad_num_from_label("1") == 7
    assert P.pad_num_from_label(".") == 10
    assert P.pad_num_from_label("ENTER") == 12
    assert P.pad_num_from_label("E") == 12  # shorthand alias


def test_pad_file_id_rejects_bad_inputs():
    with pytest.raises(ValueError):
        P.pad_file_id(0, "A", 1)  # project must be >= 1
    with pytest.raises(ValueError):
        P.pad_file_id(1, "E", 1)  # no group E
    with pytest.raises(ValueError):
        P.pad_file_id(1, "A", 0)  # pad_num must be 1..12
    with pytest.raises(ValueError):
        P.pad_file_id(1, "A", 13)  # pad_num must be 1..12


def test_build_metadata_set_null_termination():
    """Null terminator is appended; caller must not pre-include it."""
    out = P.build_metadata_set(0x1234, b'{"sym":5}')
    assert out.endswith(b"\0")
    assert out.count(b"\0") == 1  # exactly one null

    with pytest.raises(ValueError):
        P.build_metadata_set(0x1234, b'{"sym":5}\0')  # caller-supplied null rejected


# ── PadParams tests ───────────────────────────────────────────────────

def test_pad_params_defaults():
    """Default PadParams serializes the expected factory-state JSON."""
    import json
    params = P.PadParams()
    raw = params.to_json(slot=700)
    d = json.loads(raw)
    assert d["sym"] == 700
    assert d["sound.playmode"] == "oneshot"  # string form — device rejects integer on write (2026-04-24 diag)
    assert d["sample.start"] == 0
    assert "sample.end" not in d   # omitted when None
    assert d["envelope.attack"] == 0
    assert d["envelope.release"] == 255
    assert d["sound.pitch"] == 0.0
    assert d["sound.amplitude"] == 100
    assert d["sound.pan"] == 0
    assert d["sound.mutegroup"] is False
    assert d["time.mode"] == "off"  # string form — device rejects integer on write (2026-04-24 diag)


def test_pad_params_all_fields():
    """Non-default PadParams with sample_end set round-trips through JSON."""
    import json
    params = P.PadParams(
        playmode="key",
        sample_start=100,
        sample_end=44100,
        attack=10,
        release=200,
        pitch=2.5,
        amplitude=80,
        pan=-8,
        mutegroup=True,
        time_mode="bpm",
    )
    raw = params.to_json(slot=1)
    d = json.loads(raw)
    assert d["sound.playmode"] == "key"  # string form — device rejects integer on write
    assert d["sample.start"] == 100
    assert d["sample.end"] == 44100
    assert d["envelope.attack"] == 10
    assert d["envelope.release"] == 200
    assert d["sound.pitch"] == 2.5
    assert d["sound.amplitude"] == 80
    assert d["sound.pan"] == -8
    assert d["sound.mutegroup"] is True
    assert d["time.mode"] == "bpm"  # string form — device rejects integer on write


def test_pad_params_playmode_release_coupling():
    """Release auto-pairs with playmode: oneshot↔255, key↔15, legato↔255.

    The on-device UI always writes both fields atomically when playmode changes.
    Writing playmode alone (with the wrong release) causes key-mode gating to
    silently fail at playback. Verified on-device 2026-04-24.
    """
    import json
    assert json.loads(P.PadParams(playmode="oneshot").to_json(slot=1))["envelope.release"] == 255
    assert json.loads(P.PadParams(playmode="key").to_json(slot=1))["envelope.release"] == 15
    assert json.loads(P.PadParams(playmode="legato").to_json(slot=1))["envelope.release"] == 255

    # Explicit release overrides the auto-pair
    assert json.loads(P.PadParams(playmode="key", release=100).to_json(slot=1))["envelope.release"] == 100


def test_pad_params_validation():
    """PadParams rejects out-of-range and invalid values."""
    with pytest.raises(ValueError, match="playmode"):
        P.PadParams(playmode="loop")
    with pytest.raises(ValueError, match="time_mode"):
        P.PadParams(time_mode="stretch")
    with pytest.raises(ValueError, match="sample_start"):
        P.PadParams(sample_start=-1)
    with pytest.raises(ValueError, match="sample_end"):
        P.PadParams(sample_start=100, sample_end=50)
    with pytest.raises(ValueError, match="attack"):
        P.PadParams(attack=256)
    with pytest.raises(ValueError, match="release"):
        P.PadParams(release=-1)
    with pytest.raises(ValueError, match="amplitude"):
        P.PadParams(amplitude=101)
    with pytest.raises(ValueError, match="pan"):
        P.PadParams(pan=17)


def test_build_assign_pad_no_params_unchanged():
    """build_assign_pad without params is byte-identical to pre-PadParams behavior."""
    # Any fixture from the CASES table — use the first one
    fixture, project, group, pad_num, slot, _ = CASES[0]
    frame = (FIX / fixture).read_bytes()
    from ep133.sysex import parse_sysex
    parsed = parse_sysex(frame)
    assert P.build_assign_pad(project, group, pad_num, slot) == parsed.raw_data


def test_build_assign_pad_with_params_includes_all_fields():
    """build_assign_pad with PadParams embeds the full JSON."""
    import json
    params = P.PadParams(playmode="legato", attack=5, release=128)
    payload = P.build_assign_pad(1, "A", 10, 700, params=params)
    # Layout: 07 01 [file_id:u16 BE] [json] 00
    assert payload[0] == 0x07
    assert payload[1] == 0x01
    json_start = 4
    json_end = payload.index(0, json_start)
    d = json.loads(payload[json_start:json_end])
    assert d["sym"] == 700
    assert d["sound.playmode"] == "legato"  # string form — device rejects integer on write
    assert d["envelope.attack"] == 5
    assert d["envelope.release"] == 128
    assert payload[-1] == 0  # null terminator


# ── midi_channel tests ────────────────────────────────────────────────

def test_pad_params_midi_channel_default():
    """Default midi_channel is 0 and appears in to_json() output."""
    import json
    params = P.PadParams()
    d = json.loads(params.to_json(slot=1))
    assert "midi.channel" in d
    assert d["midi.channel"] == 0


def test_pad_params_midi_channel_set():
    """Non-default midi_channel is serialized correctly."""
    import json
    params = P.PadParams(midi_channel=5)
    d = json.loads(params.to_json(slot=1))
    assert d["midi.channel"] == 5


def test_pad_params_midi_channel_validation():
    """midi_channel must be 0..15."""
    with pytest.raises(ValueError, match="midi_channel"):
        P.PadParams(midi_channel=16)
    with pytest.raises(ValueError, match="midi_channel"):
        P.PadParams(midi_channel=-1)


def test_pad_params_midi_channel_boundary():
    """Boundary values 0 and 15 are accepted."""
    import json
    for ch in (0, 15):
        params = P.PadParams(midi_channel=ch)
        d = json.loads(params.to_json(slot=1))
        assert d["midi.channel"] == ch
