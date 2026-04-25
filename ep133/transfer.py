"""
Upload orchestration: PCM bytes + name + slot → ordered list of SysEx payloads.

Pure function. No I/O, no request-ID allocation. Keeps the expensive test
(byte-identical reproduction vs. Garrett's captures) deterministic.

The client layer wraps this with request-ID allocation + transport.
"""

from __future__ import annotations

from . import payloads as P
from .commands import TE_SYSEX_FILE, TE_SYSEX_GREET

# 4 MB — what phones24 uses in `getFileList`; Garrett's write captures also use this.
DEFAULT_FILE_INIT_MAX_LEN = 4 * 1024 * 1024


def generate_upload_payloads(
    pcm: bytes,
    name: str,
    channels: int = 1,
    slot: int = 1,
    file_id: int | None = None,  # ignored — kept for backwards compat
) -> list[tuple[int, bytes]]:
    """Build the upload sequence (GREET through terminator) as (command, payload) tuples.

    `slot` is embedded in the FILE_PUT_META header (byte position previously
    labelled META_C2) and is the target library slot for the sample.
    `pcm` must be raw 16-bit LE mono at 46875 Hz — no WAV header.
    """
    messages: list[tuple[int, bytes]] = []

    # (1) Greet
    messages.append((TE_SYSEX_GREET, b""))

    # (2) File init (write mode)
    messages.append((TE_SYSEX_FILE, P.build_file_init(DEFAULT_FILE_INIT_MAX_LEN, flags=1)))

    # (3) Create file + metadata; slot byte controls target library position
    messages.append((TE_SYSEX_FILE, P.build_file_put_meta(name, data_size=len(pcm), channels=channels, slot=slot)))

    # (4..N) Data chunks
    chunks = P.chunk_pcm(pcm)
    for page, chunk in enumerate(chunks):
        messages.append((TE_SYSEX_FILE, P.build_file_put_data(page=page, data=chunk)))

    # (N+1) Terminator: empty data chunk at page len(chunks)
    messages.append((TE_SYSEX_FILE, P.build_file_put_terminator(last_page=len(chunks) - 1)))

    return messages
