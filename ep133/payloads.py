"""
Payload builders for each TE SysEx message type.

Each function is pure: inputs → raw (unpacked) payload bytes. Framing
happens in `sysex.build_sysex`.

Ported verbatim from phones24:
    build_file_init   ← fsSysex.ts buildSysExFileInitRequest
    build_file_info   ← fsSysex.ts buildSysExFileInfoRequest

Reverse-engineered from Garrett's captures (no phones24 source):
    build_file_put_meta
    build_file_put_data
    build_file_put_terminator
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from .commands import (
    CHUNK_BYTES,
    META_C0,
    META_C3,
    PAD_BASE,
    PAD_GROUP_STRIDE,
    PAD_LABEL_TO_NUM,
    PAD_PROJECT_STRIDE,
    TE_SYSEX_FILE_INFO,
    TE_SYSEX_FILE_INIT,
    TE_SYSEX_FILE_METADATA,
    TE_SYSEX_FILE_METADATA_SET,
    TE_SYSEX_FILE_PUT,
    TE_SYSEX_FILE_PUT_PHASE_DATA,
    TE_SYSEX_FILE_PUT_PHASE_META,
)


# ──────────────────────────────────────────────────────────────────────
# Ported from phones24/fsSysex.ts
# ──────────────────────────────────────────────────────────────────────

def build_file_init(max_response_length: int, flags: int) -> bytes:
    """FILE_INIT request payload (6 bytes).

    phones24 uses flags=0 for read; writes use flags=1.
    """
    if not (0 <= max_response_length < 2**32):
        raise ValueError("max_response_length must fit in u32")
    if not (0 <= flags < 256):
        raise ValueError("flags must fit in u8")
    return struct.pack(">BBI", TE_SYSEX_FILE_INIT, flags, max_response_length)


def build_file_info(file_id: int) -> bytes:
    """FILE_INFO request payload (3 bytes)."""
    if not (0 <= file_id < 2**16):
        raise ValueError("file_id must fit in u16")
    return struct.pack(">BH", TE_SYSEX_FILE_INFO, file_id)


# ──────────────────────────────────────────────────────────────────────
# Reverse-engineered from Garrett's captures
# ──────────────────────────────────────────────────────────────────────

def build_file_put_meta(name: str, data_size: int, channels: int = 1, slot: int = 1) -> bytes:
    """Create-file + metadata payload.

    Layout (verified against Garrett's capture slot 1, TE web tool slot 709):
        02 00                           # put / phase=meta
        META_C0                         # 0x05 (unknown semantics)
        slot:u16 BE                     # target library slot (1..65535)
        META_C3:u16 BE                  # = 0x03E8 (unknown semantics)
        data_size:u32 BE                # raw PCM byte length ✓
        [name ASCII]\0
        {"channels":N}                  # JSON, no null terminator

    `name` must be ASCII. On the device it'll display as the filename.
    `slot` is the 1-based library slot to store the sample at.

    NOTE: what was previously labeled META_C1=0x00 is the high byte of the
    u16 slot field. Garrett's captures always used slot=1 so the high byte
    was 0x00 and appeared to be a constant. Confirmed u16 by capture of
    slot=709 → bytes 0x02 0xC5.
    """
    name_bytes = name.encode("ascii")
    if b"\0" in name_bytes:
        raise ValueError("name must not contain null bytes")
    if not (0 <= data_size < 2**32):
        raise ValueError("data_size must fit in u32")
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if not (1 <= slot <= 0xFFFF):
        raise ValueError(f"slot {slot} must be 1..65535")

    json_meta = f'{{"channels":{channels}}}'.encode("ascii")

    return (
        bytes([TE_SYSEX_FILE_PUT, TE_SYSEX_FILE_PUT_PHASE_META, META_C0])
        + struct.pack(">H", slot)
        + struct.pack(">H", META_C3)
        + struct.pack(">I", data_size)
        + name_bytes
        + b"\0"
        + json_meta
    )


def build_file_put_data(page: int, data: bytes) -> bytes:
    """Data-chunk payload.

    Layout:
        02 01                  # put / phase=data
        page:u16 BE
        [data bytes, may be empty]

    Pages are 0-indexed. `data` should be ≤ CHUNK_BYTES (433). The final
    page for a file is conventionally sent empty as a terminator — see
    `build_file_put_terminator`.
    """
    if not (0 <= page < 2**16):
        raise ValueError("page must fit in u16")
    if len(data) > CHUNK_BYTES:
        raise ValueError(f"data length {len(data)} exceeds CHUNK_BYTES ({CHUNK_BYTES})")

    return (
        bytes([TE_SYSEX_FILE_PUT, TE_SYSEX_FILE_PUT_PHASE_DATA])
        + struct.pack(">H", page)
        + data
    )


def build_file_put_terminator(last_page: int) -> bytes:
    """Empty-data-chunk payload marking end of file.

    `last_page` is the index of the last real data page. The terminator
    uses `last_page + 1`.
    """
    return build_file_put_data(last_page + 1, b"")


def chunk_pcm(data: bytes, chunk_size: int = CHUNK_BYTES) -> list[bytes]:
    """Split raw PCM bytes into CHUNK_BYTES-sized chunks (last may be shorter)."""
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


# ──────────────────────────────────────────────────────────────────────
# Pad playback parameters (confirmed via MIDI Monitor + krate PROTOCOL.md)
# ──────────────────────────────────────────────────────────────────────

_VALID_PLAYMODES = frozenset({"oneshot", "key", "legato"})
# 2026-04-24: Device EMITS integers as responses (captures show {"sound.playmode":1}) but
# REJECTS integer writes with status=1. String form is what the device accepts on write —
# diagnostic on 2026-04-24 proved {"sound.playmode":"oneshot"} ACKs and {"sound.playmode":0} ERRs.
# Integer ↔ string map kept for documentation, but to_json() uses the string form.
_PLAYMODE_WIRE_INT: dict[str, int] = {"oneshot": 0, "key": 1, "legato": 2}

# 2026-04-24: playmode gate behavior REQUIRES a paired envelope.release value. The on-device
# UI writes both fields atomically when you change playmode — writing playmode alone leaves
# release at the caller-provided value, which can silently mask the gate behavior.
# None = legato (on-device UI emits no release change; legato inherits prior release).
_PLAYMODE_DEFAULT_RELEASE: dict[str, int | None] = {
    "oneshot": 255,
    "key":     15,
    "legato":  None,
}
# 2026-04-24: BAR mode is singular "bar" on-device. Earlier "bars" was a guess that never matched
# capture. Device accepts string form only on write. Integer-to-string mapping verified twice:
# BPM→OFF→BPM transitions emit 0,1 → confirms BPM=1 (and therefore BAR=2).
_TIME_MODE_WIRE_INT: dict[str, int] = {"off": 0, "bpm": 1, "bar": 2}
_VALID_TIME_MODES = frozenset(_TIME_MODE_WIRE_INT.keys())


@dataclass
class PadParams:
    """Per-pad playback parameters written via FILE_METADATA_SET.

    Fields mirror the JSON the device accepts/returns for a pad fileId.
    Defaults match the device's factory state for an empty pad.

    `sample_end=None` omits the field from the JSON so the device uses
    the full sample length automatically.
    """

    playmode: str = "oneshot"      # "oneshot" | "key" | "legato"
    sample_start: int = 0
    sample_end: int | None = None  # omitted when None
    attack: int = 0                # 0..255
    # release=None → auto-pair with playmode (oneshot↔255, key↔15, legato↔255).
    # Pass an int to override; the device UI always writes playmode+release atomically,
    # so key-mode gating silently fails without the paired release value.
    release: int | None = None     # 0..255 or None for playmode-matched default
    pitch: float = 0.0             # semitones, -12.0..12.0
    amplitude: int = 100           # 0..100
    pan: int = 0                   # -16..16
    mutegroup: bool = False
    time_mode: str = "off"         # "off" | "bar" | "bpm"
    # NOTE: time_bpm is NOT stored in pad metadata (confirmed 2026-04-23 capture).
    # Source BPM is encoded in the WAV file at upload time (smpl chunk or TE proprietary).
    # This field is reserved for future WAV-encoding support; to_json() does not emit it.
    time_bpm: float | None = None
    midi_channel: int = 0          # 0..15 — "midi.channel" in device JSON

    def __post_init__(self) -> None:
        if self.playmode not in _VALID_PLAYMODES:
            raise ValueError(
                f"playmode {self.playmode!r} must be one of {sorted(_VALID_PLAYMODES)}"
            )
        # Auto-pair release with playmode when caller didn't specify one.
        # Legato has no UI-emitted pair, so fall back to 255 (safe default for unknown context).
        if self.release is None:
            self.release = _PLAYMODE_DEFAULT_RELEASE[self.playmode] or 255
        if self.sample_start < 0:
            raise ValueError(f"sample_start {self.sample_start} must be >= 0")
        if self.sample_end is not None and self.sample_end <= self.sample_start:
            raise ValueError(
                f"sample_end {self.sample_end} must be > sample_start {self.sample_start}"
            )
        if not (0 <= self.attack <= 255):
            raise ValueError(f"attack {self.attack} must be 0..255")
        if not (0 <= self.release <= 255):
            raise ValueError(f"release {self.release} must be 0..255")
        if not (0 <= self.amplitude <= 100):
            raise ValueError(f"amplitude {self.amplitude} must be 0..100")
        if not (-16 <= self.pan <= 16):
            raise ValueError(f"pan {self.pan} must be -16..16")
        if self.time_mode not in _VALID_TIME_MODES:
            raise ValueError(
                f"time_mode {self.time_mode!r} must be one of {sorted(_VALID_TIME_MODES)}"
            )
        if not (0 <= self.midi_channel <= 15):
            raise ValueError(f"midi_channel {self.midi_channel} must be 0..15")

    def to_json(self, slot: int) -> bytes:
        """Serialize to the ASCII JSON blob the device expects."""
        d: dict = {"sym": slot}
        d["sound.playmode"] = self.playmode
        d["sample.start"] = self.sample_start
        if self.sample_end is not None:
            d["sample.end"] = self.sample_end
        d["envelope.attack"] = self.attack
        d["envelope.release"] = self.release
        d["sound.pitch"] = round(self.pitch, 2)
        d["sound.amplitude"] = self.amplitude
        d["sound.pan"] = self.pan
        d["sound.mutegroup"] = self.mutegroup
        d["time.mode"] = self.time_mode
        # time.bpm is not emitted — device does not store BPM in pad metadata.
        d["midi.channel"] = self.midi_channel
        return json.dumps(d, separators=(",", ":")).encode("ascii")


# ──────────────────────────────────────────────────────────────────────
# Pad assignment (reverse-engineered from Sample Tool captures)
# ──────────────────────────────────────────────────────────────────────

def pad_file_id(project: int, group: str, pad_num: int) -> int:
    """Compute the device fileId for a (project, group, pad_num).

    - project: 1-indexed project number (1..)
    - group: 'A' | 'B' | 'C' | 'D'
    - pad_num: 1..12, visual position top-to-bottom left-to-right.
      (Use commands.PAD_LABEL_TO_NUM to convert from physical pad labels.)
    """
    if project < 1:
        raise ValueError(f"project {project} must be >= 1")
    if group not in "ABCD":
        raise ValueError(f"group {group!r} must be one of A, B, C, D")
    if not (1 <= pad_num <= 12):
        raise ValueError(f"pad_num {pad_num} must be 1..12")

    group_index = "ABCD".index(group)
    return (
        PAD_BASE
        + (project - 1) * PAD_PROJECT_STRIDE
        + group_index * PAD_GROUP_STRIDE
        + pad_num
    )


def build_metadata_set(file_id: int, json_bytes: bytes) -> bytes:
    """Metadata SET payload (cmd=5 implied).

    Layout: `07 01 [file_id:u16 BE] [json_bytes] 00`

    `json_bytes` should be ASCII JSON like `{"sym":1}`. The null terminator
    is appended automatically — do NOT include it in `json_bytes`.
    """
    if not (0 <= file_id < 2**16):
        raise ValueError(f"file_id {file_id} must fit in u16")
    if b"\0" in json_bytes:
        raise ValueError("json_bytes must not contain null bytes")
    return (
        bytes([TE_SYSEX_FILE_METADATA, TE_SYSEX_FILE_METADATA_SET])
        + struct.pack(">H", file_id)
        + json_bytes
        + b"\0"
    )


def build_assign_pad(
    project: int,
    group: str,
    pad_num: int,
    slot: int,
    params: PadParams | None = None,
) -> bytes:
    """Build the unpacked payload that assigns a pad to a library slot.

    With `params=None` writes only `{"sym":<slot>}` (slot assignment only).
    With `params` provided, writes the full playback parameter JSON including
    playmode, trim points, envelope, pitch, pan, amplitude, mutegroup, and
    time mode — all in one SysEx message.
    """
    if slot < 0:
        raise ValueError(f"slot {slot} must be >= 0")
    file_id = pad_file_id(project, group, pad_num)
    if params is None:
        json_bytes = f'{{"sym":{slot}}}'.encode("ascii")
    else:
        json_bytes = params.to_json(slot)
    return build_metadata_set(file_id, json_bytes)


def pad_num_from_label(label: str) -> int:
    """Translate a physical pad label ('7', '.', 'ENTER', …) to its pad_num."""
    if label not in PAD_LABEL_TO_NUM:
        raise ValueError(
            f"unknown pad label {label!r}; valid: {sorted(PAD_LABEL_TO_NUM)}"
        )
    return PAD_LABEL_TO_NUM[label]


# ──────────────────────────────────────────────────────────────────────
# Sample-slot metadata (fileId = slot number)
#
# Separate schema from pad metadata. 17 fields total, discovered 2026-04-24
# from a paginated FILE_METADATA_GET on slot 100. Writes are partial-field:
# only emit what the caller set; unlisted fields stay at their current value.
# Notable fields:
#   sound.bpm    — source BPM for stretch math (stretch = project_bpm / source)
#   sound.bars   — bar count for BAR mode (device clamps to powers of 2)
#   sound.rootnote — MIDI root note (default 60, affects key/legato pitch track)
#   sound.loopstart/loopend — sample-index loop points, -1 = none
# Read-only fields (set at upload time): channels, samplerate, format, crc.
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SampleParams:
    """Partial sample-slot metadata for FILE_METADATA_SET at fileId = slot.

    All fields optional; None means "don't emit, leave current value alone".
    This is deliberately a partial-write schema — unlike PadParams which
    writes a full snapshot, sample-slot writes merge into existing state.
    """
    bpm: float | None = None          # sound.bpm — source BPM
    bars: float | None = None         # sound.bars — device may clamp to 1/2/4/8/16
    playmode: str | None = None       # sound.playmode — "oneshot"/"key"/"legato"
    time_mode: str | None = None      # time.mode — "off"/"bar"/"bpm"
    rootnote: int | None = None       # sound.rootnote — MIDI 0..127, default 60
    amplitude: int | None = None      # sound.amplitude — 0..100
    pan: int | None = None            # sound.pan — -16..16
    pitch: float | None = None        # sound.pitch — semitones, -12..12
    loopstart: int | None = None      # sound.loopstart — sample index, -1 = none
    loopend: int | None = None        # sound.loopend — sample index, -1 = none
    attack: int | None = None         # envelope.attack — 0..255
    release: int | None = None        # envelope.release — 0..255
    name: str | None = None           # display name (20 chars max)

    def __post_init__(self) -> None:
        if self.playmode is not None and self.playmode not in _VALID_PLAYMODES:
            raise ValueError(
                f"playmode {self.playmode!r} must be one of {sorted(_VALID_PLAYMODES)}"
            )
        if self.time_mode is not None and self.time_mode not in _VALID_TIME_MODES:
            raise ValueError(
                f"time_mode {self.time_mode!r} must be one of {sorted(_VALID_TIME_MODES)}"
            )
        if self.rootnote is not None and not (0 <= self.rootnote <= 127):
            raise ValueError(f"rootnote {self.rootnote} must be 0..127")
        if self.amplitude is not None and not (0 <= self.amplitude <= 100):
            raise ValueError(f"amplitude {self.amplitude} must be 0..100")
        if self.pan is not None and not (-16 <= self.pan <= 16):
            raise ValueError(f"pan {self.pan} must be -16..16")
        if self.attack is not None and not (0 <= self.attack <= 255):
            raise ValueError(f"attack {self.attack} must be 0..255")
        if self.release is not None and not (0 <= self.release <= 255):
            raise ValueError(f"release {self.release} must be 0..255")
        if self.loopstart is not None and self.loopstart < -1:
            raise ValueError(f"loopstart {self.loopstart} must be >= -1")
        if self.loopend is not None and self.loopend < -1:
            raise ValueError(f"loopend {self.loopend} must be >= -1")
        if self.name is not None and len(self.name.encode("ascii")) > 20:
            raise ValueError(f"name {self.name!r} exceeds 20 ASCII bytes")
        if self.bpm is not None and not (1.0 <= self.bpm <= 200.0):
            # Observed 2026-04-24: 180 ACK, 240 rejected with status=1.
            # Exact upper bound uncertain; conservative cap at 200.
            raise ValueError(f"bpm {self.bpm} must be 1.0..200.0 (device rejects higher)")

    def is_empty(self) -> bool:
        """Return True if every field is None (nothing to write)."""
        return all(getattr(self, f.name) is None for f in self.__dataclass_fields__.values())

    def to_json(self) -> bytes:
        """Serialize only the set fields to the JSON blob the device expects."""
        d: dict = {}
        if self.playmode is not None:
            d["sound.playmode"] = self.playmode
        if self.amplitude is not None:
            d["sound.amplitude"] = self.amplitude
        if self.pan is not None:
            d["sound.pan"] = self.pan
        if self.pitch is not None:
            d["sound.pitch"] = round(self.pitch, 2)
        if self.rootnote is not None:
            d["sound.rootnote"] = self.rootnote
        if self.time_mode is not None:
            d["time.mode"] = self.time_mode
        if self.bpm is not None:
            d["sound.bpm"] = round(self.bpm, 2)
        if self.bars is not None:
            d["sound.bars"] = round(self.bars, 2)
        if self.loopstart is not None:
            d["sound.loopstart"] = self.loopstart
        if self.loopend is not None:
            d["sound.loopend"] = self.loopend
        if self.attack is not None:
            d["envelope.attack"] = self.attack
        if self.release is not None:
            d["envelope.release"] = self.release
        if self.name is not None:
            d["name"] = self.name
        return json.dumps(d, separators=(",", ":")).encode("ascii")


def build_slot_metadata_set(slot: int, params: SampleParams) -> bytes:
    """Build the FILE_METADATA_SET payload for a sample-slot write.

    `slot` is the sample library slot (1..65535) — fileId is the slot number
    directly, not a pad fileId. Writes merge into the slot's existing record.
    """
    if not (1 <= slot <= 0xFFFF):
        raise ValueError(f"slot {slot} must be 1..65535")
    if params.is_empty():
        raise ValueError("SampleParams is empty — nothing to write")
    return build_metadata_set(slot, params.to_json())
