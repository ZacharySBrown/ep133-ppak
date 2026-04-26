"""Tests for two-pass pad-placement in `tools/load_from_manifest.build_ops_new`.

Producer (StemForge) owns rotation: writes `suggested_pad` / `suggested_group`
into each `SampleMeta`. Consumer (this loader) honors explicit hints and
fills any gaps positionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ep133.manifest import BatchManifest, SampleMeta
from tools.load_from_manifest import (
    BAR_INDEX_TO_LABEL,
    BAR_INDEX_TO_PAD_NUM,
    build_ops_new,
)


def _batch(samples: list[dict], **batch_fields) -> BatchManifest:
    return BatchManifest(samples=[SampleMeta(**s) for s in samples], **batch_fields)


def _groups(*pairs) -> list[tuple[str, str]]:
    """[('A','drums'), ('B','bass'), ...]"""
    return list(pairs)


# ---------------------------------------------------------------------------
# Pure positional fill (no suggested_pad)
# ---------------------------------------------------------------------------


def test_positional_fill_matches_legacy_layout(tmp_path: Path):
    batch = _batch([
        {"file": "d1.wav", "stem": "drums"},
        {"file": "d2.wav", "stem": "drums"},
        {"file": "d3.wav", "stem": "drums"},
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    assert [op["pad_label"] for op in ops] == [".", "0", "ENTER"]
    assert [op["bar_index"] for op in ops] == [0, 1, 2]
    assert [op["slot"] for op in ops] == [300, 301, 302]


# ---------------------------------------------------------------------------
# Pure explicit placement
# ---------------------------------------------------------------------------


def test_all_explicit_pads_honored(tmp_path: Path):
    batch = _batch([
        {"file": "d1.wav", "stem": "drums", "suggested_pad": "9"},
        {"file": "d2.wav", "stem": "drums", "suggested_pad": "."},
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    # ops are emitted in bar_index order, so '.' (bar 0) comes before '9' (bar 11)
    labels = {op["pad_label"]: op["wav_path"].name for op in ops}
    assert labels[".".strip()] == "d2.wav"
    assert labels["9"] == "d1.wav"
    # Slot follows bar_index, so '.' = base+0, '9' = base+11
    by_label = {op["pad_label"]: op["slot"] for op in ops}
    assert by_label["."] == 300
    assert by_label["9"] == 311


# ---------------------------------------------------------------------------
# Mixed: explicit claims first, positional fills around them
# ---------------------------------------------------------------------------


def test_mixed_claims_and_positional_fill(tmp_path: Path):
    batch = _batch([
        {"file": "explicit_at_0.wav", "stem": "drums", "suggested_pad": "."},
        {"file": "fills_first_unclaimed.wav", "stem": "drums"},  # → bar 1 (".0")
        {"file": "explicit_at_2.wav", "stem": "drums", "suggested_pad": "ENTER"},
        {"file": "fills_next.wav", "stem": "drums"},  # → bar 3 (next free after 2)
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    by_bar = {op["bar_index"]: op["wav_path"].name for op in ops}
    assert by_bar[0] == "explicit_at_0.wav"
    assert by_bar[1] == "fills_first_unclaimed.wav"
    assert by_bar[2] == "explicit_at_2.wav"
    assert by_bar[3] == "fills_next.wav"


def test_positional_fill_skips_already_claimed(tmp_path: Path):
    """Fillers walk bar-indices in order, jumping over explicit claims."""
    batch = _batch([
        {"file": "fill_0.wav", "stem": "drums"},
        {"file": "claim_1.wav", "stem": "drums", "suggested_pad": "0"},  # bar 1
        {"file": "fill_after_1.wav", "stem": "drums"},  # → bar 2
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    by_bar = {op["bar_index"]: op["wav_path"].name for op in ops}
    assert by_bar[0] == "fill_0.wav"
    assert by_bar[1] == "claim_1.wav"
    assert by_bar[2] == "fill_after_1.wav"


# ---------------------------------------------------------------------------
# suggested_group routing
# ---------------------------------------------------------------------------


def test_suggested_group_overrides_stem_routing(tmp_path: Path):
    """Sample with suggested_group=C lands in C even though stem=drums maps to A."""
    batch = _batch([
        {"file": "drum_in_a.wav", "stem": "drums"},
        {"file": "drum_in_c.wav", "stem": "drums", "suggested_group": "C"},
    ])
    ops = build_ops_new(
        batch, _groups(("A", "drums"), ("C", "vocals")), 300, 12, tmp_path
    )
    by_group = {(op["group"], op["wav_path"].name) for op in ops}
    assert ("A", "drum_in_a.wav") in by_group
    assert ("C", "drum_in_c.wav") in by_group


def test_suggested_group_respected_when_pad_is_none(tmp_path: Path):
    """User's open-question case: suggested_group set, suggested_pad=None.

    Sample lands in the requested group and gets positionally filled.
    """
    batch = _batch([
        {"file": "to_b.wav", "stem": "drums", "suggested_group": "B"},
    ])
    ops = build_ops_new(
        batch, _groups(("A", "drums"), ("B", "bass")), 300, 12, tmp_path
    )
    assert len(ops) == 1
    assert ops[0]["group"] == "B"
    assert ops[0]["pad_label"] == "."  # positional fill, bar 0
    assert ops[0]["bar_index"] == 0


def test_sample_routed_to_unrequested_group_is_skipped(tmp_path: Path):
    """suggested_group=D but --groups only has A → silently skipped."""
    batch = _batch([
        {"file": "in_a.wav", "stem": "drums"},
        {"file": "in_d.wav", "stem": "drums", "suggested_group": "D"},
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    assert len(ops) == 1
    assert ops[0]["wav_path"].name == "in_a.wav"


def test_sample_with_no_routing_info_is_skipped(tmp_path: Path):
    """No suggested_group, stem doesn't match any --groups entry → skipped."""
    batch = _batch([
        {"file": "drums.wav", "stem": "drums"},
        {"file": "stranded.wav", "stem": "vocals"},  # no group requested for vocals
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    assert [op["wav_path"].name for op in ops] == ["drums.wav"]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_two_explicit_claims_on_same_pad_errors(tmp_path: Path):
    batch = _batch([
        {"file": "first.wav", "stem": "drums", "suggested_pad": "."},
        {"file": "second.wav", "stem": "drums", "suggested_pad": "."},
    ])
    with pytest.raises(ValueError, match="claimed twice"):
        build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)


def test_explicit_pad_beyond_n_pads_errors(tmp_path: Path):
    batch = _batch([
        {"file": "out_of_range.wav", "stem": "drums", "suggested_pad": "9"},
    ])
    with pytest.raises(ValueError, match="exceeds --pads"):
        build_ops_new(batch, _groups(("A", "drums")), 300, 4, tmp_path)


def test_too_many_unclaimed_for_n_pads_errors(tmp_path: Path):
    batch = _batch([
        {"file": "a.wav", "stem": "drums"},
        {"file": "b.wav", "stem": "drums"},
        {"file": "c.wav", "stem": "drums"},
    ])
    # n_pads=2, but 3 samples want positional fill
    with pytest.raises(ValueError, match="too many samples"):
        build_ops_new(batch, _groups(("A", "drums")), 300, 2, tmp_path)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_relative_paths_resolved_against_manifest_dir(tmp_path: Path):
    batch = _batch([
        {"file": "subdir/d1.wav", "stem": "drums"},
        {"file": "/abs/path/d2.wav", "stem": "drums"},
    ])
    ops = build_ops_new(batch, _groups(("A", "drums")), 300, 12, tmp_path)
    # Relative path joined with manifest_dir
    assert ops[0]["wav_path"] == tmp_path / "subdir" / "d1.wav"
    # Absolute path passed through
    assert ops[1]["wav_path"] == Path("/abs/path/d2.wav")


# ---------------------------------------------------------------------------
# Slot allocation
# ---------------------------------------------------------------------------


def test_slot_uses_bar_index_within_group(tmp_path: Path):
    """slot = start_slot + g_idx * n_pads + bar_idx, even for sparse layouts."""
    batch = _batch([
        {"file": "d_first.wav", "stem": "drums", "suggested_pad": "."},
        {"file": "d_last.wav", "stem": "drums", "suggested_pad": "9"},
        {"file": "b_only.wav", "stem": "bass"},
    ])
    ops = build_ops_new(
        batch, _groups(("A", "drums"), ("B", "bass")), 300, 12, tmp_path
    )
    by_name = {op["wav_path"].name: op for op in ops}
    assert by_name["d_first.wav"]["slot"] == 300        # g=0, bar=0
    assert by_name["d_last.wav"]["slot"] == 300 + 11    # g=0, bar=11
    assert by_name["b_only.wav"]["slot"] == 300 + 12    # g=1, bar=0


# ---------------------------------------------------------------------------
# Sanity: bar_index → pad_num/label tables stay in sync
# ---------------------------------------------------------------------------


def test_bar_tables_consistent():
    assert len(BAR_INDEX_TO_PAD_NUM) == 12
    assert len(BAR_INDEX_TO_LABEL) == 12
