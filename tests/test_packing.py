"""
7-bit packing/unpacking round-trip + fixture decoding.
"""

from __future__ import annotations

import os

import pytest

from ep133.packing import (
    crc32,
    pack_to_buffer,
    packed_length,
    unpack_in_place,
)


@pytest.mark.parametrize("n", [0, 1, 6, 7, 8, 13, 14, 15, 433, 1000, 11272])
def test_pack_round_trip_random(n: int):
    data = os.urandom(n)
    packed = pack_to_buffer(data)
    assert len(packed) == packed_length(n)
    unpacked = unpack_in_place(packed)
    assert unpacked == data


def test_pack_specific_byte_patterns():
    # All-MSB-set: every byte gets its high bit packed into the MSB byte
    data = bytes([0xFF] * 7)
    packed = pack_to_buffer(data)
    assert packed == bytes([0x7F, 0x7F, 0x7F, 0x7F, 0x7F, 0x7F, 0x7F, 0x7F])
    assert unpack_in_place(packed) == data

    # All-MSB-clear
    data = bytes([0x00] * 7)
    packed = pack_to_buffer(data)
    assert packed == bytes([0x00] * 8)
    assert unpack_in_place(packed) == data


def test_crc32_empty():
    assert crc32(b"") == 0


def test_crc32_known_vector():
    # "123456789" under CRC-32/ISO-HDLC (poly 0xEDB88320) = 0xCBF43926
    assert crc32(b"123456789") == 0xCBF43926
