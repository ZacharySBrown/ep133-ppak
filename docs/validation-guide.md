# Validating a generated .ppak

A short checklist for confirming that a `.ppak` produced by this
project's writer (or any other generator) imports cleanly and behaves
as expected on the device. Use this when you're trying out a new
preset, integrating against a new manifest source, or debugging why
something doesn't sound right after upload.

For the byte-level layout itself, see
[PROTOCOL.md](../PROTOCOL.md). For the diff method that backs up the
field offsets, see [verifying-byte-offsets.md](verifying-byte-offsets.md).

---

## Stage 0 — sanity-check the ZIP

Before anything else, confirm the archive structure is right:

```bash
unzip -l my_generated.ppak
```

You should see three entry types, all with leading `/`:

```
/projects/PXX.tar
/sounds/<slot> <name>.wav     (one per referenced slot)
/meta.json
```

If any entry is missing a leading slash, has the wrong filename
convention (`<slot>_<name>.wav` instead of `<slot> <name>.wav`), or
contains the wrong WAV format (must be 44.1 kHz stereo), Sample Tool
will silently refuse to import the file with no useful error. Most of
this is handled automatically by `ep133.ppak.writer.build_from_base`.

---

## Stage 1 — Sample Tool import

Open Sample Tool ([teenageengineering.com/apps/ep-sample-tool](https://teenageengineering.com/apps/ep-sample-tool))
in Chrome, connect your EP-133 via WebMIDI, and use **Load Project** (or
the Restore equivalent for full backups) to pull in the `.ppak`.

You should see:

- The expected sample slots populated, named `<slot> <name>.wav`
- The target project listed
- Pads in the configured groups bound to the right slots (Sample Tool's
  pad inspector will show each pad's slot reference, BPM, and trim)

If Sample Tool reports any kind of error — even a transient one — stop
and inspect the file before moving to the device. The browser is the
cheaper place to fail.

---

## Stage 2 — On-device behavior

After upload, open the project on the EP-133 and tap pads in the
configured group(s). Things to listen for:

- **Pads play the expected sample.** If a pad is silent, the slot
  binding (byte +1 of the pad record) didn't take. Verify against the
  output of `ep133.pad_record.find_pad_records` on the project TAR.
- **Tempo behavior matches the loop's source BPM.** When `time.mode=bpm`
  and `sound.bpm` is set to the loop's true recording tempo, the device
  stretches each loop to fit the project tempo cleanly. If a pad
  produces a "blip" (very short audio fragment) instead of the expected
  loop, the source-BPM-vs-project-BPM ratio is too aggressive — see
  PROTOCOL.md §5 on time-stretch math.
- **Pad position matches what you wrote.** Remember the two pad-numbering
  conventions: `pads/c/p01` in the TAR is the bottom-left "." pad, but
  `assign_pad(pad_num=1)` over SysEx is the top-left "7" pad. Same
  physical grid, different numbers. See
  [01_pad_numbering.md](diagrams/01_pad_numbering.md).

---

## Stage 3 — Read the device back via SysEx

Once a project is loaded, you can read its TAR back live to confirm
what actually landed:

```python
from ep133.project_reader import read_project_file
from ep133.pad_record import find_pad_records

tar = read_project_file(project_num=9)
for pad in find_pad_records(tar):
    print(pad.name, pad.bpm, pad.bpm_encoding)
```

Cross-check the bytes against what your generator wrote. If the
roundtrip differs, the import path normalized something — that's
useful signal about which fields are advisory vs. authoritative.

---

## Known sharp edges

- **No `settings` file in the project TAR.** Sample Tool's emit format
  doesn't include one, and adding one to a synthetic `.ppak` triggers
  ERROR CLOCK 43 on the device — recoverable only by `SHIFT+ERASE`
  flash format. The writer in this repo never adds one.
- **WAV format**: 44.1 kHz stereo, 16-bit. Sample Tool transcodes to
  46875 Hz mono internally on upload; the format inside the `.ppak`
  itself is 44.1/stereo. A `.ppak` containing a 46875 mono WAV is
  silently rejected.
- **Timestamps**: `meta.json`'s `generated_at` should be a current ISO
  timestamp with millisecond precision. Stale or stub timestamps
  (`1970-01-01...`, `1980-01-01...`) seem to fail an internal validator.
- **`device_sku`**: leave it as `TE032AS001` (the K.O. II model
  identifier) — that's what real backups carry.
- **Override-BPM encoding** (byte +13 = `0x80`, +14 = bpm or bpm×2,
  +15 = precision flag) is verified for several captures of pads
  configured on-device. Whether this exact encoding survives a fresh
  `.ppak` import vs. being normalized to the float32 form is worth
  testing if you're authoring per-pad BPM via `bpm_override=True`.

---

## When something doesn't load

| Symptom | Likely cause | Where to look |
|---|---|---|
| Sample Tool silently rejects the file | ZIP entry conventions off (missing `/`, bad order, wrong WAV format) | PROTOCOL.md §9.1 |
| Sample Tool imports, device throws ERROR CLOCK 43 | Extra file in the project TAR (almost always `settings`), or stale/stub `generated_at` | PROTOCOL.md §11 + above |
| Pads silent on device after import | Slot binding (byte +1) or sample length (bytes +8..+11) didn't write | PROTOCOL.md §7.3 (diff method) |
| All pads same tempo | `time.mode` not set to `"bpm"` on slots, or `sound.bpm` left at default | PROTOCOL.md §5 |
| One pad's tempo wildly off | source-BPM-vs-project-BPM ratio extreme; bar quantization in the device | PROTOCOL.md §5 |
| Layout lands on wrong physical pads | Confusion between TAR `pNN` and SysEx `pad_num` conventions | [diagrams/01_pad_numbering.md](diagrams/01_pad_numbering.md) |

If you hit something that isn't covered here, the diff method
([verifying-byte-offsets.md](verifying-byte-offsets.md)) is the most
reliable way to figure out what changed at the byte level. Two
backups, one `cmp -l`, and you have your answer.
