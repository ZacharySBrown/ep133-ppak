"""
TE SysEx protocol constants.

Direct port of phones24/src/lib/midi/constants.ts. Plus a handful of
write-path constants reverse-engineered from Garrett's captures, clearly
labeled.
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────
# Ported verbatim from phones24/constants.ts
# ──────────────────────────────────────────────────────────────────────

# Status codes
STATUS_OK = 0
STATUS_ERROR = 1
STATUS_COMMAND_NOT_FOUND = 2
STATUS_BAD_REQUEST = 3
STATUS_SPECIFIC_ERROR_START = 16
STATUS_SPECIFIC_SUCCESS_START = 64

# Frame flag bits (in byte 6)
BIT_IS_REQUEST = 0x40
BIT_REQUEST_ID_AVAILABLE = 0x20

# MIDI framing
MIDI_SYSEX_START = 0xF0
MIDI_SYSEX_END = 0xF7

# TE manufacturer ID (in bytes 1–3)
TE_MIDI_ID_0 = 0x00
TE_MIDI_ID_1 = 0x20
TE_MIDI_ID_2 = 0x76

# TE SysEx marker (byte 5)
MIDI_SYSEX_TE = 0x40

# Universal MIDI identity request
IDENTITY_SYSEX = bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])

# Top-level commands (byte 8)
TE_SYSEX_GREET = 1
TE_SYSEX_FILE = 5

# Sub-commands under TE_SYSEX_FILE
TE_SYSEX_FILE_INIT = 1
TE_SYSEX_FILE_METADATA_GET = 2
TE_SYSEX_FILE_GET = 3
TE_SYSEX_FILE_LIST = 4
TE_SYSEX_FILE_METADATA = 7
TE_SYSEX_FILE_INFO = 11

# Sub-sub-commands under FILE_GET
TE_SYSEX_FILE_GET_TYPE_INIT = 0
TE_SYSEX_FILE_GET_TYPE_DATA = 1

# File type bit
TE_SYSEX_FILE_FILE_TYPE_FILE = 1


# ──────────────────────────────────────────────────────────────────────
# Reverse-engineered from Garrett's captures (not in phones24)
# ──────────────────────────────────────────────────────────────────────
#
# Under TE_SYSEX_FILE, a sub-command byte of 0x02 is used for writes.
# Unlike phones24's read-only FILE_METADATA_GET (=2), the byte immediately
# after selects a phase:
#
#   0x02 0x00 …  → create-file + metadata (header + filename + JSON)
#   0x02 0x01 …  → data chunk (page number + raw PCM bytes)
#
# Upload flow (from captures):
#
#   (1) GREET                            cmd=1
#   (2) FILE_INIT (flags=0x01)           cmd=5 sub=01
#   (3) FILE_PUT_META                    cmd=5 sub=02 00 [header]
#   (4..N)  FILE_PUT_DATA page=0..N-1    cmd=5 sub=02 01 [page] [pcm]
#   (N+1) FILE_PUT_DATA page=N (empty)   cmd=5 sub=02 01 [N] (terminator)
#   (N+2) FILE_INFO                      cmd=5 sub=0B [nodeId]
#
# The FILE_INIT flags byte is 0x00 in phones24's read usage and 0x01 in
# writes. Guess: bit 0 = "writable".
TE_SYSEX_FILE_PUT = 2
TE_SYSEX_FILE_PUT_PHASE_META = 0
TE_SYSEX_FILE_PUT_PHASE_DATA = 1
TE_SYSEX_FILE_INIT_FLAG_WRITE = 0x01

# Metadata SET: the write-path complement to phones24's FILE_METADATA_GET (= 0x02).
# Wire format: `07 01 [fileId:u16 BE] [json-bytes] 00`  (null-terminated JSON).
TE_SYSEX_FILE_METADATA_SET = 1

# Pad fileId formula — validated against 8 captures across projects 1/2/3/5/7,
# groups A/B/C/D, pad_nums 1/3/5/7/10/12.
#
# padFileId = PAD_BASE + (project - 1) * PROJECT_STRIDE + group_index * GROUP_STRIDE + pad_num
#
# - group_index: A=0, B=1, C=2, D=3
# - pad_num: 1..12, visual position top-to-bottom, left-to-right on the EP-133 grid.
#   Labels on the pads themselves are NOT the pad_num — see PAD_LABEL_TO_NUM below.
# - pad_num = 0 is the group's cursor/metadata file (not assignable).
PAD_BASE = 3200
PAD_PROJECT_STRIDE = 1000
PAD_GROUP_STRIDE = 100

# Map from the physical EP-133 pad label to its 1-indexed visual position.
# Pads are laid out in a phone-keypad pattern:
#   row 0 (top):    7 8 9
#   row 1:          4 5 6
#   row 2:          1 2 3
#   row 3 (bottom): . 0 ENTER
# Visual position counts top-to-bottom, left-to-right starting at 1.
PAD_LABEL_TO_NUM = {
    "7": 1, "8": 2, "9": 3,
    "4": 4, "5": 5, "6": 6,
    "1": 7, "2": 8, "3": 9,
    ".": 10, "0": 11, "ENTER": 12, "E": 12,  # "E" is an alias for ENTER
}

# Maximum byte payload per data chunk after 7-bit packing fits inside the
# device's (500-byte packed / 510-byte on-wire) envelope. 433 raw bytes →
# header(4) + 433 = 437 raw → 437 + ceil(437/7) = 500 packed.
CHUNK_BYTES = 433

# Metadata-create header constants. Verified byte-identical against Garrett's
# captures (slot 1 always). Left as named constants so firmware changes are
# easy to grep.
#
# Observed metadata-create header layout (after `02 00` phase bytes):
#   [META_C0:u8=0x05]
#   [slot:u16 BE]              ← was mislabeled META_C1(0x00)+slot(u8); it's a u16
#   [META_C3:u16 BE=0x03E8]   [size:u32 BE]
#   [filename null-terminated ASCII]
#   [JSON metadata, no null terminator]
#
# slot is the target library position (u16 BE, 1..65535). Garrett's captures
# always used slot=1, so the high byte was 0x00 and appeared to be a constant
# labeled META_C1. Confirmed u16 by TE web tool capture of slot=709 (0x02C5).
# Values >127 are safe in the payload because bytes are 7-bit packed at the
# MIDI frame level. `size` is the raw-PCM byte length (big-endian).
META_C0 = 0x05
META_C3 = 0x03E8  # written as u16 BE
