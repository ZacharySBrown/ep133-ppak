"""Tests for ``ep133.song.wav.convert_wav_to_ep133``.

Builds synthetic PCM WAVs at various sample rates / bit depths / channel
counts, runs them through the converter, and re-parses the output to
verify the EP-133 format constraints.
"""

from __future__ import annotations

import io
import json
import struct
import wave

import pytest

from ep133.song.wav import (
    EP133_CHANNELS,
    EP133_SAMPLE_RATE,
    EP133_SAMPLE_WIDTH,
    convert_wav_to_ep133,
)


# ---- helpers ---------------------------------------------------------------


def _make_wav(*, rate: int, channels: int, width: int, frames: int) -> bytes:
    """Build a synthetic PCM WAV — flat zeros at the given format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames * channels * width))
    return buf.getvalue()


def _read_chunks(wav_bytes: bytes) -> list[tuple[str, int, bytes]]:
    """Parse a WAV byte stream into [(chunk_id, payload_size, payload), ...].

    Walks the RIFF body chunk-by-chunk; unaffected by chunk order."""
    if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise AssertionError("not a RIFF/WAVE file")
    out: list[tuple[str, int, bytes]] = []
    pos = 12
    while pos < len(wav_bytes):
        if pos + 8 > len(wav_bytes):
            break
        cid = wav_bytes[pos : pos + 4].decode("ascii")
        size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
        payload = wav_bytes[pos + 8 : pos + 8 + size]
        out.append((cid, size, payload))
        # RIFF chunks are word-aligned (odd-byte pad)
        pos += 8 + size + (size & 1)
    return out


def _read_format(wav_bytes: bytes) -> tuple[int, int, int]:
    """Return (channels, rate, sample_width_bits) from the fmt chunk."""
    for cid, _size, payload in _read_chunks(wav_bytes):
        if cid == "fmt ":
            channels = struct.unpack_from("<H", payload, 2)[0]
            rate = struct.unpack_from("<I", payload, 4)[0]
            bits = struct.unpack_from("<H", payload, 14)[0]
            return channels, rate, bits
    raise AssertionError("no fmt chunk")


def _read_tnge_json(wav_bytes: bytes) -> dict:
    """Locate the LIST/INFO/TNGE chunk and return the embedded JSON dict."""
    for cid, _size, payload in _read_chunks(wav_bytes):
        if cid != "LIST":
            continue
        if payload[:4] != b"INFO":
            continue
        # After "INFO", a TNGE sub-chunk: 4-byte ID + 4-byte size + payload.
        if payload[4:8] != b"TNGE":
            continue
        sub_size = struct.unpack_from("<I", payload, 8)[0]
        json_bytes = payload[12 : 12 + sub_size].rstrip(b"\x00")
        return json.loads(json_bytes.decode("utf-8"))
    raise AssertionError("no LIST/INFO/TNGE chunk")


# ---- format conformance ---------------------------------------------------


def test_output_is_16bit_mono_46875hz():
    wav = _make_wav(rate=44100, channels=2, width=3, frames=44100)
    out, frames = convert_wav_to_ep133(wav)
    channels, rate, bits = _read_format(out)
    assert channels == EP133_CHANNELS == 1
    assert rate == EP133_SAMPLE_RATE == 46875
    assert bits == EP133_SAMPLE_WIDTH * 8 == 16
    assert frames > 0


def test_output_is_already_native_passes_through_unchanged_format():
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=1000
    )
    out, frames = convert_wav_to_ep133(wav)
    channels, rate, bits = _read_format(out)
    assert (channels, rate, bits) == (1, 46875, 16)
    assert frames == 1000


def test_chunk_order_is_fmt_smpl_list_data():
    """Factory order: fmt → smpl → LIST → data. Verified against
    factory_default.pak — every factory sample uses this order."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    out, _ = convert_wav_to_ep133(wav)
    chunk_ids = [cid for cid, _, _ in _read_chunks(out)]
    assert chunk_ids == ["fmt ", "smpl", "LIST", "data"]


def test_smpl_chunk_has_midi_unity_note_60():
    """smpl chunk: only MIDIUnityNote (=60 = C4) is non-zero. Other 8 fields
    are zero. Total payload = 36 bytes (9 × uint32 LE)."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    out, _ = convert_wav_to_ep133(wav)
    smpl = next(payload for cid, _, payload in _read_chunks(out) if cid == "smpl")
    assert len(smpl) == 36
    fields = struct.unpack("<9I", smpl)
    # Index 3 is MIDIUnityNote per the smpl chunk spec.
    assert fields[3] == 60
    # All other fields zero.
    for i, v in enumerate(fields):
        if i == 3:
            continue
        assert v == 0, f"smpl field {i} = {v}, expected 0"


def test_default_metadata_is_oneshot_no_bpm():
    """Without sound_bpm, TNGE JSON is the factory default oneshot config:
    time.mode=off, no sound.bpm key."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    out, _ = convert_wav_to_ep133(wav)
    meta = _read_tnge_json(out)
    assert meta["sound.playmode"] == "oneshot"
    assert meta["sound.rootnote"] == 60
    assert meta["sound.amplitude"] == 100
    assert meta["envelope.release"] == 255
    assert meta["time.mode"] == "off"
    assert "sound.bpm" not in meta


def test_with_sound_bpm_sets_time_mode_bpm_and_embeds_value():
    """sound_bpm present → time.mode=bpm and sound.bpm=<value>. Per
    PROTOCOL.md §10.2, the device computes
    playback_speed = project_bpm / sound.bpm."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    out, _ = convert_wav_to_ep133(wav, sound_bpm=120.0)
    meta = _read_tnge_json(out)
    assert meta["time.mode"] == "bpm"
    assert meta["sound.bpm"] == pytest.approx(120.0)


def test_sound_bpm_rounds_to_two_decimals():
    """ep133-ppak SampleParams convention: 2-decimal float, no exponent."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    out, _ = convert_wav_to_ep133(wav, sound_bpm=135.999)
    meta = _read_tnge_json(out)
    assert meta["sound.bpm"] == pytest.approx(136.0)


def test_sound_bpm_rejects_out_of_range():
    """Device rejects sound.bpm outside 1..200 (PROTOCOL.md §5)."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    for bad in (0.5, 250.0, -1.0):
        with pytest.raises(ValueError, match="sound_bpm"):
            convert_wav_to_ep133(wav, sound_bpm=bad)


# ---- frame-count + slicing ------------------------------------------------


def test_frame_count_matches_data_chunk_size():
    """Returned frame count == len(data) / (channels * sample_width)."""
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=2048
    )
    out, frames = convert_wav_to_ep133(wav)
    data = next(payload for cid, _, payload in _read_chunks(out) if cid == "data")
    assert frames == len(data) // (EP133_SAMPLE_WIDTH * EP133_CHANNELS) == 2048


def test_resample_changes_frame_count_proportionally():
    """44100 Hz × 1 sec → 44100 frames in. After resample to 46875 Hz the
    output should have ≈46875 frames (small rounding tolerance)."""
    wav = _make_wav(rate=44100, channels=1, width=2, frames=44100)  # 1 sec
    _out, frames = convert_wav_to_ep133(wav)
    assert abs(frames - EP133_SAMPLE_RATE) < 100, f"got {frames}, expected ~{EP133_SAMPLE_RATE}"


def test_slicing_takes_substring_in_input_seconds():
    """start_sec/end_sec slice the WAV in input-time seconds (before any
    resample/mono/16-bit conversion). 4-second input, slice [1.0, 3.0]
    → 2 seconds → ~93750 output frames at 46875 Hz."""
    wav = _make_wav(rate=44100, channels=1, width=2, frames=44100 * 4)  # 4 seconds
    _out, frames = convert_wav_to_ep133(wav, start_sec=1.0, end_sec=3.0)
    expected = int(2.0 * EP133_SAMPLE_RATE)
    assert abs(frames - expected) < 200


def test_slicing_with_only_start_runs_to_end():
    """Omit end_sec → slice from start to end of input."""
    wav = _make_wav(rate=44100, channels=1, width=2, frames=44100 * 4)
    _out, frames = convert_wav_to_ep133(wav, start_sec=2.0)
    expected = int(2.0 * EP133_SAMPLE_RATE)
    assert abs(frames - expected) < 200


def test_slicing_rejects_negative_start():
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    with pytest.raises(ValueError, match="start_sec"):
        convert_wav_to_ep133(wav, start_sec=-0.5)


def test_slicing_rejects_end_at_or_before_start():
    wav = _make_wav(
        rate=EP133_SAMPLE_RATE, channels=EP133_CHANNELS, width=EP133_SAMPLE_WIDTH, frames=100
    )
    with pytest.raises(ValueError, match="end_sec"):
        convert_wav_to_ep133(wav, start_sec=1.0, end_sec=1.0)
    with pytest.raises(ValueError, match="end_sec"):
        convert_wav_to_ep133(wav, start_sec=1.0, end_sec=0.5)


def test_slicing_clamps_to_input_bounds():
    """start_sec past EOF returns empty / minimal data, end_sec past EOF
    truncates at EOF — should not raise."""
    wav = _make_wav(rate=44100, channels=1, width=2, frames=44100)  # 1 sec
    # end_sec way past file end
    _out, frames = convert_wav_to_ep133(wav, start_sec=0.5, end_sec=99.0)
    assert frames > 0


# ---- channel + bit-depth conversion ---------------------------------------


def test_stereo_input_collapses_to_mono():
    wav = _make_wav(rate=EP133_SAMPLE_RATE, channels=2, width=EP133_SAMPLE_WIDTH, frames=1000)
    out, frames = convert_wav_to_ep133(wav)
    channels, _, _ = _read_format(out)
    assert channels == 1
    assert frames == 1000  # frames count unchanged by stereo→mono


def test_24bit_input_converts_to_16bit():
    wav = _make_wav(rate=EP133_SAMPLE_RATE, channels=1, width=3, frames=1000)
    out, frames = convert_wav_to_ep133(wav)
    _, _, bits = _read_format(out)
    assert bits == 16
    assert frames == 1000


def test_unsupported_channel_count_raises():
    """4-channel input — not valid for EP-133 conversion."""
    wav = _make_wav(rate=EP133_SAMPLE_RATE, channels=4, width=EP133_SAMPLE_WIDTH, frames=100)
    with pytest.raises(ValueError, match="channel count"):
        convert_wav_to_ep133(wav)


# ---- error paths ----------------------------------------------------------


def test_unparseable_input_raises_wave_error():
    """Garbage bytes that aren't a WAV at all → raise wave.Error (not
    swallowed). Callers in build_ppak catch this explicitly."""
    with pytest.raises(wave.Error):
        convert_wav_to_ep133(b"not a wav file at all")


# ---- exposed constants ----------------------------------------------------


def test_constants_match_native_format():
    assert EP133_SAMPLE_RATE == 46875
    assert EP133_CHANNELS == 1
    assert EP133_SAMPLE_WIDTH == 2
