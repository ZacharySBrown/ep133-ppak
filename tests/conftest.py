from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


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
