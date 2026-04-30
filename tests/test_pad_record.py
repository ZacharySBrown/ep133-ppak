"""Tests for pad-record BPM decoder.

Data points captured live from an EP-133 on 2026-04-24 during the SysEx
project-read breakthrough. Two distinct encodings were observed; both are
exercised here with the exact bytes the device returned.
"""

import struct

import pytest

from ep133 import pad_record as PR
from ep133.project_reader import project_file_id


# ── project_file_id ────────────────────────────────────────────────────

def test_project_file_id_formula():
    assert project_file_id(1) == 3000
    assert project_file_id(2) == 4000
    assert project_file_id(7) == 9000   # the project we read during discovery
    assert project_file_id(8) == 10000
    assert project_file_id(99) == 101000


def test_project_file_id_range():
    with pytest.raises(ValueError, match="project_num"):
        project_file_id(0)
    with pytest.raises(ValueError, match="project_num"):
        project_file_id(100)


# ── decode_bpm: override encoding ─────────────────────────────────────

# Exact record for pad label "9" (pad_num 3) after knobY saved BPM=100 on device.
# Bytes +0..+31 as returned by the device's project-read stream.
PAD_9_BPM_100 = bytes.fromhex(
    "004000800000808020a385000080c800"
    "e40000000f8100013c01000000000000"
)

# Earlier in the session the same pad was at BPM=92; later BPM=150. The
# override encoding was identical except for +14 and the precision flag at +15.
# Reconstructed from the diff:
PAD_9_BPM_92 = bytearray(PAD_9_BPM_100)
PAD_9_BPM_92[14] = 0xB8  # 184 = 92 * 2
PAD_9_BPM_92[15] = 0x00
PAD_9_BPM_92 = bytes(PAD_9_BPM_92)

PAD_9_BPM_150 = bytearray(PAD_9_BPM_100)
PAD_9_BPM_150[14] = 0x96  # 150 (high-range: not doubled)
PAD_9_BPM_150[15] = 0x80
PAD_9_BPM_150 = bytes(PAD_9_BPM_150)


def test_override_low_range_bpm_100():
    bpm, enc = PR.decode_bpm(PAD_9_BPM_100)
    assert enc == "override"
    assert bpm == 100.0


def test_override_low_range_bpm_92():
    bpm, enc = PR.decode_bpm(PAD_9_BPM_92)
    assert enc == "override"
    assert bpm == 92.0


def test_override_high_range_bpm_150():
    bpm, enc = PR.decode_bpm(PAD_9_BPM_150)
    assert enc == "override"
    assert bpm == 150.0


# ── decode_bpm: float32 encoding ──────────────────────────────────────

# Exact record for pad label "6" (pad_num 6) after knobY saved BPM=70.
# This pad had been toggled BAR→BPM→BAR→BPM before saving, which appears
# to trigger the float32 encoding instead of the override encoding.
PAD_6_BPM_70 = bytes.fromhex(
    "00640000000002006623050000010c42"
    "64000000808180813c00000000000000"
)


def test_float32_encoding_bpm_70():
    bpm, enc = PR.decode_bpm(PAD_6_BPM_70)
    assert enc == "float32"
    # Device stored ~35.001 (not exactly 35.0) — tolerate tiny float drift
    assert abs(bpm - 70.0) < 0.01


# ── decode_bpm: default (unset) pad ───────────────────────────────────

# Representative default pad: float32 LE of 60.0 at +12..+15 → decoded as 120.
# (Per module docstring: this is our "default = 120 BPM" hypothesis.)
DEFAULT_PAD = b"\x00" * 12 + struct.pack("<f", 60.0) + b"\x00" * 16


def test_default_pad_decodes_as_float32_120():
    bpm, enc = PR.decode_bpm(DEFAULT_PAD)
    assert enc == "float32"
    assert bpm == 120.0


# ── decode_bpm: unknown / unparseable ─────────────────────────────────

def test_too_short_record():
    bpm, enc = PR.decode_bpm(b"\x00" * 8)  # shorter than +12+4
    assert bpm is None
    assert enc == "unknown"


def test_nonsense_bytes_returns_unknown():
    # +13 != 0x80 and float32 bytes don't form a valid BPM
    record = b"\x00" * 12 + b"\xff\xff\xff\x7f" + b"\x00" * 16  # max finite float
    bpm, enc = PR.decode_bpm(record)
    assert bpm is None
    assert enc == "unknown"


# ── find_pad_records: TAR scan ────────────────────────────────────────

def test_find_pad_records_identifies_pNN_names():
    """Synthetic TAR with 2 pad blocks; both should be found."""
    tar = bytearray(4096)
    # Block 0 at offset 0: filename "pads/c/p03" + padding, then pad data
    tar[0:10] = b"pads/c/p03"
    tar[512 : 512 + len(PAD_9_BPM_100)] = PAD_9_BPM_100
    # Block 1 at offset 2048: filename "pads/c/p06" + padding, then pad data
    tar[2048 : 2048 + 10] = b"pads/c/p06"
    tar[2560 : 2560 + len(PAD_6_BPM_70)] = PAD_6_BPM_70

    records = PR.find_pad_records(bytes(tar))
    # Both pad blocks should have been identified, at minimum
    assert any(r.block_offset == 0 for r in records)
    assert any(r.block_offset == 2048 for r in records)
    # And the decoded BPMs should match their expected values
    by_offset = {r.block_offset: r for r in records}
    assert by_offset[0].bpm == 100.0
    assert by_offset[0].bpm_encoding == "override"
    assert abs(by_offset[2048].bpm - 70.0) < 0.01
    assert by_offset[2048].bpm_encoding == "float32"


def test_find_pad_records_skips_non_pad_blocks():
    """A TAR with no pad-named blocks yields no records."""
    tar = bytearray(2048)
    tar[0:10] = b"random/hdr"
    tar[512 : 512 + 32] = b"\xff" * 32
    assert PR.find_pad_records(bytes(tar)) == []


# ── builder × decoder round-trip ──────────────────────────────────────
# These tests pin the contract between the new song-mode builder
# (ep133.song.format.build_pad) and the existing decoder
# (ep133.pad_record.decode_bpm).
#
# **Known disagreement (xfail until hardware-resolved):**
#
# decode_bpm's float32 branch returns ``2.0 * float32_le(bytes[12:16])``,
# justified by ONE captured data point (pad 6 at BPM=70, bytes encoding
# 35.001 as float32). The decoder's own docstring marks this rule as
# "tentative — needs more test points to confirm".
#
# build_pad (ported from StemForge) writes BPM as RAW float32 LE at
# +12..+15. Hardware-validated through StemForge's song-export pipeline,
# which produces .ppak files that play at correct tempo.
#
# The two views can't both be right for arbitrary pads. Possible
# explanations:
#   1. The /2 rule applies only to pads that were toggled BAR→BPM
#      multiple times before save (pad 6's case) — distinguishable by
#      something else in the record (byte 21 mode flag, byte 23 play
#      mode).
#   2. The captured pad 6's "BPM=70" claim was wrong — actual BPM=35.
#   3. The device interprets the float32 differently in different modes.
#
# Resolving requires more device captures across mode combinations.
# Until then, the round-trip tests below are marked xfail so CI flags
# the moment someone fixes either side.


@pytest.mark.xfail(
    reason="decode_bpm's *2 rule conflicts with build_pad's raw-float32 write; "
    "needs hardware capture across mode combinations to resolve"
)
def test_song_format_build_pad_decodes_as_float32():
    """build_pad in BARS mode writes BPM as float32 LE at +12..+15.
    decode_bpm should report enc='float32' and the same BPM value."""
    from ep133.song.format import build_pad

    blob = build_pad(
        sample_slot=42,
        play_mode="oneshot",
        time_stretch_bars=1,
        project_bpm=120.0,
        stretch_mode="bars",
    )
    bpm, enc = PR.decode_bpm(blob)
    assert enc == "float32"
    assert bpm == pytest.approx(120.0)


@pytest.mark.xfail(
    reason="decode_bpm's *2 rule conflicts with build_pad's raw-float32 write; "
    "needs hardware capture across mode combinations to resolve"
)
def test_song_format_build_pad_in_bpm_mode_decodes_as_float32():
    """BPM-stretch mode writes source BPM as float32 LE at +12..+15."""
    from ep133.song.format import build_pad

    blob = build_pad(
        sample_slot=42,
        play_mode="oneshot",
        time_stretch_bars=1,
        stretch_mode="bpm",
        sound_bpm=135.99,
    )
    bpm, enc = PR.decode_bpm(blob)
    assert enc == "float32"
    assert bpm == pytest.approx(135.99, abs=0.01)


def test_song_format_pad_record_size_constant_is_26():
    """Factory native pad records are 26 bytes. Sample Tool's 27-byte
    form is non-canonical (corrupts during scene-switch iteration)."""
    from ep133.song.format import PAD_RECORD_SIZE

    assert PAD_RECORD_SIZE == 26
