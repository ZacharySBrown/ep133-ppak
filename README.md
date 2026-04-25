# ep133-ppak

Reverse-engineered SysEx + `.ppak` protocol library for the
[Teenage Engineering EP-133 K.O. II](https://teenage.engineering/products/ep-133).

Write valid `.ppak` archives from Python. Decode the EP-133's binary pad
record. Read project files live via SysEx. Upload samples and assign pads
without touching Sample Tool.

## What's unique here

This work complements two earlier community efforts:
- [phones24](https://github.com/phones24) — `.ppak` archive parser
- [ep133-krate](https://github.com/icherniukh/ep133-krate) — live SysEx
  capture-based protocol RE + sample-management TUI

`ep133-ppak` adds:

- **Verified pad-record byte offsets** — phones24's table has the right
  fields but wrong offsets (shifted by 1-2 bytes). Decoded by diffing
  two real Sample Tool backups; documented with verification status
  field-by-field. See [PROTOCOL.md §7](PROTOCOL.md#7-pad-binary-record-27-bytes-in-project-tar).
- **`.ppak` writer that actually loads** — generates Sample-Tool-compatible
  archives by patching a real backup as a base. Build-from-scratch
  attempts get silently rejected; patch-from-real works on the first try.
  See [`ep133/ppak/writer.py`](ep133/ppak/writer.py).
- **Sample Tool emit format details** — `meta.json` schema, ZIP entry
  conventions, the absent-on-purpose `settings` file (adding one triggers
  ERROR CLOCK 43 + flash-format recovery), WAV format (44.1kHz stereo, NOT
  the device's internal 46875 mono).
- **Two pad-numbering conventions** explicitly documented — the TAR's
  `pNN` is bottom-up, the SysEx `pad_num` is top-down. Easy footgun,
  not previously called out.
- **Time-stretch math** for `time.mode=bpm` and the practical implication:
  set each loop's `sound.bpm` to its true recorded tempo, and the device's
  bar inference Just Works at any project tempo.
- **The diff method** — reproducible byte-offset verification via two
  Sample Tool backups, before and after one UI change. See
  [docs/verifying-byte-offsets.md](docs/verifying-byte-offsets.md).

## Install

```bash
pip install -e .
# or with MIDI backends for live device interaction:
pip install -e ".[midi]"
```

Requires Python 3.11+. Live USB-MIDI requires `mido` + `python-rtmidi`.

## Quick start

### Write a `.ppak` from a real backup

You'll need one real `.ppak` from Sample Tool's **Backup** as a format-clean
base. Connect your EP-133 to Chrome via WebMIDI at
[teenageengineering.com/apps/ep-sample-tool](https://teenageengineering.com/apps/ep-sample-tool),
hit Backup, save the file.

Then:

```bash
ppak-writer \
  --base ~/Downloads/EP-133_*_backup.ppak \
  --preset matrix_tight \
  --out ~/Desktop/my_project.ppak
```

Drag the result into Sample Tool and **Upload**. Your project is on the
device with 12 pads in Group C, each at a different BPM (120-180), all
playing the sample from the base backup.

### Live SysEx upload (no Chrome)

```python
from ep133 import EP133Client
from ep133.payloads import SampleParams, PadParams, build_slot_metadata_set
from ep133.commands import TE_SYSEX_FILE

with EP133Client.open() as client:
    # Upload a WAV to slot 100
    client.upload_sample("kick.wav", slot=100)

    # Tag the slot with its true tempo
    params = SampleParams(bpm=107.666, time_mode="bpm")
    payload = build_slot_metadata_set(100, params)
    rid = client._send(TE_SYSEX_FILE, payload)
    client._await_response(rid)

    # Assign to pad C-1 (label "7", top-left of group C)
    client.assign_pad(project=9, group="C", pad_num=1, slot=100,
                      params=PadParams(time_mode="bpm"))
```

### Bulk-load loops from a JSON manifest

```bash
ppak-load-manifest manifest.json \
  --project 9 \
  --groups A=drums B=bass C=vocals D=other \
  --start-slot 300
```

The manifest's `bpm` field is written to every slot's `sound.bpm`, and
each loop's audio length combined with that BPM tells the device how
many bars the loop is — so it stretches cleanly at any project tempo.
See [`tools/load_from_manifest.py`](tools/load_from_manifest.py) for the
manifest schema.

## Repository layout

```
ep133/                   The Python library
  __init__.py            Lazy-loaded EP133Client
  client.py              High-level SysEx client
  transport.py           MIDI port discovery + I/O
  sysex.py               Frame build/parse, request IDs
  packing.py             7-bit packing
  commands.py            Command bytes, sub-cmd bytes, fileId formula
  payloads.py            PadParams, SampleParams, payload builders
  audio.py               WAV → 46875 Hz mono PCM transcode
  transfer.py            Upload message-sequence generator
  project_reader.py      Live read project TAR via SysEx
  pad_record.py          Decode 27-byte pad records
  ppak/
    writer.py            .ppak archive writer (patch-from-real)

tools/                   CLI utilities
  ppak_writer.py         CLI for the .ppak writer
  bpm_matrix.py          12-pad BPM matrix via SysEx
  load_from_manifest.py  Bulk-load loops from a JSON manifest

tests/                   pytest suite (100+ tests, all passing)

docs/
  validation-guide.md    What to expect when validating a generated .ppak
  verifying-byte-offsets.md  The diff method for verifying pad-record bytes

PROTOCOL.md              Complete protocol + format specification
LICENSE                  MIT
```

## See also

- **[PROTOCOL.md](PROTOCOL.md)** — full SysEx protocol + `.ppak` format reference
- **[ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)** — what each upstream community project contributed and where to use their work today

Brief pointers to upstream / sibling projects (full credit in [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)):

- **[phones24](https://github.com/phones24)** — original `.ppak` archive parser; canonical for archive shape
- **[ep133-krate](https://github.com/icherniukh/ep133-krate)** — live SysEx capture-based RE + sample manager TUI; extensive capture archive
- **[garrettjwilke/ep_133_sysex_thingy](https://github.com/garrettjwilke/ep_133_sysex_thingy)** — pre-FW 2.0 SysEx examples
- **[benjaminr/mcp-koii](https://github.com/benjaminr/mcp-koii)** — MIDI control + sound-to-pad mapping research
- **abrilstudios/rcy** — FW 2.0.5 upload reference

## Status

This is community RE; expect errors. Verify against real device output
before using for anything load-bearing. PRs and corrections welcome.

Tested against firmware **OS 2.0.5** as of 2026-04.

## License

MIT — see [LICENSE](LICENSE).
