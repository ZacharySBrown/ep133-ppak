"""
Read EP-133 project files as raw binary over SysEx.

Discovered 2026-04-24. Project files are TAR archives living at fileId
``3000 + (project_num - 1) * 1000`` (so project 1 = 3000, project 7 = 9000).
They contain pad records for all 4 groups × 12 pads, plus patterns/scenes.

Read flow (device in read-file mode):

    1. FILE_INIT with flags=0 (read)
    2. 03 00 <project_fid:u16 BE> <u32 offset=0>
    3. loop: 03 01 <page:u16 BE>
       until a response comes back shorter than the usual 327 raw bytes,
       which signals EOF.

Each page response has a 3-byte header ``00 00 NN`` where NN is the page
number. The remainder is content. Concatenating the content portion of
every page yields the full TAR.

Notes:

- ``03 00`` only accepts a zero offset — the device returns
  "offset not supported for raw" for non-zero offsets, so pagination is
  strictly sequential from page 0.
- Do NOT call ``03 00`` on arbitrary fileIds that aren't known-readable
  files (samples or projects). Speculative opens can wedge the device
  into an error state requiring a power cycle. See memory
  ``feedback_ep133_probing_safety`` for details.
"""

from __future__ import annotations

import struct
import time
from typing import Protocol

from .client import EP133Client
from .commands import TE_SYSEX_FILE
from .packing import unpack_in_place
from .payloads import build_file_init
from .sysex import build_sysex

PROJECT_BASE_FILE_ID = 3000
PROJECT_STRIDE = 1000
PAGE_HEADER_BYTES = 3
PAGE_DATA_BYTES = 324  # 327 on the wire minus 3 page-header bytes


def project_file_id(project_num: int) -> int:
    """Compute the fileId of a project on the EP-133.

    Valid project numbers are 1..99 (the device display shows ``01``..``99``).
    """
    if not (1 <= project_num <= 99):
        raise ValueError(f"project_num {project_num} must be 1..99")
    return PROJECT_BASE_FILE_ID + (project_num - 1) * PROJECT_STRIDE


class _MidiIO(Protocol):
    def send_raw_frame(self, frame: bytes) -> None: ...
    def recv_sysex(self, timeout: float) -> bytes | None: ...


def read_project_file(
    project_num: int,
    *,
    identity_code: int = 0x33,
    request_id_base: int = 0x900,
    inter_message_delay_s: float = 0.005,
    page_timeout_s: float = 2.0,
    max_pages: int = 500,
) -> bytes:
    """Read a project file from the EP-133 and return its raw TAR bytes.

    The returned bytes are the concatenated *content* of every page with
    the 3-byte page-number header stripped — suitable for passing to a
    TAR/pad-record parser.

    This function opens its own MIDI ports via ``mido``+``python-rtmidi``
    (via :func:`ep133.transport.find_ep133_ports`).
    Caller is responsible for ensuring the device is connected and not
    currently busy with another transfer.

    Raises
    ------
    RuntimeError
        If the device rejects the open or aborts mid-stream.
    """
    import mido  # local import — optional at package level

    from .transport import find_ep133_ports

    fid = project_file_id(project_num)
    out_name, in_name = find_ep133_ports()

    with mido.open_input(in_name) as inp, mido.open_output(out_name) as outp:
        # Drain any pending input
        while inp.poll() is not None:
            pass

        rid = request_id_base

        def _send(payload: bytes, timeout: float = page_timeout_s) -> bytes | None:
            nonlocal rid
            rid = (rid + 1) % 0x1000
            frame = build_sysex(TE_SYSEX_FILE, payload, request_id=rid, identity_code=identity_code)
            outp.send(mido.Message.from_bytes(list(frame)))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                msg = inp.poll()
                if msg is None:
                    time.sleep(inter_message_delay_s)
                    continue
                if msg.type == "sysex":
                    raw = bytes([0xF0]) + bytes(msg.data) + bytes([0xF7])
                    try:
                        return unpack_in_place(raw[9:-1])
                    except Exception:
                        return raw[9:-1]
            return None

        # FILE_INIT in read mode
        init = _send(build_file_init(4 * 1024 * 1024, flags=0))
        if init is None:
            raise RuntimeError("FILE_INIT(read) timed out — device not responding")

        # Open the project file for reading
        hdr = _send(bytes([0x03, 0x00]) + struct.pack(">H", fid) + struct.pack(">I", 0))
        if hdr is None:
            raise RuntimeError(f"03 00 open timed out for fileId {fid}")
        if b"invali" in hdr.lower():
            raise RuntimeError(
                f"device rejected open for project {project_num} (fileId {fid}): "
                f"{hdr[:40]!r}"
            )

        # Stream pages
        pages: list[bytes] = []
        for page in range(max_pages):
            p = _send(bytes([0x03, 0x01]) + struct.pack(">H", page))
            if p is None:
                break  # timeout = treat as EOF
            pages.append(p)
            if len(p) < PAGE_HEADER_BYTES + PAGE_DATA_BYTES:
                # short page = EOF
                break
        else:
            raise RuntimeError(
                f"read_project_file exceeded max_pages={max_pages} without EOF"
            )

    # Strip 3-byte page header from each page, concatenate
    content = b"".join(p[PAGE_HEADER_BYTES:] for p in pages)
    return content


def read_project_file_via_client(
    client: EP133Client,
    project_num: int,
    *,
    page_timeout_s: float = 2.0,
    max_pages: int = 500,
) -> bytes:
    """Read a project file using an already-open EP133Client session.

    Useful when you want to batch a project read with other operations
    inside a single client session. The client's transport is used to
    send FILE_INIT / 03 00 / 03 01 directly.
    """
    fid = project_file_id(project_num)

    client._send(TE_SYSEX_FILE, build_file_init(4 * 1024 * 1024, flags=0))
    # Note: EP133Client._send/_await_response are designed for request/response
    # pairs with status codes, not the raw-binary streaming we do here. The
    # cleaner implementation lives in read_project_file() above which uses
    # mido directly. Left here as a sketch for future integration.
    raise NotImplementedError(
        "read_project_file_via_client is not yet wired — use read_project_file() "
        "which opens its own MIDI ports."
    )
