"""
.ppak archive writer for the EP-133 K.O. II.

A .ppak is a ZIP archive containing one project, its samples, and metadata.
This module patches a real Sample Tool backup as a base, modifies only the
bytes that need to change, and repacks — guaranteeing format conformance.

See PROTOCOL.md §9 for the .ppak format specification.

Quick start:

    from ep133.ppak import build_from_base, get_sample_length_frames, PRESETS

    sample_length = get_sample_length_frames("real_backup.ppak")
    spec = PRESETS["matrix_tight"](sample_length)
    build_from_base("real_backup.ppak", "out.ppak", spec)
"""

from .writer import (
    DEFAULT_BLANK_PAD,
    PAD_RECORD_SIZE,
    PRESETS,
    build_from_base,
    encode_bpm_override,
    find_pad_record_offsets,
    get_sample_length_frames,
    patch_meta_timestamp,
    patch_pad_record,
    patch_tar,
    preset_matrix,
    preset_matrix_tight,
    preset_mvp,
    preset_mvp_override,
)

__all__ = [
    "DEFAULT_BLANK_PAD",
    "PAD_RECORD_SIZE",
    "PRESETS",
    "build_from_base",
    "encode_bpm_override",
    "find_pad_record_offsets",
    "get_sample_length_frames",
    "patch_meta_timestamp",
    "patch_pad_record",
    "patch_tar",
    "preset_matrix",
    "preset_matrix_tight",
    "preset_mvp",
    "preset_mvp_override",
]
