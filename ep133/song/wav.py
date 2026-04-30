"""EP-133 K.O. II native WAV format + metadata.

The device requires samples in a specific format with two non-standard
metadata chunks; bundling raw user WAVs causes Sample Tool transfers to
hang. Verified against ``factory_default.pak`` (a freshly factory-reset
device backup — every sample in the factory library follows this exact
format):

- **mono, 16-bit, 46875 Hz** PCM
- ``smpl`` chunk (36 bytes) with ``MIDIUnityNote=60`` (other fields zero)
- ``LIST/INFO/TNGE`` chunk (176 bytes) holding a JSON blob with default
  per-sample parameters (playmode, rootnote, amplitude, envelope, etc.)
- chunk order: ``fmt`` → ``smpl`` → ``LIST`` → ``data``

This module owns the conversion + metadata-chunk authoring.
"""

from __future__ import annotations

import audioop  # noqa: TODO migrate before Python 3.13 (removal); scipy.signal.resample_poly is the most likely successor.
import io
import json
import struct
import wave

EP133_SAMPLE_RATE = 46875
EP133_CHANNELS = 1
EP133_SAMPLE_WIDTH = 2  # 16-bit

# Default per-sample metadata JSON for one-shot mode. Verified verbatim
# against every factory sample. The 164-byte padding is also
# factory-verified for this no-BPM form.
DEFAULT_SOUND_METADATA_JSON = (
    '{"sound.playmode":"oneshot","sound.rootnote":60,"sound.pitch":0,'
    '"sound.pan":0,"sound.amplitude":100,"envelope.attack":0,'
    '"envelope.release":255,"time.mode":"off"}'
)
# Factory's per-sample TNGE chunk size. Used as the floor when sizing
# BPM-mode chunks so the WAV layout stays close to factory.
TNGE_PAYLOAD_SIZE = 164


def convert_wav_to_ep133(
    wav_bytes: bytes,
    *,
    sound_bpm: float | None = None,
    start_sec: float = 0.0,
    end_sec: float | None = None,
) -> tuple[bytes, int]:
    """Convert a WAV to EP-133 native format with metadata chunks.

    Accepts any standard PCM WAV; returns ``(new_wav_bytes, frame_count)``
    where ``frame_count`` is the post-conversion sample-frame count
    suitable for writing into the pad record's bytes 8..11.

    When ``sound_bpm`` is provided, the embedded TNGE JSON sets
    ``time.mode=bpm`` and ``sound.bpm=<value>`` so the device stretches
    playback to project tempo (``playback_speed = project_bpm /
    sound.bpm``). Per PROTOCOL.md §5/§7.2 the pad-record's float32 BPM
    at bytes 12..15 overrides this slot value when both are set, so the
    WAV metadata is effectively a fallback; we still write it so the
    slot library is consistent.

    ``start_sec`` / ``end_sec`` slice the WAV in input-time seconds (i.e.
    seconds of the original file at its native sample rate, before any
    conversion). The slice is taken before resample/mono/16-bit so that
    rounding errors stay below one input frame. Use this to upload only
    the bar-aligned region of a longer rendered stem (forge curation
    typically stores 6-bar renders sliced to 1- or 2-bar regions in the
    manifest's ``start_offset_sec`` / ``end_offset_sec`` fields).

    Raises ``wave.Error`` on unparseable input.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        nframes = wf.getnframes()
        data = wf.readframes(nframes)

    if start_sec < 0:
        raise ValueError(f"start_sec must be >= 0, got {start_sec}")
    if end_sec is not None and end_sec <= start_sec:
        raise ValueError(f"end_sec ({end_sec}) must be > start_sec ({start_sec})")

    # Slice before any conversion so the frame indices line up exactly
    # with the input WAV's sample rate.
    if start_sec > 0 or end_sec is not None:
        bytes_per_frame = width * channels
        start_frame = int(round(start_sec * rate))
        end_frame = int(round(end_sec * rate)) if end_sec is not None else nframes
        start_frame = max(0, min(start_frame, nframes))
        end_frame = max(start_frame, min(end_frame, nframes))
        data = data[start_frame * bytes_per_frame : end_frame * bytes_per_frame]

    # 1. Sample width → 16-bit
    if width != EP133_SAMPLE_WIDTH:
        data = audioop.lin2lin(data, width, EP133_SAMPLE_WIDTH)

    # 2. Channels → mono
    if channels == 2:
        data = audioop.tomono(data, EP133_SAMPLE_WIDTH, 0.5, 0.5)
    elif channels != 1:
        raise ValueError(f"unsupported channel count {channels} (need 1 or 2)")

    # 3. Sample rate → 46875 Hz
    if rate != EP133_SAMPLE_RATE:
        data, _ = audioop.ratecv(
            data,
            EP133_SAMPLE_WIDTH,
            EP133_CHANNELS,
            rate,
            EP133_SAMPLE_RATE,
            None,
        )

    frames = len(data) // (EP133_SAMPLE_WIDTH * EP133_CHANNELS)
    return _build_ep133_wav(data, sound_bpm=sound_bpm), frames


def _build_metadata_json(sound_bpm: float | None) -> bytes:
    """Build the TNGE-chunk JSON, optionally tagged with sound.bpm."""
    if sound_bpm is None:
        return DEFAULT_SOUND_METADATA_JSON.encode("utf-8")
    if not (1.0 <= sound_bpm <= 200.0):
        # Device rejects writes outside this range (PROTOCOL.md §5).
        raise ValueError(f"sound_bpm {sound_bpm} must be 1.0..200.0 (device rejects higher)")
    # Match ep133-ppak SampleParams: 2-decimal float, formatted without
    # exponent notation. json.dumps gives the right shape.
    bpm_str = json.dumps(round(float(sound_bpm), 2))
    return (
        '{"sound.playmode":"oneshot","sound.rootnote":60,"sound.pitch":0,'
        '"sound.pan":0,"sound.amplitude":100,"envelope.attack":0,'
        f'"envelope.release":255,"time.mode":"bpm","sound.bpm":{bpm_str}}}'
    ).encode("utf-8")


def _chunk(cid: bytes, payload: bytes) -> bytes:
    """RIFF chunk: 4-byte ID + 4-byte LE size + payload + odd-byte pad."""
    pad = b"\x00" if len(payload) & 1 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def _build_ep133_wav(pcm_data: bytes, *, sound_bpm: float | None = None) -> bytes:
    """Wrap PCM data + EP-133 metadata in a RIFF/WAVE container."""
    # fmt chunk (16 bytes)
    byte_rate = EP133_SAMPLE_RATE * EP133_CHANNELS * EP133_SAMPLE_WIDTH
    block_align = EP133_CHANNELS * EP133_SAMPLE_WIDTH
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,  # PCM format
        EP133_CHANNELS,
        EP133_SAMPLE_RATE,
        byte_rate,
        block_align,
        EP133_SAMPLE_WIDTH * 8,  # bits per sample
    )

    # smpl chunk (36 bytes): only MIDIUnityNote (=60) is non-zero
    smpl_payload = struct.pack(
        "<9I",
        0,  # Manufacturer
        0,  # Product
        0,  # SamplePeriod (ns)
        60,  # MIDIUnityNote
        0,  # MIDIPitchFraction
        0,  # SMPTEFormat
        0,  # SMPTEOffset
        0,  # NumSampleLoops
        0,  # SamplerDataLen
    )

    # LIST/INFO/TNGE: JSON metadata padded to a chunk slot. Factory uses
    # 164 bytes for the no-BPM JSON; BPM-mode JSON is longer, so we round
    # up to the next 4-byte boundary at or above the factory size.
    json_bytes = _build_metadata_json(sound_bpm)
    payload_size = max(TNGE_PAYLOAD_SIZE, (len(json_bytes) + 3) & ~3)
    if len(json_bytes) > payload_size:
        raise ValueError(f"metadata JSON too large: {len(json_bytes)} > {payload_size}")
    json_padded = json_bytes + b"\x00" * (payload_size - len(json_bytes))
    tnge_chunk = b"TNGE" + struct.pack("<I", payload_size) + json_padded
    list_payload = b"INFO" + tnge_chunk

    # Body = WAVE + chunks (factory order: fmt, smpl, LIST, data)
    body = (
        b"WAVE"
        + _chunk(b"fmt ", fmt_payload)
        + _chunk(b"smpl", smpl_payload)
        + _chunk(b"LIST", list_payload)
        + _chunk(b"data", pcm_data)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body
