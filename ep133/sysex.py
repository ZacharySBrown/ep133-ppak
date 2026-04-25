"""
SysEx framing, request-ID allocation, response parsing.

Port of phones24/src/lib/midi/device.ts (`sendTESysEx`, `parseTeenageSysex`)
and `getNextRequestId` from `utils.ts`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .commands import (
    BIT_IS_REQUEST,
    BIT_REQUEST_ID_AVAILABLE,
    MIDI_SYSEX_END,
    MIDI_SYSEX_START,
    MIDI_SYSEX_TE,
    STATUS_BAD_REQUEST,
    STATUS_COMMAND_NOT_FOUND,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SPECIFIC_ERROR_START,
    STATUS_SPECIFIC_SUCCESS_START,
    TE_MIDI_ID_0,
    TE_MIDI_ID_1,
    TE_MIDI_ID_2,
)
from .packing import pack_to_buffer, packed_length, unpack_in_place


class RequestIdAllocator:
    """Matches phones24's `getNextRequestId`: random seed, +1 mod 4096.

    One allocator per output port. The device doesn't care about the starting
    value — any 12-bit ID is valid, it just echoes it back on responses.
    """

    MODULUS = 4096

    def __init__(self, seed: int | None = None) -> None:
        if seed is None:
            seed = random.randint(0, self.MODULUS - 2)
        self._value = seed % self.MODULUS

    def next(self) -> int:
        self._value = (self._value + 1) % self.MODULUS
        return self._value


def build_sysex(command: int, payload: bytes, request_id: int, identity_code: int = 0) -> bytes:
    """Build a single TE SysEx frame.

    Layout (verbatim from `sendTESysEx`):
        F0 00 20 76 [id] 40 [flags] [reqLo] [cmd] [packed payload] F7

    Flags byte: BIT_IS_REQUEST | BIT_REQUEST_ID_AVAILABLE | ((reqId >> 7) & 0x1F)
    ReqLo byte: reqId & 0x7F
    """
    if not (0 <= request_id < RequestIdAllocator.MODULUS):
        raise ValueError(f"request_id {request_id} out of 12-bit range")
    if not (0 <= command < 0x80):
        raise ValueError(f"command {command} must fit in 7 bits")

    plen = packed_length(len(payload))
    frame = bytearray(10 + plen)

    frame[0] = MIDI_SYSEX_START
    frame[1] = TE_MIDI_ID_0
    frame[2] = TE_MIDI_ID_1
    frame[3] = TE_MIDI_ID_2
    frame[4] = identity_code
    frame[5] = MIDI_SYSEX_TE
    frame[6] = BIT_IS_REQUEST | BIT_REQUEST_ID_AVAILABLE | ((request_id >> 7) & 0x1F)
    frame[7] = request_id & 0x7F
    frame[8] = command
    if plen > 0:
        frame[9 : 9 + plen] = pack_to_buffer(payload)
    frame[-1] = MIDI_SYSEX_END

    return bytes(frame)


@dataclass
class TESysexResponse:
    """Parsed response. Matches phones24's `TESysexMessage`."""

    identity_code: int
    has_request_id: bool
    request_id: int
    is_request: bool
    command: int
    status: int
    status_text: str
    raw_data: bytes  # unpacked payload after status byte


def status_to_string(status: int) -> str:
    if status == STATUS_OK:
        return "ok"
    if status >= STATUS_SPECIFIC_SUCCESS_START:
        return "command-specific-success"
    if status == STATUS_ERROR:
        return "error"
    if status == STATUS_COMMAND_NOT_FOUND:
        return "not-found"
    if status == STATUS_BAD_REQUEST:
        return "bad-request"
    if STATUS_SPECIFIC_ERROR_START <= status < STATUS_SPECIFIC_SUCCESS_START:
        return "command-specific-error"
    return "unknown"


def parse_sysex(frame: bytes) -> TESysexResponse | None:
    """Parse a TE SysEx frame. Returns None if not a valid TE message.

    Port of phones24's `parseTeenageSysex`.
    """
    if (
        len(frame) < 10
        or frame[0] != MIDI_SYSEX_START
        or frame[1] != TE_MIDI_ID_0
        or frame[2] != TE_MIDI_ID_1
        or frame[3] != TE_MIDI_ID_2
        or frame[5] != MIDI_SYSEX_TE
        or frame[-1] != MIDI_SYSEX_END
    ):
        return None

    identity_code = frame[4]
    flags = frame[6]
    is_request = bool(flags & BIT_IS_REQUEST)
    has_request_id = bool(flags & BIT_REQUEST_ID_AVAILABLE)
    request_id = ((flags & 0x1F) << 7) | (frame[7] & 0x7F) if has_request_id else 0
    command = frame[8]

    index = 9
    if is_request:
        status = -1
    else:
        status = frame[index]
        index += 1

    unpacked = unpack_in_place(frame[index:-1])

    return TESysexResponse(
        identity_code=identity_code,
        has_request_id=has_request_id,
        request_id=request_id,
        is_request=is_request,
        command=command,
        status=status,
        status_text=status_to_string(status),
        raw_data=unpacked,
    )
