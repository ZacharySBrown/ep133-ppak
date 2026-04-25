"""
Byte-identical reproduction of Garrett's kick-01 upload.

This is the gate: if we can generate every payload byte-for-byte from the
PCM data + filename + slot, the protocol is correct.

Request IDs differ per session (random seed in phones24's allocator), so
we compare unpacked *payloads*, not framed bytes. Payloads are the part
we control.
"""

from __future__ import annotations

import pytest

from ep133 import payloads as P
from ep133.commands import CHUNK_BYTES
from ep133.packing import unpack_in_place
from ep133.sysex import parse_sysex


def _payload_of(frame: bytes) -> tuple[int, bytes]:
    """Return (command, unpacked-payload-after-status-byte)."""
    parsed = parse_sysex(frame)
    assert parsed is not None, f"failed to parse frame: {frame.hex()}"
    return parsed.command, parsed.raw_data


def test_fixture_count(garrett_kick_messages):
    # 1 identity + GREET + FILE_INIT + META + 27 data chunks + 1 terminator + 1 finalize = 33
    assert len(garrett_kick_messages) == 33


def test_init_sequence(garrett_kick_messages):
    # Message 0: universal identity request (no TE framing)
    assert garrett_kick_messages[0] == bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])

    # Message 1: TE GREET, empty payload
    cmd, payload = _payload_of(garrett_kick_messages[1])
    assert cmd == 1  # TE_SYSEX_GREET
    assert payload == b""

    # Message 2: TE FILE_INIT, flags=0x01, maxLen=4MB
    cmd, payload = _payload_of(garrett_kick_messages[2])
    assert cmd == 5
    assert payload == P.build_file_init(max_response_length=4 * 1024 * 1024, flags=1)


def test_meta_message_reproduction(garrett_kick_messages):
    """The metadata message encodes name + data size + channels.

    Extract the captured data size (uint32 BE at bytes 7..10 of unpacked
    payload, after `02 00 05 00 01 03 E8`), then rebuild and compare.
    """
    cmd, captured = _payload_of(garrett_kick_messages[3])
    assert cmd == 5

    # Rebuild with the captured values: name="1_kick_01", size=0x2C08, channels=1
    size = int.from_bytes(captured[7:11], "big")
    assert size == 0x2C08

    # Extract the filename (null-terminated ASCII, immediately after the size u32)
    name_start = 11
    null_idx = captured.index(0, name_start)
    name = captured[name_start:null_idx].decode("ascii")
    assert name == "1_kick_01"

    regenerated = P.build_file_put_meta(name=name, data_size=size, channels=1)
    assert regenerated == captured, (
        f"meta mismatch:\n  want: {captured.hex()}\n  got:  {regenerated.hex()}"
    )


def test_data_chunks_reproduction(garrett_kick_messages):
    """Reconstruct the PCM stream from captures, then re-chunk and compare."""
    # Indices 4..31 inclusive: 27 real data pages + 1 empty terminator = 28 messages.
    # Index 32 is finalize.
    data_msgs = garrett_kick_messages[4:32]

    # Rebuild PCM by stripping the 4-byte `02 01 [page:u16]` header off each
    pcm = bytearray()
    for i, frame in enumerate(data_msgs):
        cmd, payload = _payload_of(frame)
        assert cmd == 5
        assert payload[0] == 0x02 and payload[1] == 0x01, (
            f"message {i}: not a PUT_DATA chunk"
        )
        page = int.from_bytes(payload[2:4], "big")
        assert page == i, f"message {i}: page mismatch (got {page})"
        pcm.extend(payload[4:])

    # Last chunk (page 26) was partial; page 27 was the empty terminator. The
    # last real data chunk is index 26 (page 26). Terminator is index 27.
    # Actually we captured 27 frames; 0..25 full + 26 partial + 27 empty terminator.
    # Verify terminator is empty.
    _, terminator = _payload_of(data_msgs[-1])
    assert terminator == P.build_file_put_terminator(last_page=len(data_msgs) - 2)

    # Re-chunk all non-terminator payload data
    real_chunks = []
    for frame in data_msgs[:-1]:
        _, payload = _payload_of(frame)
        real_chunks.append(payload[4:])

    # All but last full == CHUNK_BYTES
    for chunk in real_chunks[:-1]:
        assert len(chunk) == CHUNK_BYTES

    # Regenerate chunks from concatenated PCM using our chunker
    pcm_concat = b"".join(real_chunks)
    regenerated = P.chunk_pcm(pcm_concat)
    assert regenerated == real_chunks

    # And the full payload bytes match
    for i, chunk in enumerate(real_chunks):
        generated = P.build_file_put_data(page=i, data=chunk)
        _, captured = _payload_of(data_msgs[i])
        assert generated == captured


def test_finalize_is_file_info(garrett_kick_messages):
    cmd, payload = _payload_of(garrett_kick_messages[32])
    assert cmd == 5
    # build_file_info(1) = bytes([0x0B, 0x00, 0x01])
    assert payload == P.build_file_info(file_id=1)


def test_full_reproduction(garrett_kick_messages):
    """End-to-end: reconstruct PCM from captures, then regenerate every
    unpacked payload and compare.

    generate_upload_payloads produces GREET..terminator (31 messages).
    The 32nd captured message is FILE_INFO — a separate client-level query,
    not part of the upload sequence. test_finalize_is_file_info covers it.
    """
    # Reconstruct PCM: 27 real data pages at indices 4..30 (page 26 is partial)
    pcm = bytearray()
    data_msgs = garrett_kick_messages[4:31]
    for frame in data_msgs:
        _, payload = _payload_of(frame)
        pcm.extend(payload[4:])

    from ep133.transfer import generate_upload_payloads

    generated = generate_upload_payloads(bytes(pcm), name="1_kick_01", channels=1)

    # Compare against captures 1..31 (skip identity at 0, skip FILE_INFO at 32)
    captured = [_payload_of(m) for m in garrett_kick_messages[1:32]]

    assert len(generated) == len(captured), (
        f"message count mismatch: generated={len(generated)} captured={len(captured)}"
    )

    for i, (gen, cap) in enumerate(zip(generated, captured)):
        assert gen[0] == cap[0], f"message {i}: command mismatch {gen[0]} vs {cap[0]}"
        assert gen[1] == cap[1], (
            f"message {i}: payload mismatch\n"
            f"  want: {cap[1].hex()}\n"
            f"  got:  {gen[1].hex()}"
        )
