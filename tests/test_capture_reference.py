"""Unit tests for the helpers in ``tools/ep133_capture_reference.py``.

The capture tool itself talks to a live EP-133 over USB-MIDI — those
paths are exercised manually. Here we cover the pure functions
(``build_meta``, ``validate_project_tar``, ``wrap_tar_as_ppak``) so the
container-format gotchas (leading slash, meta.json shape, 26-byte pad
records) are protected by CI even without device hardware.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

# Make the tools/ directory importable
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import ep133_capture_reference as cap  # noqa: E402


def _make_minimal_project_tar(*, with_settings: bool = True) -> bytes:
    """Build a syntactically valid project TAR for tests."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # One pad with a non-zero sample slot (26 bytes — factory native).
        pad = bytearray(26)
        pad[1:3] = struct.pack("<H", 100)
        info = tarfile.TarInfo(name="pads/a/p01")
        info.size = len(pad)
        tar.addfile(info, io.BytesIO(bytes(pad)))

        if with_settings:
            settings = bytes(222)
            info2 = tarfile.TarInfo(name="settings")
            info2.size = len(settings)
            tar.addfile(info2, io.BytesIO(settings))
    return buf.getvalue()


# --- build_meta --------------------------------------------------------------


def test_build_meta_required_keys():
    meta = cap.build_meta()
    for key in (
        "info",
        "pak_version",
        "pak_type",
        "pak_release",
        "device_name",
        "device_sku",
        "device_version",
        "generated_at",
        "author",
        "base_sku",
    ):
        assert key in meta, f"missing key: {key!r}"


def test_build_meta_sku_equals_base_sku():
    meta = cap.build_meta(device_sku="TE032ABC123")
    assert meta["device_sku"] == "TE032ABC123"
    assert meta["base_sku"] == "TE032ABC123"


def test_build_meta_constants():
    meta = cap.build_meta()
    assert meta["info"] == "teenage engineering - pak file"
    assert meta["pak_version"] == 1
    assert meta["device_name"] == "EP-133"
    assert meta["pak_type"] == "user"


# --- validate_project_tar ----------------------------------------------------


def test_validate_rejects_too_small():
    with pytest.raises(ValueError, match="suspiciously small"):
        cap.validate_project_tar(b"\x00" * 100)


def test_validate_rejects_no_pads():
    """2 KB of zeros — survives length check, has no pad entries."""
    with pytest.raises(ValueError, match="no `pads/"):
        cap.validate_project_tar(b"\x00" * 2048)


def test_validate_accepts_minimal_project():
    tar_bytes = _make_minimal_project_tar()
    summary = cap.validate_project_tar(tar_bytes)
    assert summary["pad_count"] == 1
    assert summary["has_settings"] is True


def test_validate_warns_when_settings_missing(capsys):
    tar_bytes = _make_minimal_project_tar(with_settings=False)
    summary = cap.validate_project_tar(tar_bytes)
    assert summary["pad_count"] == 1
    assert summary["has_settings"] is False
    captured = capsys.readouterr()
    assert "settings" in captured.err.lower()


# --- wrap_tar_as_ppak --------------------------------------------------------


def test_wrap_creates_valid_zip(tmp_path):
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "ref.ppak"
    cap.wrap_tar_as_ppak(tar_bytes, project_num=1, meta=cap.build_meta(), out_path=out)
    assert out.exists()
    assert zipfile.is_zipfile(out)


def test_wrap_entries_have_leading_slash(tmp_path):
    """The big gotcha — device shows 'PAK FILE IS EMPTY' without leading /."""
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "ref.ppak"
    cap.wrap_tar_as_ppak(tar_bytes, project_num=2, meta=cap.build_meta(), out_path=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert names, "zip is empty"
    bad = [n for n in names if not n.startswith("/")]
    assert not bad, f"entries missing leading slash: {bad}"


def test_wrap_contains_project_tar_at_correct_path(tmp_path):
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "ref.ppak"
    cap.wrap_tar_as_ppak(tar_bytes, project_num=7, meta=cap.build_meta(), out_path=out)
    with zipfile.ZipFile(out) as zf:
        assert "/projects/P07.tar" in zf.namelist()
        roundtrip = zf.read("/projects/P07.tar")
    assert roundtrip == tar_bytes


def test_wrap_contains_meta_json(tmp_path):
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "ref.ppak"
    cap.wrap_tar_as_ppak(tar_bytes, project_num=1, meta=cap.build_meta(), out_path=out)
    with zipfile.ZipFile(out) as zf:
        assert "/meta.json" in zf.namelist()
        meta = json.loads(zf.read("/meta.json"))
    assert meta["info"] == "teenage engineering - pak file"


def test_wrap_rejects_invalid_project_num(tmp_path):
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "ref.ppak"
    with pytest.raises(ValueError, match="out of range"):
        cap.wrap_tar_as_ppak(tar_bytes, project_num=10, meta=cap.build_meta(), out_path=out)
    with pytest.raises(ValueError, match="out of range"):
        cap.wrap_tar_as_ppak(tar_bytes, project_num=0, meta=cap.build_meta(), out_path=out)


def test_wrap_creates_parent_dirs(tmp_path):
    tar_bytes = _make_minimal_project_tar()
    out = tmp_path / "deep" / "nested" / "ref.ppak"
    cap.wrap_tar_as_ppak(tar_bytes, project_num=1, meta=cap.build_meta(), out_path=out)
    assert out.exists()
