"""
ep133 — SysEx + .ppak protocol library for the Teenage Engineering EP-133 K.O. II.

Exports:
- `EP133Client`   — high-level SysEx client over USB-MIDI
- `pad_record`    — decode 27-byte pad records inside project TARs
- `project_reader`— read a project TAR from the device live via SysEx
- `payloads`      — `PadParams`, `SampleParams`, payload builders
- `audio`         — WAV → 46875 Hz mono PCM transcode
- `ppak`          — `.ppak` archive read/write

Quick start:

    from ep133 import EP133Client
    with EP133Client.open() as client:
        client.upload_sample("kick.wav", slot=1)
        client.assign_pad(project=1, group="A", pad_num=10, slot=1)

For the full protocol reference, see PROTOCOL.md.
"""


def __getattr__(name):
    # Lazy-load EP133Client so importing the package doesn't pull mido + hardware layers.
    if name == "EP133Client":
        from .client import EP133Client
        return EP133Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__version__ = "0.1.0"
