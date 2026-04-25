"""
EP133Client — the public API. Orchestrates framing + transport + responses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .audio import wav_to_ep133_pcm
from .commands import IDENTITY_SYSEX, STATUS_OK, TE_SYSEX_FILE
from . import payloads as P
from .payloads import PadParams, build_assign_pad
from .sysex import RequestIdAllocator, build_sysex
from .transfer import generate_upload_payloads
from .transport import EP133Transport

ProgressFn = Callable[[int, int], None]


class EP133UploadError(RuntimeError):
    pass




class EP133Client:
    """High-level wrapper: `upload_sample(wav, slot)`."""

    def __init__(self, transport: EP133Transport, identity_code: int = 0):
        self._t = transport
        self._identity_code = identity_code
        self._reqs = RequestIdAllocator()

    @classmethod
    def open(cls, inter_message_delay_s: float = 0.01) -> EP133Client:
        return cls(EP133Transport.open(inter_message_delay_s=inter_message_delay_s))

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> EP133Client:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Low-level request helpers ──────────────────────────────────────

    def _send(self, command: int, payload: bytes) -> int:
        request_id = self._reqs.next()
        frame = build_sysex(command, payload, request_id, self._identity_code)
        self._t.send(frame)
        return request_id

    def _await_response(self, request_id: int, timeout: float = 5.0) -> TESysexResponse:
        """Block for a response matching `request_id`. Stale responses are discarded."""
        import time

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EP133UploadError(f"timeout waiting for response to request {request_id}")
            response = self._t.recv(timeout=remaining)
            if response is None:
                continue
            if response.is_request or not response.has_request_id:
                continue
            if response.request_id != request_id:
                continue
            if response.status != STATUS_OK:
                raise EP133UploadError(
                    f"device returned status {response.status} ({response.status_text}) "
                    f"for command {response.command} request {request_id}"
                )
            return response

    # ── Public API ─────────────────────────────────────────────────────

    def identify(self) -> bytes:
        """Send a universal identity request, return the raw response bytes."""
        self._t.drain()
        self._t.send(IDENTITY_SYSEX)
        # Identity replies are universal, not request-id-keyed. We don't parse
        # them here — just let the transport capture it and move on.
        return b""

    def apply_pad_assignments(
        self,
        assignments,
        timeout: float = 5.0,
        progress: ProgressFn | None = None,
    ) -> None:
        """Push every `EP133PadAssignment` in an EP133Mapping to the device.

        `assignments` is any iterable of objects with `project: int`,
        `group: str`, `pad: int` (1..12), `slot: int` — which matches the
        existing `EP133PadAssignment` dataclass in `ep133_mapping.py`.

        If an assignment has a `params` attribute (a `PadParams` instance),
        it will be written alongside the slot assignment in a single message.
        """
        pas = list(assignments)
        total = len(pas)
        for i, pa in enumerate(pas):
            params = getattr(pa, "params", None)
            self.assign_pad(pa.project, pa.group, pa.pad, pa.slot, params=params, timeout=timeout)
            if progress is not None:
                progress(i + 1, total)

    def assign_pad(
        self,
        project: int,
        group: str,
        pad_num: int,
        slot: int,
        params: PadParams | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Assign a pad to a library slot, optionally with playback parameters.

        - `project`: 1-indexed project number
        - `group`: 'A' | 'B' | 'C' | 'D'
        - `pad_num`: 1..12, visual position top-to-bottom left-to-right.
          Use `ep133.payloads.pad_num_from_label` to
          convert from physical pad labels ('7', '.', 'ENTER', …).
        - `slot`: library slot to play from when the pad is triggered.
        - `params`: optional `PadParams` — sets playmode, trim, envelope, etc.
          in the same message. Without it only the slot assignment is written.
        """
        payload = build_assign_pad(project, group, pad_num, slot, params=params)
        request_id = self._send(TE_SYSEX_FILE, payload)
        self._await_response(request_id, timeout=timeout)

    def upload_sample(
        self,
        wav_path: Path,
        slot: int,
        name: str | None = None,
        channels: int = 1,
        timeout: float = 10.0,
        progress: ProgressFn | None = None,
    ) -> int:
        """Upload a WAV file to a specific EP-133 library slot.

        `slot` is the target library slot (1-based). FILE_INFO commits the
        uploaded audio buffer to this slot — without it the device discards
        the upload. `name` defaults to `<slot>_<wav_stem>`.

        `progress(done, total)` is called once per message sent.
        Returns `slot` on success.
        """
        if not (1 <= slot <= 0xFFFF):
            raise ValueError(f"slot {slot} must be 1..65535")
        if name is None:
            name = f"{slot}_{wav_path.stem}"[:20]

        pcm = wav_to_ep133_pcm(wav_path, channels=channels)
        messages = generate_upload_payloads(pcm, name=name, channels=channels, slot=slot)

        total = len(messages)
        for i, (command, payload) in enumerate(messages):
            request_id = self._send(command, payload)
            self._await_response(request_id, timeout=timeout)
            if progress is not None:
                progress(i + 1, total)

        return slot
