# Overview

![ep133-ppak overview](./00_overview.svg)

`ep133-ppak` is a Python library for putting samples and project state on a Teenage Engineering EP-133 K.O. II — over USB-MIDI, without going through the official Sample Tool. It handles the SysEx wire format, 7-bit packing, `.ppak` archive read/write, and the binary pad-record layout, so the rest of your pipeline can stay in plain Python.

There are four CLIs, ordered by how much you want to do at once. `ppak-load-one` drops a single WAV onto a single pad — useful when you just want to mess around. `ppak-load-manifest` takes a JSON manifest and batch-uploads many samples in one project, with per-pad slot, BPM, and playmode set in one pass. `ppak-writer` starts from a real Sample Tool backup as the base and patches it with a preset (BPM matrix, presets, full project state) — the path for authoring whole projects offline before sending them. `ppak-export-song` goes one step further: from an arrangement-shaped JSON plus a manifest it builds a full song-mode `.ppak` — multiple patterns, multiple scenes, and a song-position playlist that plays straight off the device.

The manifest format is the integration point. Anything that can emit a JSON list of WAVs feeds `ppak-load-manifest`: DAW exports, your own scripts, or curation tools like [StemForge](https://github.com/zacharysbrown/stemforge) — whose Ableton arrangement-export flow is the original consumer of `ppak-export-song`. If you'd rather skip the CLI entirely, the same operations are available through `EP133Client` as a Python context manager.

For the song-mode pipeline specifically — how an `arrangement.json` becomes scene snapshots, then a `PpakSpec`, then bytes — see [04_song_pipeline.svg](./04_song_pipeline.svg).

For protocol-level details — SysEx framing, the file-transfer state machine, pad-record byte layout, and the gotchas (coupled fields, integer-vs-string enums, fileId stat-before-open discipline) — see [PROTOCOL.md](../../PROTOCOL.md).
