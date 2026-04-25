"""
7-bit MIDI-safe pack/unpack + CRC32.

Port of phones24/src/lib/midi/utils.ts (`packToBuffer`, `unpackInPlace`,
`crc32`). Byte-verified against Garrett's captures.
"""

from __future__ import annotations


def packed_length(raw_length: int) -> int:
    """Number of output bytes `pack_to_buffer` writes for `raw_length` input bytes.

    Every 7 input bytes produce 8 output bytes (1 MSB + 7 stripped).
    A partial final group still consumes 1 MSB byte + N data bytes.
    """
    if raw_length <= 0:
        return 0
    full_groups, tail = divmod(raw_length, 7)
    return full_groups * 8 + (1 + tail if tail else 0)


def pack_to_buffer(data: bytes) -> bytes:
    """Pack 8-bit `data` into 7-bit-safe form for MIDI SysEx.

    For every group of up to 7 input bytes, emits one MSB byte (carrying the
    high bit of each input byte, bit N ↔ byte N of the group) followed by N
    data bytes each with their top bit cleared.
    """
    out_len = packed_length(len(data))
    out = bytearray(out_len)
    out_index = 1
    msb_index = 0

    for i, byte in enumerate(data):
        position_in_group = i % 7
        out[msb_index] |= (byte >> 7) << position_in_group
        out[out_index] = byte & 0x7F
        out_index += 1
        if position_in_group == 6 and i < len(data) - 1:
            msb_index += 8
            out_index += 1

    return bytes(out)


def unpack_in_place(packed: bytes) -> bytes:
    """Inverse of `pack_to_buffer`. Accepts packed bytes, returns raw bytes."""
    if len(packed) == 0:
        return b""

    out = bytearray()
    msb_index = 0
    bit_index = 0
    read_index = 1
    msb_byte = packed[msb_index]

    while read_index < len(packed):
        msb = (1 if (msb_byte & (1 << bit_index)) else 0) << 7
        data_byte = packed[read_index] & 0x7F
        out.append(msb | data_byte)

        bit_index += 1
        read_index += 1

        if bit_index > 6:
            read_index += 1
            bit_index = 0
            msb_index += 8
            if msb_index >= len(packed):
                break
            msb_byte = packed[msb_index]

    return bytes(out)


def crc32(data: bytes, initial: int = 0) -> int:
    """CRC-32 with polynomial 0xEDB88320. Matches phones24 exactly."""

    def _normalize(value: int) -> int:
        return value if value >= 0 else 0xFFFFFFFF + value + 1

    crc = _normalize(initial) ^ 0xFFFFFFFF

    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xEDB88320) if (crc & 1) else (crc >> 1)

    return _normalize(crc ^ 0xFFFFFFFF)
