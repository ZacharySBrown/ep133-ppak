#!/usr/bin/env python3
"""
ep133_capture_reference.py — Capture a reference `.ppak` from a live EP-133.

Reads a project's TAR over USB-MIDI SysEx via
:func:`ep133.project_reader.read_project_file`, wraps it in a ZIP container
with `meta.json`, and writes a standards-compliant `.ppak` that downstream
pipelines can use as a template.

The captured `.ppak` is the single source of truth for:

  * Per-pad default record bytes (callers patch `sample_slot`, `bpm`,
    `play_mode`, `time_stretch_*` but preserve everything else)
  * `settings` file (222 bytes — only the BPM at bytes 4..7 is patched)
  * `device_sku` and `base_sku` in `meta.json` (must match the target device)

Usage::

    uv run python tools/ep133_capture_reference.py \\
        --project 1 \\
        --out tests/fixtures/reference.ppak

Common errors:

  * "EP-133 MIDI ports not found" → connect the device via USB.
  * "device rejected open for project N" → that project slot is empty.
    Initialise a project on-device first (any minimal project works), then
    re-run.
  * "FILE_INIT(read) timed out" → device is busy with another transfer or
    is in an error state. Power-cycle the device and retry.

The pure helpers (`build_meta`, `validate_project_tar`, `wrap_tar_as_ppak`)
are importable without any MIDI dependencies — the `ep133.transport` /
`ep133.project_reader` imports are deferred into :func:`capture` so that
unit tests can exercise the helpers on machines without MIDI hardware or
the optional `mido` / `python-rtmidi` stack installed.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Repo root on sys.path so `ep133` resolves whether this script is run
# directly or via `uv run`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Default device metadata. `device_sku` is patched at runtime if the captured
# project's TAR carries a recognisable serial; otherwise we keep the default
# (which matches the EP-133 we have on hand at TE032AS001).
DEFAULT_DEVICE_SKU = "TE032AS001"
DEFAULT_DEVICE_NAME = "EP-133"
DEFAULT_DEVICE_VERSION = "2.0.5"
PAK_VERSION = 1
PAK_TYPE = "user"
PAK_RELEASE = "1.2.0"
PAK_AUTHOR = "ep133-ppak"


def build_meta(
    *,
    device_sku: str = DEFAULT_DEVICE_SKU,
    device_name: str = DEFAULT_DEVICE_NAME,
    device_version: str = DEFAULT_DEVICE_VERSION,
    author: str = PAK_AUTHOR,
    generated_at: str | None = None,
) -> dict:
    """Build the `meta.json` dict for a `.ppak` per spec §"Container → meta.json"."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "info": "teenage engineering - pak file",
        "pak_version": PAK_VERSION,
        "pak_type": PAK_TYPE,
        "pak_release": PAK_RELEASE,
        "device_name": device_name,
        "device_sku": device_sku,
        "device_version": device_version,
        "generated_at": generated_at,
        "author": author,
        "base_sku": device_sku,
    }


def validate_project_tar(tar_bytes: bytes) -> dict:
    """Sanity-check captured TAR bytes look like a real project archive.

    Returns a small summary dict ({pad_count, pattern_count, has_settings,
    has_scenes}). Raises `ValueError` if the bytes don't look like a project.

    We deliberately accept the device's slightly-mangled TAR (null bytes
    interspersed in headers — see `pad_record.py`); we just need to confirm
    the shape, not parse pad records.
    """
    if len(tar_bytes) < 1024:
        raise ValueError(
            f"captured project is suspiciously small ({len(tar_bytes)} bytes); "
            "expected at least a few KB. Did the device abort mid-stream?"
        )

    # Try to open as a strict TAR; if that fails, fall back to substring
    # checks (the device's TAR sometimes has weird padding that confuses
    # tarfile but is still consumable by the EP-133 itself).
    pad_count = 0
    pattern_count = 0
    has_settings = False
    has_scenes = False

    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            for member in tar.getmembers():
                name = member.name
                if name.startswith("pads/") and "/p" in name:
                    pad_count += 1
                elif name.startswith("patterns/"):
                    pattern_count += 1
                elif name == "settings":
                    has_settings = True
                elif name == "scenes":
                    has_scenes = True
    except (tarfile.TarError, EOFError):
        # Substring fallback — count pad/pattern occurrences
        for grp in (b"a", b"b", b"c", b"d"):
            for n in range(1, 13):
                needle = b"pads/" + grp + b"/p" + f"{n:02d}".encode()
                if needle in tar_bytes:
                    pad_count += 1
        # Patterns directory marker
        if b"patterns/" in tar_bytes:
            pattern_count = tar_bytes.count(b"patterns/")
        has_settings = b"settings\x00" in tar_bytes or b"\x00settings" in tar_bytes
        has_scenes = b"scenes\x00" in tar_bytes or b"\x00scenes" in tar_bytes

    if pad_count == 0:
        raise ValueError(
            "captured TAR contains no `pads/{a,b,c,d}/pNN` entries — "
            "this does not look like an EP-133 project file."
        )
    if not has_settings:
        # Settings is required for our song-export workflow. Warn loudly
        # but don't hard-fail — older firmware may name it differently and
        # the on-disk extraction will still succeed.
        print(
            "[WARN] captured TAR has no `settings` file. Song-export will "
            "fall back to a minimal 222-byte settings buffer.",
            file=sys.stderr,
        )

    return {
        "pad_count": pad_count,
        "pattern_count": pattern_count,
        "has_settings": has_settings,
        "has_scenes": has_scenes,
    }


def wrap_tar_as_ppak(
    tar_bytes: bytes,
    *,
    project_num: int,
    meta: dict,
    out_path: Path,
) -> Path:
    """Wrap a project TAR + meta.json into a `.ppak` ZIP at `out_path`.

    The ZIP entries are written with **leading slashes** in their names —
    omitting the slash makes the EP-133 show "PAK FILE IS EMPTY" on import.
    """
    if not (1 <= project_num <= 9):
        # The on-device project picker only exposes slots 1..9 for `.ppak`
        # imports; we keep the file inside that range to avoid surprises
        # at upload time even though the SysEx layer supports 1..99.
        raise ValueError(
            f"project_num {project_num} out of range for .ppak container (1..9)"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Project TAR — note the leading slash (critical, see spec §"ZIP gotcha")
        tar_info = zipfile.ZipInfo(f"/projects/P{project_num:02d}.tar")
        tar_info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(tar_info, tar_bytes)

        # meta.json — likewise with leading slash
        meta_info = zipfile.ZipInfo("/meta.json")
        meta_info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(meta_info, json.dumps(meta, indent=2))

    return out_path


def capture(
    project_num: int,
    out_path: Path,
    *,
    device_sku: str = DEFAULT_DEVICE_SKU,
    device_version: str = DEFAULT_DEVICE_VERSION,
    author: str = PAK_AUTHOR,
) -> Path:
    """Read a project from a live EP-133 and write a `.ppak` to `out_path`.

    This is the function-level entry point — `main()` simply parses args
    and forwards here.
    """
    # Lazy import: `mido` + `python-rtmidi` are heavy and only needed when
    # actually talking to a device. Importing here gives us a friendlier
    # error message if the user has the script but not the MIDI deps, and
    # — critically — keeps the pure helpers above importable in test
    # environments without MIDI hardware.
    try:
        from ep133.project_reader import read_project_file
        from ep133.transport import EP133PortNotFound
    except ImportError as exc:
        raise SystemExit(
            f"failed to import ep133 transport modules: {exc}\n"
            "Did you run via `uv run python tools/ep133_capture_reference.py`?"
        ) from exc

    print(f"[capture] reading project {project_num} from EP-133 over SysEx…")
    try:
        tar_bytes = read_project_file(project_num)
    except EP133PortNotFound as exc:
        raise SystemExit(
            f"EP-133 not found on USB-MIDI: {exc}\n"
            "Connect the device, confirm it appears in macOS Audio MIDI Setup, "
            "and retry."
        ) from exc
    except RuntimeError as exc:
        raise SystemExit(f"capture failed: {exc}") from exc

    print(f"[capture] received {len(tar_bytes):,} bytes of TAR data")
    summary = validate_project_tar(tar_bytes)
    print(
        f"[capture] TAR contents: pads={summary['pad_count']}, "
        f"patterns={summary['pattern_count']}, "
        f"settings={'yes' if summary['has_settings'] else 'no'}, "
        f"scenes={'yes' if summary['has_scenes'] else 'no'}"
    )

    meta = build_meta(device_sku=device_sku, device_version=device_version, author=author)
    out = wrap_tar_as_ppak(tar_bytes, project_num=project_num, meta=meta, out_path=out_path)
    size_kb = out.stat().st_size / 1024
    print(f"[capture] wrote {out} ({size_kb:.1f} KB)")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture a reference .ppak from a live EP-133 over SysEx.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  uv run python tools/ep133_capture_reference.py \\\n"
            "      --project 1 \\\n"
            "      --out tests/fixtures/reference.ppak"
        ),
    )
    parser.add_argument(
        "--project",
        type=int,
        required=True,
        help="Project slot to capture (1-9 for .ppak container range)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the captured .ppak file",
    )
    parser.add_argument(
        "--device-sku",
        default=DEFAULT_DEVICE_SKU,
        help=f"Device SKU to write into meta.json (default: {DEFAULT_DEVICE_SKU})",
    )
    parser.add_argument(
        "--device-version",
        default=DEFAULT_DEVICE_VERSION,
        help=f"Device firmware version for meta.json (default: {DEFAULT_DEVICE_VERSION})",
    )
    parser.add_argument(
        "--author",
        default=PAK_AUTHOR,
        help=f"Author string for meta.json (default: {PAK_AUTHOR})",
    )
    args = parser.parse_args(argv)

    try:
        capture(
            args.project,
            args.out,
            device_sku=args.device_sku,
            device_version=args.device_version,
            author=args.author,
        )
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - last-ditch error reporting
        print(f"[capture] unexpected error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
