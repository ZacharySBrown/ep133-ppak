"""
Parse EP-133 project-file TAR archives to locate and decode per-pad records.

A project file (read via :mod:`ep133.project_reader`) is a
TAR-like archive. Each pad lives at a 512-byte-aligned block with a header
naming it ``pads/{a|b|c|d}/p{NN}`` (though TAR names can be slightly mangled
by the device's internal formatting — null bytes interspersed).

The 27-byte pad record starts 512 bytes into each pad block. Byte offsets
below are relative to the **record start** (i.e., ``block_offset + 512``).

Per-pad BPM encodings observed 2026-04-24
-----------------------------------------

The device stores per-pad BPM in one of **two encodings**, detectable by
inspecting byte +13:

**Encoding A — Override (byte +13 == 0x80):**
    Used for pads whose BPM was set via the on-device knobY while the pad
    stayed in BPM mode (never cycled through BAR). Bytes +13..+15 form a
    3-byte record:

    - ``+13 = 0x80`` — "has override" flag
    - ``+14 = BPM_byte``
    - ``+15 = precision_flag`` (``0x00`` = low-range, ``0x80`` = high-range)

    Decoded: ``BPM = byte if (precision & 0x80) else byte / 2``.
    Low-range gives 0.5 BPM resolution for values < 128; high-range gives
    1 BPM resolution for values ≥ 128.

**Encoding B — Float32 (byte +13 != 0x80):**
    Used for pads that were toggled between BAR and BPM modes before the
    save, and for default (unset) pads. Bytes +12..+15 form an IEEE-754
    little-endian float32 whose value is ``BPM / 2``.

    Decoded: ``BPM = 2 * float32_le(bytes[12:16])``.

    NOTE: Only ONE data point (pad 6 at BPM=70, stored as 35.001) has
    validated this encoding. Default pads (never user-set) also store
    float32 60.0 here, implying their "default BPM" is 120. This
    interpretation is **tentative** — needs more test points to confirm.

Other fields (from phones24 research; 1-indexed bytes, subtract 1 for 0-indexed)
-------------------------------------------------------------------------------

- Byte 1-2 (0-idx 0-1):   ``soundId`` u16 LE — sample-library slot
- Byte 21 (0-idx 20):     ``timeStretch mode`` (0=off, 1=bpm, 2=bars)
                          May have high bit (0x80) set as "override" flag
- Byte 23 (0-idx 22):     ``playMode`` (0=oneshot, 1=key, 2=legato)
                          May have high bit set

These field interpretations have not been fully verified against our
captures — use with caution.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

PAD_BLOCK_SIZE = 1024              # TAR header (512) + data area (≤512)
TAR_HEADER_SIZE = 512
PAD_RECORD_SIZE = 27               # phones24: pad records are ~27 bytes

OVERRIDE_FLAG_BYTE_OFFSET = 13     # +13 == 0x80 means override encoding
OVERRIDE_VALUE_BYTE_OFFSET = 14
OVERRIDE_PRECISION_BYTE_OFFSET = 15

FLOAT_BPM_OFFSET = 12              # +12..+15 as float32 LE
FLOAT_BPM_LENGTH = 4


@dataclass
class PadRecord:
    """One pad's record extracted from a project TAR."""

    # Location in the enclosing project file
    block_offset: int              # offset of the 512-byte TAR header
    content_offset: int            # block_offset + 512
    name: str                      # TAR filename (may be mangled)

    # Decoded fields
    bpm: Optional[float]           # None if we couldn't decode
    bpm_encoding: str              # "override" or "float32" or "unknown"

    # Raw bytes for further analysis
    raw: bytes                     # first 32 bytes of record
    raw_header_name: bytes = field(repr=False, default=b"")


def _looks_like_pad_name(header_bytes: bytes) -> bool:
    """TAR names get mangled but almost always contain one of ``p01``..``p12``."""
    for n in range(1, 13):
        if f"p{n:02d}".encode() in header_bytes[:100]:
            return True
    return False


def _extract_pad_num(header_bytes: bytes) -> Optional[int]:
    """Return the pad number 1..12 if the TAR name contains ``pNN``, else None."""
    for n in range(1, 13):
        if f"p{n:02d}".encode() in header_bytes[:100]:
            return n
    return None


def decode_bpm(record: bytes) -> tuple[Optional[float], str]:
    """Return (BPM, encoding_tag) from a 32-byte (or longer) pad record.

    encoding_tag is one of ``"override"``, ``"float32"``, or ``"unknown"``.

    See the module docstring for the detection rule and format details.
    """
    if len(record) < FLOAT_BPM_OFFSET + FLOAT_BPM_LENGTH:
        return None, "unknown"

    b13 = record[OVERRIDE_FLAG_BYTE_OFFSET]
    if b13 == 0x80:
        b14 = record[OVERRIDE_VALUE_BYTE_OFFSET]
        b15 = record[OVERRIDE_PRECISION_BYTE_OFFSET]
        if b15 & 0x80:
            return float(b14), "override"     # high-range: value as-is
        return b14 / 2.0, "override"          # low-range: value ÷ 2

    # Fallback: interpret +12..+15 as float32 LE, multiply by 2.
    # Tentative — only validated against one sample (pad 6 at BPM=70).
    try:
        f = struct.unpack_from("<f", record, FLOAT_BPM_OFFSET)[0]
    except struct.error:
        return None, "unknown"
    # Filter obvious junk: NaN / infinities / out-of-range
    import math
    if not math.isfinite(f) or not (0.0 < f < 500.0):
        return None, "unknown"
    return 2.0 * f, "float32"


def find_pad_records(project_tar_bytes: bytes) -> list[PadRecord]:
    """Scan a project file for pad records.

    Walks 512-byte-aligned blocks, treating any block whose header contains
    ``pNN`` (1..12) as a pad entry.
    """
    out: list[PadRecord] = []
    for pos in range(0, len(project_tar_bytes) - TAR_HEADER_SIZE, TAR_HEADER_SIZE):
        hdr = project_tar_bytes[pos : pos + 100]
        pad_num = _extract_pad_num(hdr)
        if pad_num is None:
            continue
        content_start = pos + TAR_HEADER_SIZE
        record_end = min(content_start + 32, len(project_tar_bytes))
        rec = project_tar_bytes[content_start:record_end]
        name = hdr.split(b"\x00")[0].decode("ascii", errors="replace")
        bpm, encoding = decode_bpm(rec)
        out.append(
            PadRecord(
                block_offset=pos,
                content_offset=content_start,
                name=name,
                bpm=bpm,
                bpm_encoding=encoding,
                raw=rec,
                raw_header_name=hdr,
            )
        )
    return out
