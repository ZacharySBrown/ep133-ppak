from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURES = FIXTURES / "captures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def captures_dir() -> Path:
    return CAPTURES


def _split_messages(raw: bytes) -> list[bytes]:
    msgs: list[bytes] = []
    cur = bytearray()
    for b in raw:
        cur.append(b)
        if b == 0xF7:
            msgs.append(bytes(cur))
            cur = bytearray()
    return msgs


@pytest.fixture(scope="session")
def garrett_kick_messages() -> list[bytes]:
    """All 32 SysEx messages from Garrett's kick-01 upload, in order.

    File order: kick_00_init.syx (3 messages: identity, greet, file_init),
    then kick_01.syx..kick_30.syx.
    """
    out: list[bytes] = []
    out.extend(_split_messages((FIXTURES / "kick_00_init.syx").read_bytes()))
    for i in range(1, 31):
        out.extend(_split_messages((FIXTURES / f"kick_{i:02d}.syx").read_bytes()))
    return out


# ── song-mode fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def arrangement() -> dict:
    """Sample arrangement: 3 locators (Verse/Chorus/Outro), tracks A/B/C populated."""
    return json.loads((FIXTURES / "sample_arrangement.json").read_text())


@pytest.fixture
def manifest() -> dict:
    """Sample manifest: session_tracks for A (3 slots), B (2), C (1), D (empty)."""
    return json.loads((FIXTURES / "sample_manifest.json").read_text())


@pytest.fixture(scope="session")
def reference_minimal_ppak() -> Path:
    """177KB minimal capture from a real device — used as reference template.

    Always shipped in the repo. If missing, fail loudly — this should never
    be skipped in CI.
    """
    path = CAPTURES / "reference_minimal.ppak"
    if not path.is_file():
        pytest.fail(
            f"reference_minimal.ppak missing at {path} — "
            "this fixture must always be present (177KB, in-repo)"
        )
    return path


@pytest.fixture(scope="session")
def factory_default_pak() -> Path:
    """26MB factory-reset device backup. Source of truth for the 26-byte
    pad-record format claim. Optional fixture — too large to commit; the
    user drops it in for golden-file testing.

    Skip the test if missing.
    """
    path = CAPTURES / "factory_default.pak"
    if not path.is_file():
        pytest.skip(
            f"factory_default.pak optional fixture missing at {path}. "
            "Copy from a factory-reset device backup to enable golden-byte tests."
        )
    return path


@pytest.fixture(scope="session")
def smack_song_ppak() -> Path:
    """22MB full song-mode capture (multi-scene + song positions).
    Optional fixture — too large to commit.
    """
    path = CAPTURES / "smack_song.ppak"
    if not path.is_file():
        pytest.skip(
            f"smack_song.ppak optional fixture missing at {path}. "
            "Copy from a Sample Tool song-mode backup to enable full-song tests."
        )
    return path
