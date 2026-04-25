"""
.ppak writer — patch a real Sample Tool backup as a base, modify only the
bytes that need to change, repack.

Why patch-from-real instead of build-from-scratch? Sample Tool's parser is
strict about format details that aren't easy to reproduce exactly (TAR
field-padding conventions, ZIP entry ordering, meta.json timestamps,
absent-on-purpose sub-files). Generating from scratch produces silent
rejections. Cloning a real backup and modifying only the bytes you need
to change guarantees format conformance.

Usage:

    from ep133.ppak.writer import build_from_base, preset_matrix_tight, get_sample_length_frames

    sample_length = get_sample_length_frames("real_backup.ppak")
    spec = preset_matrix_tight(sample_length)
    build_from_base("real_backup.ppak", "out.ppak", spec)
"""

from __future__ import annotations

import json
import os
import struct
import wave
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, Tuple

PAD_RECORD_SIZE = 27
TAR_BLOCK = 512


# Real default-blank pad bytes (verbatim from a fresh Sample Tool backup).
# Used to reset all pads before applying the spec — guarantees the output
# contains exactly what was specified, not stray bindings from the base.
DEFAULT_BLANK_PAD = bytes([
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xf0, 0x42,   # bytes 12-15: float32 LE 120.0
    0x64,                                              # byte 16: volume = 100
    0x00, 0x00, 0x00,                                  # bytes 17-19
    0xff,                                              # byte 20: release = 255
    0x00, 0x00,                                        # bytes 21-22
    0x00,                                              # byte 23: playMode = oneshot
    0x3c,                                              # byte 24: rootNote = 60
    0x00, 0x00,                                        # bytes 25-26
])
assert len(DEFAULT_BLANK_PAD) == PAD_RECORD_SIZE


def encode_bpm_override(bpm: int) -> Tuple[int, int, int, int]:
    """Return (b8, b13, b14, b15) for the override-BPM encoding.

    Verified across three on-device captures:
      BPM=92  → 80 B8 00  → 184/2 = 92  (low-range)
      BPM=100 → 80 C8 00  → 200/2 = 100 (low-range)
      BPM=150 → 80 96 80  → 150        (high-range)
    """
    if bpm < 128:
        return (0x20, 0x80, bpm * 2, 0x00)
    return (0x00, 0x80, bpm, 0x80)


def patch_pad_record(
    record: bytes,
    sample_slot: int,
    sample_length_frames: int,
    bpm: int | None = None,
    bpm_override: bool = False,
    time_mode: str = "off",
) -> bytes:
    """Patch a 27-byte pad record using verified offsets.

    Byte layout (verified by diffing two real Sample Tool backups):
      +1     : slot u8 (sample-library slot 1..255)
      +8..11 : sample length in frames (u32 LE) — REQUIRED, the binding
               is broken without this
      +12..15: BPM float32 LE (when override flag at +13 is NOT 0x80)
      +13..15 (override mode): byte +13 = 0x80, +14 = bpm×2 (low) or bpm (high),
              +15 = 0x00 (low-range, BPM<128) or 0x80 (high-range)

    If bpm_override=False, writes BPM as float32 at +12..+15. If True,
    writes the 3-byte override at +13..+15 (overrides the float32).
    """
    rec = bytearray(record)
    rec[1] = sample_slot & 0xFF
    rec[8:12] = struct.pack("<I", sample_length_frames)

    if bpm is not None:
        if bpm_override:
            _, b13, b14, b15 = encode_bpm_override(bpm)
            rec[13] = b13
            rec[14] = b14
            rec[15] = b15
            rec[12] = 0  # byte +12 stays 0 in override mode
        else:
            rec[12:16] = struct.pack("<f", float(bpm))

    rec[21] = {"off": 0, "bpm": 1, "bar": 2}[time_mode]
    return bytes(rec)


def find_pad_record_offsets(tar_bytes: bytes) -> Dict[Tuple[str, int], int]:
    """Scan the TAR and return {(group, pad_num): data_offset}.

    The data offset is where the 27-byte pad record starts (right after
    the 512-byte TAR header block).

    pad_num here uses the TAR's pNN convention (bottom-up, left-right):
    p01 = "." (bottom-left), p12 = "9" (top-right). DIFFERENT from the
    SysEx pad_num convention (top-down). See PROTOCOL.md §3.1 for the
    translation table.
    """
    offsets: Dict[Tuple[str, int], int] = {}
    pos = 0
    while pos + TAR_BLOCK <= len(tar_bytes):
        header = tar_bytes[pos:pos + TAR_BLOCK]
        if header[:4] == b"\x00\x00\x00\x00":
            break
        name = header[:100].rstrip(b"\x00/").decode("ascii", errors="replace")
        try:
            size = int(header[124:135].rstrip(b"\x00 ") or b"0", 8)
        except ValueError:
            size = 0
        typeflag = chr(header[156]) if header[156] else "0"

        if typeflag in ("0", "\x00") and name.startswith("pads/") and len(name) == len("pads/x/pNN"):
            group = name[5]
            try:
                pad_num = int(name[8:10])
                if group in "abcd" and 1 <= pad_num <= 12:
                    offsets[(group, pad_num)] = pos + TAR_BLOCK
            except ValueError:
                pass

        data_blocks = (size + TAR_BLOCK - 1) // TAR_BLOCK
        pos += TAR_BLOCK + data_blocks * TAR_BLOCK
    return offsets


def patch_tar(tar_bytes: bytes, spec: dict, reset_others: bool = True) -> bytes:
    """Apply the spec to a TAR's pad records, returning new TAR bytes.

    If `reset_others` is True (default), every pad NOT in the spec is
    reset to DEFAULT_BLANK_PAD first — guaranteeing the output project
    contains exactly the bindings specified, no more.

    Each cfg in spec must include `slot` and `length` (frames).
    """
    out = bytearray(tar_bytes)
    offsets = find_pad_record_offsets(tar_bytes)

    if reset_others:
        for key, off in offsets.items():
            if key not in spec:
                out[off:off + PAD_RECORD_SIZE] = DEFAULT_BLANK_PAD

    for (group, pad_num), cfg in spec.items():
        key = (group, pad_num)
        if key not in offsets:
            continue
        off = offsets[key]
        new = patch_pad_record(
            DEFAULT_BLANK_PAD,
            sample_slot=cfg["slot"],
            sample_length_frames=cfg["length"],
            bpm=cfg.get("bpm"),
            bpm_override=cfg.get("bpm_override", False),
            time_mode=cfg.get("time_mode", "off"),
        )
        out[off:off + PAD_RECORD_SIZE] = new

    return bytes(out)


# --------------------------------------------------------------- presets

def preset_mvp(sample_length: int) -> dict:
    """One pad: C-01 → slot 100, BPM 120 (float32 mode, no override).

    Mirrors what Sample Tool does on assignment: slot + length, BPM as
    float32 at +12..+15. The minimum viable test for the format.
    """
    return {("c", 1): {"slot": 100, "length": sample_length, "bpm": 120, "time_mode": "off"}}


def preset_mvp_override(sample_length: int) -> dict:
    """Same as mvp but uses override-BPM encoding at bytes 13-15."""
    return {
        ("c", 1): {
            "slot": 100, "length": sample_length, "bpm": 120,
            "bpm_override": True, "time_mode": "bpm",
        },
    }


def preset_matrix(sample_length: int) -> dict:
    """12-pad BPM matrix using override encoding (BPMs 60-200).

    Wide range. Aggressive stretching at low BPMs may produce 'blip'
    playback when the device's bar-quantization clamps short samples.
    Use preset_matrix_tight for cleaner musical results.
    """
    pads = [
        (1,  60), (2,  80), (3, 100), (4, 120),
        (5, 130), (6, 140), (7, 150), (8, 160),
        (9, 170), (10, 180), (11, 190), (12, 200),
    ]
    return {
        ("c", n): {
            "slot": 100, "length": sample_length, "bpm": bpm,
            "bpm_override": True, "time_mode": "bpm",
        }
        for n, bpm in pads
    }


def preset_matrix_tight(sample_length: int) -> dict:
    """12-pad BPM matrix in 120-180 range — avoids the aggressive
    compression that produces 'blip' playback when source_bpm
    is much lower than project_bpm.
    """
    pads = [
        (1, 120), (2, 125), (3, 130), (4, 135),
        (5, 140), (6, 145), (7, 150), (8, 155),
        (9, 160), (10, 165), (11, 170), (12, 180),
    ]
    return {
        ("c", n): {
            "slot": 100, "length": sample_length, "bpm": bpm,
            "bpm_override": True, "time_mode": "bpm",
        }
        for n, bpm in pads
    }


PRESETS = {
    "mvp": preset_mvp,
    "mvp_override": preset_mvp_override,
    "matrix": preset_matrix,
    "matrix_tight": preset_matrix_tight,
}


# --------------------------------------------------------------- meta + io

def patch_meta_timestamp(meta_json_bytes: bytes) -> bytes:
    """Refresh `generated_at` to current ms-precision UTC. Other fields
    stay as Sample Tool emitted them (device_sku, base_sku, author,
    pak_type — all preserved).
    """
    meta = json.loads(meta_json_bytes.decode("utf-8"))
    now = datetime.now(timezone.utc)
    meta["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    return json.dumps(meta, indent=2).encode("utf-8")


def get_sample_length_frames(base_path: str) -> int:
    """Read the first WAV inside a .ppak and return its frame count."""
    with zipfile.ZipFile(base_path, "r") as zf:
        wav_entries = [
            i for i in zf.infolist()
            if i.filename.startswith("/sounds/") and i.filename.endswith(".wav")
        ]
        if not wav_entries:
            raise RuntimeError("base has no WAV in /sounds/")
        wav_data = zf.read(wav_entries[0].filename)
    with wave.open(BytesIO(wav_data), "rb") as wf:
        return wf.getnframes()


def build_from_base(
    base_path: str,
    output_path: str,
    spec: dict,
    refresh_meta: bool = True,
) -> dict:
    """Build a new .ppak by patching pad records in the base's project TAR.

    The base ZIP is decompressed entry-by-entry, the project TAR is
    patched in-memory, and everything is re-zipped preserving the
    original entry order and per-entry attributes.

    Returns a summary dict:
        {"path": str, "project_tar": str, "configured_pads": list[dict]}
    """
    if not os.path.exists(base_path):
        raise FileNotFoundError(f"base file not found: {base_path}")

    with zipfile.ZipFile(base_path, "r") as base_zf:
        info_list = base_zf.infolist()
        project_entries = [
            i for i in info_list
            if "/projects/" in i.filename and i.filename.endswith(".tar")
        ]
        if not project_entries:
            raise RuntimeError("base has no /projects/*.tar entry")
        project_info = project_entries[0]

        entries = []
        for info in info_list:
            data = base_zf.read(info.filename)
            if info.filename == project_info.filename:
                data = patch_tar(data, spec)
            elif info.filename.endswith("/meta.json") and refresh_meta:
                data = patch_meta_timestamp(data)
            entries.append((info, data))

        with zipfile.ZipFile(output_path, "w") as out_zf:
            for info, data in entries:
                now = datetime.now()
                new_info = zipfile.ZipInfo(
                    filename=info.filename,
                    date_time=(now.year, now.month, now.day,
                               now.hour, now.minute, now.second),
                )
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                new_info.create_system = info.create_system
                out_zf.writestr(new_info, data)

    return {
        "path": output_path,
        "project_tar": project_info.filename,
        "configured_pads": [
            {"group": g.upper(), "pad_num": n, **cfg}
            for (g, n), cfg in sorted(spec.items())
        ],
    }
