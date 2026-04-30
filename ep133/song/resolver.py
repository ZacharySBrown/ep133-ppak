"""Snapshot resolver — arrangement → per-locator playback snapshots.

Generic arrangement-shape: ``{tempo, time_sig, locators, tracks}`` where
tracks maps group letters A/B/C/D to lists of clips. See
``docs/PORT_PLAN.md`` for the full schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


GROUPS: tuple[str, ...] = ("A", "B", "C", "D")


@dataclass
class ArrangementClip:
    """One arrangement-view clip on track A/B/C/D."""

    file_path: str
    start_time_sec: float
    length_sec: float
    warping: int = 1

    @property
    def end_time_sec(self) -> float:
        return self.start_time_sec + self.length_sec

    @classmethod
    def from_dict(cls, data: dict) -> "ArrangementClip":
        return cls(
            file_path=str(data["file_path"]),
            start_time_sec=float(data["start_time_sec"]),
            length_sec=float(data["length_sec"]),
            warping=int(data.get("warping", 1)),
        )


@dataclass
class Snapshot:
    """Which clip is playing on each group at one locator."""

    locator_time_sec: float
    locator_name: str
    a_clip: ArrangementClip | None
    b_clip: ArrangementClip | None
    c_clip: ArrangementClip | None
    d_clip: ArrangementClip | None

    def clip_for(self, group: str) -> ArrangementClip | None:
        return getattr(self, f"{group.lower()}_clip")


class ManifestLookupError(KeyError):
    """Raised when an arrangement clip's file_path is not in
    ``manifest.session_tracks``. Carries the offending file path + group."""


def _index_session_tracks(manifest: dict) -> dict[str, dict[str, int]]:
    """Build ``{group_lower: {file_path: slot}}`` from
    ``manifest.session_tracks``. Both ``"file"`` (canonical) and ``"file_path"``
    keys are supported as the source field — the hybrid loader emits ``"file"``
    while the spec calls the matching key ``file_path``; we accept both.
    """
    session = manifest.get("session_tracks") or {}
    out: dict[str, dict[str, int]] = {}
    for group in GROUPS:
        entries = session.get(group) or session.get(group.lower()) or []
        per_group: dict[str, int] = {}
        for entry in entries:
            path = entry.get("file_path") or entry.get("file")
            if path is None:
                continue
            slot = int(entry["slot"])
            per_group[str(path)] = slot
        out[group.lower()] = per_group
    return out


def lookup_pad(manifest: dict, group: str, file_path: str) -> int:
    """Return the EP-133 pad number (1..12) for ``file_path`` on ``group``.

    Raises :class:`ManifestLookupError` if the file isn't registered for that
    group in ``manifest.session_tracks``. Pads are 1-indexed; session_tracks
    slots are 0-indexed → ``pad = slot + 1``.
    """
    index = _index_session_tracks(manifest)
    per_group = index.get(group.lower(), {})
    if file_path not in per_group:
        raise ManifestLookupError(
            f"file not in manifest.session_tracks[{group}]: {file_path!r}. "
            "Make sure the arrangement clip points at a Session-view source "
            "file that the COMMIT step registered."
        )
    return per_group[file_path] + 1


def _select_active_clip(
    clips: list[ArrangementClip], locator_time_sec: float
) -> ArrangementClip | None:
    """Find the clip playing at ``locator_time_sec`` on a single track.

    Rule: ``start_time_sec <= t < end_time_sec`` (strict ``<`` on the right —
    a locator at exactly clip-end is NOT inside that clip). When multiple
    clips overlap the locator, pick the latest-started — Ableton's playback
    semantics for arrangement-view clip overlap.
    """
    candidates = [c for c in clips if c.start_time_sec <= locator_time_sec < c.end_time_sec]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c.start_time_sec, reverse=True)
    return candidates[0]


def _coerce_track(raw: Any) -> list[ArrangementClip]:
    if not raw:
        return []
    return [ArrangementClip.from_dict(c) for c in raw]


def resolve_scenes(arrangement: dict, manifest: dict) -> list[Snapshot]:
    """Return one :class:`Snapshot` per locator (in time order).

    Validates that every clip referenced by an active snapshot is present in
    ``manifest.session_tracks`` — raises :class:`ManifestLookupError` with a
    clear message if not. Silent groups (no clip at the locator) are
    represented by ``None`` clips and produce no manifest lookup.
    """
    locators_raw = arrangement.get("locators") or []
    if not locators_raw:
        raise ValueError(
            "arrangement has no locators — locator-driven export needs at "
            "least one locator. Drop locators in Ableton with Cmd-L."
        )
    tracks_raw = arrangement.get("tracks") or {}
    tracks: dict[str, list[ArrangementClip]] = {
        g: _coerce_track(tracks_raw.get(g) or tracks_raw.get(g.lower())) for g in GROUPS
    }

    locators_sorted = sorted(
        ({"time_sec": float(L["time_sec"]), "name": str(L.get("name", ""))} for L in locators_raw),
        key=lambda L: L["time_sec"],
    )

    snapshots: list[Snapshot] = []
    pre_index = _index_session_tracks(manifest)
    for locator in locators_sorted:
        t = locator["time_sec"]
        per_group: dict[str, ArrangementClip | None] = {}
        for group in GROUPS:
            clip = _select_active_clip(tracks[group], t)
            if clip is not None:
                if clip.file_path not in pre_index.get(group.lower(), {}):
                    raise ManifestLookupError(
                        f"file not in manifest.session_tracks[{group}]: "
                        f"{clip.file_path!r}. Make sure the arrangement clip "
                        "points at a Session-view source file that the COMMIT "
                        "step registered."
                    )
            per_group[group] = clip

        snapshots.append(
            Snapshot(
                locator_time_sec=t,
                locator_name=locator["name"],
                a_clip=per_group["A"],
                b_clip=per_group["B"],
                c_clip=per_group["C"],
                d_clip=per_group["D"],
            )
        )
    return snapshots
