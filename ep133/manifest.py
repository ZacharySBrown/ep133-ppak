"""StemForge sample-manifest schema + lookup helpers.

This module mirrors the canonical schema documented in
`stemforge/specs/manifest-spec.md`. It provides:

  - `SampleMeta` / `BatchManifest`  pydantic models (loader-side mirror).
  - `compute_audio_hash`            sha256-first-16-hex of WAV bytes.
  - `find_sidecar` / `find_batch`   filesystem lookups next to a WAV.
  - `resolve_meta`                  the full lookup chain a CLI/skill should call.

Resolution order (highest to lowest): explicit override → sidecar
`.manifest_<hash>.json` next to the WAV → batch `.manifest.json` in the
WAV's directory (matched by `audio_hash`, falling back to filename) →
`None`. The CLI layers its own flags on top of whatever this returns.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PadLabel = Literal["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "ENTER"]
Group = Literal["A", "B", "C", "D"]
TimeMode = Literal["off", "bar", "bpm"]
PlayMode = Literal["oneshot", "key", "legato"]
Stem = Literal["drums", "bass", "vocals", "other", "full"]

SIDECAR_PREFIX = ".manifest_"
SIDECAR_SUFFIX = ".json"
BATCH_FILENAME = ".manifest.json"
HASH_LENGTH = 16  # hex chars from sha256


class SampleMeta(BaseModel):
    """Per-sample metadata (sidecar contents OR a batch entry)."""

    file: str | None = None
    audio_hash: str | None = None

    name: str | None = None

    bpm: float | None = None
    time_mode: TimeMode | None = None
    bars: float | None = None

    playmode: PlayMode | None = None

    source_track: str | None = None
    stem: Stem | None = None
    role: str | None = None

    suggested_group: Group | None = None
    suggested_pad: PadLabel | None = None

    model_config = {"extra": "ignore"}


class BatchManifest(BaseModel):
    """Directory-level manifest. Filename: `.manifest.json` in the dir root."""

    version: int = 1
    track: str | None = None
    bpm: float | None = None
    samples: list[SampleMeta] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


def compute_audio_hash(path: Path, *, length: int = HASH_LENGTH) -> str:
    """Return sha256 of a file's raw bytes, lowercase hex, first `length` chars."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


def sidecar_path_for(wav_path: Path, *, audio_hash: str | None = None) -> Path:
    """Return the expected sidecar path for a given WAV.

    Hash is computed if not supplied.
    """
    h = audio_hash or compute_audio_hash(wav_path)
    return wav_path.parent / f"{SIDECAR_PREFIX}{h}{SIDECAR_SUFFIX}"


def find_sidecar(wav_path: Path) -> Path | None:
    """Return the sidecar path next to `wav_path` if it exists, else None."""
    p = sidecar_path_for(wav_path)
    return p if p.exists() else None


def find_batch(wav_path: Path) -> Path | None:
    """Return the `.manifest.json` in the WAV's directory if it exists."""
    p = wav_path.parent / BATCH_FILENAME
    return p if p.exists() else None


def load_sidecar(wav_path: Path) -> SampleMeta | None:
    """Load the sidecar `SampleMeta` next to a WAV, or None if absent."""
    p = find_sidecar(wav_path)
    if p is None:
        return None
    return SampleMeta.model_validate_json(p.read_text())


def load_batch(manifest_path: Path) -> BatchManifest:
    """Load a `BatchManifest` from a JSON file."""
    return BatchManifest.model_validate_json(manifest_path.read_text())


def lookup_in_batch(batch: BatchManifest, wav_path: Path) -> SampleMeta | None:
    """Find the entry in `batch` matching `wav_path` by hash or filename.

    Hash match is preferred (more robust to renames). Falls back to filename
    matching against `SampleMeta.file`.
    """
    if not batch.samples:
        return None

    target_name = wav_path.name
    target_hash: str | None = None

    by_name = None
    for s in batch.samples:
        if s.audio_hash:
            if target_hash is None:
                target_hash = compute_audio_hash(wav_path)
            if s.audio_hash == target_hash:
                return s
        if s.file and Path(s.file).name == target_name and by_name is None:
            by_name = s

    return by_name


def resolve_meta(
    wav_path: Path,
    *,
    manifest_override: Path | None = None,
    use_sidecar: bool = True,
    use_batch: bool = True,
) -> SampleMeta | None:
    """Resolve metadata for `wav_path` via the standard lookup chain.

    Order:
      1. `manifest_override` if given. Detected as sidecar (single object) or
         batch (object with `samples` list) by content shape.
      2. Sidecar `.manifest_<hash>.json` next to the WAV.
      3. Batch `.manifest.json` in the WAV's directory; entry matched by
         `audio_hash`, falling back to filename.
      4. None.

    Returns the first match. CLI flags layer on top; this function does not
    apply defaults.
    """
    if manifest_override is not None:
        raw = json.loads(manifest_override.read_text())
        if isinstance(raw, dict) and "samples" in raw:
            return lookup_in_batch(BatchManifest.model_validate(raw), wav_path)
        return SampleMeta.model_validate(raw)

    if use_sidecar:
        side = load_sidecar(wav_path)
        if side is not None:
            return side

    if use_batch:
        batch_path = find_batch(wav_path)
        if batch_path is not None:
            entry = lookup_in_batch(load_batch(batch_path), wav_path)
            if entry is not None:
                return entry

    return None


def merge_batch_default_bpm(meta: SampleMeta, batch: BatchManifest) -> SampleMeta:
    """If `meta.bpm` is None and the batch has a default BPM, fill it in.

    Returns a new `SampleMeta`; does not mutate the input.
    """
    if meta.bpm is None and batch.bpm is not None:
        return meta.model_copy(update={"bpm": batch.bpm})
    return meta


__all__ = [
    "BATCH_FILENAME",
    "HASH_LENGTH",
    "SIDECAR_PREFIX",
    "SIDECAR_SUFFIX",
    "BatchManifest",
    "Group",
    "PadLabel",
    "PlayMode",
    "SampleMeta",
    "Stem",
    "TimeMode",
    "compute_audio_hash",
    "find_batch",
    "find_sidecar",
    "load_batch",
    "load_sidecar",
    "lookup_in_batch",
    "merge_batch_default_bpm",
    "resolve_meta",
    "sidecar_path_for",
]
