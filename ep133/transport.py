"""
MIDI transport wrapper over `mido`. Finds the EP-133 port, sends SysEx,
reads back responses.
"""

from __future__ import annotations

import queue
import threading
import time

from .sysex import TESysexResponse, parse_sysex

EP133_PORT_SUBSTRINGS = ("EP-133", "EP133", "EP-1320")


class EP133PortNotFound(RuntimeError):
    pass


def find_ep133_ports() -> tuple[str, str]:
    """Return `(output_name, input_name)` for the EP-133. Raises if not found."""
    import mido

    output_name = None
    for name in mido.get_output_names():
        if any(s in name for s in EP133_PORT_SUBSTRINGS):
            output_name = name
            break

    input_name = None
    for name in mido.get_input_names():
        if any(s in name for s in EP133_PORT_SUBSTRINGS):
            input_name = name
            break

    if not output_name or not input_name:
        raise EP133PortNotFound(
            f"EP-133 MIDI ports not found. "
            f"outputs={mido.get_output_names()}, inputs={mido.get_input_names()}"
        )
    return output_name, input_name


class EP133Transport:
    """Opens EP-133 I/O, sends framed SysEx, provides a blocking recv.

    Use as a context manager:
        with EP133Transport.open() as t:
            t.send(frame)
            response = t.recv(timeout=5.0)
    """

    def __init__(self, output_name: str, input_name: str, inter_message_delay_s: float = 0.01):
        self._output_name = output_name
        self._input_name = input_name
        self._delay = inter_message_delay_s
        self._output = None
        self._input = None
        self._queue: queue.Queue[TESysexResponse] = queue.Queue()
        self._raw_queue: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def open(cls, inter_message_delay_s: float = 0.01) -> EP133Transport:
        out_name, in_name = find_ep133_ports()
        t = cls(out_name, in_name, inter_message_delay_s=inter_message_delay_s)
        t._open()
        return t

    def _open(self) -> None:
        import mido

        self._output = mido.open_output(self._output_name)
        self._input = mido.open_input(self._input_name)
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        # `iter_pending` is non-blocking; poll at a modest rate.
        while not self._stop.is_set():
            if self._input is None:
                return
            for msg in self._input.iter_pending():
                if msg.type == "sysex":
                    raw = bytes([0xF0, *msg.data, 0xF7])
                    self._raw_queue.put(raw)
                    parsed = parse_sysex(raw)
                    if parsed is not None:
                        self._queue.put(parsed)
            time.sleep(0.001)

    def send(self, frame: bytes) -> None:
        import mido

        # mido expects the SysEx *payload* (bytes between F0 and F7), not the full frame
        if len(frame) < 2 or frame[0] != 0xF0 or frame[-1] != 0xF7:
            raise ValueError("frame must start with F0 and end with F7")
        self._output.send(mido.Message("sysex", data=frame[1:-1]))
        if self._delay > 0:
            time.sleep(self._delay)

    def recv(self, timeout: float = 5.0) -> TESysexResponse | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        while not self._raw_queue.empty():
            self._raw_queue.get_nowait()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

        output = self._output
        input_ = self._input
        self._output = None
        self._input = None

        # Close ports in a daemon thread: on macOS, MIDIPortDispose can block
        # in CoreMIDI while the device is still sending unsolicited messages.
        # Running it in a daemon thread lets close() return in a bounded time
        # and also makes the process Ctrl-C-able.
        def _close_ports() -> None:
            if output is not None:
                try:
                    output.close()
                except Exception:
                    pass
            if input_ is not None:
                try:
                    input_.close()
                except Exception:
                    pass

        t = threading.Thread(target=_close_ports, daemon=True)
        t.start()
        t.join(timeout=2.0)  # wait up to 2 s; daemon thread dies with the process if it hangs

    def __enter__(self) -> EP133Transport:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
