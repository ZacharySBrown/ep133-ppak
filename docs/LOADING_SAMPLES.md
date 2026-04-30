# Loading samples onto an EP-133 K.O. II

A walk-through for putting your own WAVs on the device — one sample, many
samples, with all the per-sample knobs (BPM, time mode, playmode, names,
suggested pads). It assumes you've got an EP-133, a USB cable, and at least
one WAV you want on a pad.

If you instead want to build a multi-pattern, multi-scene song-mode project
from a full arrangement, that's a different pipeline — see the pointer at
the bottom.

---

## prerequisites

- An EP-133 K.O. II connected over USB-MIDI. Power it on first; the host
  needs to see a MIDI port called something like `EP-133 K.O. II`.
- Python 3.11+, then from the project root:

  ```bash
  pip install -e ".[midi]"
  ```

  The `[midi]` extra pulls in `mido` and `python-rtmidi`. Without it the CLI
  imports fine but can't talk to the device.

- A WAV. Any common format — mono or stereo, any sample rate, 16/24/32-bit.
  The library transcodes to the device's native format (mono 16-bit
  46875 Hz) at upload time. You don't need to pre-convert anything.

After install you'll have two CLI entry points on your `$PATH`:

- `ppak-load-one` — single sample to a single pad
- `ppak-load-manifest` — bulk-load from a JSON manifest

---

## the simplest case: one sample, one pad

Drop a kick on Project 1, Group A, the pad labelled "7":

```bash
ppak-load-one kick.wav --project 1 --group A --pad 7 --slot 100
```

What each flag does:

- `kick.wav` — the file to upload. Anywhere on disk.
- `--project 1` (or `-P 1`) — which of the device's 99 projects you want
  this pad to live in. Default is 1.
- `--group A` (or `-g A`) — which of the four pad groups (A/B/C/D) on the
  device. Default is A.
- `--pad 7` — the **physical pad label** on the device. Pads are
  arranged like a phone keypad:

  ```
       7  8  9
       4  5  6
       1  2  3
       .  0  ENTER
  ```

  So `--pad 7` is the top-left pad, `--pad ENTER` (or `--pad E`) is
  bottom-right. Valid labels: `1`–`9`, `.`, `0`, `ENTER`.

- `--slot 100` (or `-s 100`) — the sample-library slot to upload into.
  Slots are 1–999 and they're shared across the whole device, not
  per-project. Two pads can point at the same slot (handy for re-using
  a sample without re-uploading).

Output:

```
  uploading kick.wav → slot 100 ... done (1.2s)
  assign  P1 A-7 (pad_num=1) → slot 100 ... done (0.05s)
  ✓ Loaded.
```

Tap pad 7 on the device and you should hear it. If you'd rather not
memorise label-to-position, use `--pad-num 1..12` instead — counted
top-down, left-to-right. See [pad numbering, quick reference](#pad-numbering-quick-reference)
below.

---

## per-sample settings

Beyond pad placement, each slot carries metadata that affects playback.
Set these from the CLI on `ppak-load-one`, or in a manifest (next
section).

### `--bpm` — source tempo of the sample

```bash
ppak-load-one loop.wav -P 1 -g A --pad 7 --slot 100 --bpm 107.666
```

Tags the slot with `sound.bpm`. The device uses this when `time.mode`
is `bpm` to stretch the sample to project tempo:

```
playback_speed = project_bpm / sound.bpm
```

A 107.666 BPM loop in a 120 BPM project plays back ~1.115× faster.
Set this to the WAV's **true recorded tempo**, not what you wish it
played at. Range is 1.0–200.0 (device rejects anything higher).

Passing `--bpm` without `--time-mode` auto-flips `time.mode = bpm` so
the stretch actually engages. Without that, BPM is recorded but
ignored at playback.

### `--time-mode` — what to do with the BPM tag

- `off` — one-shot at native rate. Project tempo is ignored. Good for
  hits and fx.
- `bar` — sync to bars. The device fits the sample into a power-of-2
  bar count. Use when you've recorded a clean N-bar loop and want it
  bar-locked.
- `bpm` — stretch to project tempo using `sound.bpm`. The default when
  `--bpm` is given.

### `--playmode` — how the pad responds to a press

- `oneshot` (default) — pad press triggers; sample plays through.
- `key` — gate. Sample plays only while the pad is held; release chokes
  it (envelope.release = 15).
- `legato` — smooth re-trigger across pads in the same group, no
  re-attack. Useful for monophonic melodic patches.

The library auto-pairs `envelope.release` with `playmode`, so gate
behaviour actually works — writing playmode alone silently breaks
`key` mode. You don't need to think about it; just pick a `--playmode`.

### `--name` — display name on the device

```bash
ppak-load-one kick.wav -P 1 -g A --pad 7 --slot 100 --name "kick 909"
```

ASCII, max 20 characters. Defaults to `<slot>_<filename-stem>`.

### bars (manifest-only)

The `bars` field — sample length in bars — helps the device pick clean
bar-quantized stretching for `time.mode = bar`. **Only settable via
manifest**, not from `ppak-load-one`.

For everything else the slot can carry (root note, loop points, attack,
fine pitch, pan, amplitude), see [PROTOCOL.md §5](../PROTOCOL.md#5-sample-slot-json-metadata-17-fields)
— accessible via the Python API, not yet through the CLI.

---

## bulk-loading from a manifest

Once you've got more than a handful of samples, list them in a JSON file
and let `ppak-load-manifest` do the rest. There are two shapes the loader
will accept.

### a. inline batch manifest

A `.manifest.json` file alongside your WAVs:

```json
{
  "version": 1,
  "track": "summer demos",
  "bpm": 120.0,
  "samples": [
    {"file": "drums/kick.wav",  "stem": "drums",  "suggested_group": "A", "suggested_pad": "7"},
    {"file": "bass/lead.wav",   "stem": "bass",   "suggested_group": "B", "bpm": 100.0, "playmode": "key"},
    {"file": "vox/hook.wav",    "stem": "vocals", "suggested_group": "C", "time_mode": "bpm"}
  ]
}
```

Top-level fields:

- `version` — schema version. Currently `1`.
- `track` — display name for logs. Optional.
- `bpm` — **default** source BPM applied to any sample that doesn't
  set its own. Per-sample `bpm` always wins.
- `samples` — list of `SampleMeta` entries.

Per-sample fields (all optional except `file`):

- `file` — path, absolute or relative to the manifest's directory.
- `stem` — `drums` | `bass` | `vocals` | `other` | `full`. Used for
  group routing via `--groups` (see below).
- `bpm`, `time_mode`, `playmode`, `name`, `bars` — same meaning as
  the CLI flags above. `time_mode` accepts `off` / `bar` / `bpm`,
  `playmode` accepts `oneshot` / `key` / `legato`.
- `suggested_group` — `A` / `B` / `C` / `D`. If set, it overrides the
  `--groups` stem→group routing for this sample.
- `suggested_pad` — physical pad label (`"7"`, `"."`, etc.). If set,
  the loader pins this sample to that pad. Two samples claiming the
  same pad in the same group is an error.
- `audio_hash` — first 16 hex chars of the WAV's sha256. Used to match
  sidecar/batch entries to a WAV even after rename. The library
  computes it for you when needed.

### b. sidecar manifest

When you'd rather have metadata travel with one specific WAV, drop a
sidecar next to it:

```
drums/
  kick.wav
  .manifest_a3f12b7c8e9d4516.json
```

Filename is `.manifest_<hash>.json`, where `<hash>` is the first 16 hex
chars of the WAV's sha256. The contents are a single `SampleMeta`
object (no `samples` wrapper, no `version`):

```json
{
  "name": "kick 909",
  "bpm": 120.0,
  "time_mode": "off",
  "playmode": "oneshot",
  "stem": "drums",
  "suggested_group": "A",
  "suggested_pad": "7"
}
```

`ppak-load-one` auto-detects this sidecar when you run with just
`--slot` and `--pad` — flags from the sidecar fill in `--bpm`,
`--playmode`, `--name`, etc. if you didn't pass them yourself.

### resolution order

When `ppak-load-one` runs, it walks this chain (highest-priority first)
to find each setting:

1. Explicit CLI flag (`--bpm`, `--playmode`, …).
2. `--manifest <path>` if you passed one, treated as either sidecar
   shape or batch shape based on its content.
3. Auto-detected sidecar (`.manifest_<hash>.json` next to the WAV).
4. Auto-detected batch (`.manifest.json` in the WAV's directory).
5. Device default.

Pass `--no-manifest` to ignore steps 2–4 entirely.

For batch loads, the same logic applies per-entry: the entry's own
fields beat the batch-level `bpm`, which beats the device default.

---

## actually uploading

### a. CLI

```bash
# one sample
ppak-load-one kick.wav --project 1 --group A --pad 7 --slot 100 --bpm 120

# a batch — dry-run first, then run for real once the plan looks right
ppak-load-manifest ~/songs/summer-demos/.manifest.json \
  --project 9 --groups A=drums B=bass C=vocals D=other \
  --start-slot 300 --dry-run
```

`--groups GROUP=stem ...` tells the loader which stem feeds which pad
group. Use any subset (e.g. just `A=drums B=bass`); other stems are
skipped. Samples with `suggested_group` set in the manifest bypass
stem routing.

Other flags on `ppak-load-manifest`:

- `--start-slot N` (default 300) — first library slot to use. Slots
  are assigned sequentially per group: A gets `N..N+11`, B gets the
  next 12, etc.
- `--pads N` (default 12) — pads per group (1–12).
- `--no-bpm` — skip `sound.bpm` + `time.mode = bpm` tagging entirely.
  For samples that aren't tempo-locked loops.
- `--dry-run` — print the plan without touching the device.
- `--delay-ms N` — inter-message delay (default 10). Bump on slow hosts.

### b. Python API

For scripting (e.g. integrating with a DAW exporter), talk to
`EP133Client` directly:

```python
from ep133 import EP133Client
from ep133.commands import TE_SYSEX_FILE
from ep133.payloads import (
    PadParams, SampleParams,
    build_slot_metadata_set, pad_num_from_label,
)

with EP133Client.open() as client:
    # 1. Upload the WAV into a library slot.
    client.upload_sample("kick.wav", slot=100, name="kick 909")

    # 2. Tag the slot with source BPM + stretch mode (partial-merge write).
    payload = build_slot_metadata_set(100, SampleParams(bpm=107.666, time_mode="bpm"))
    rid = client._send(TE_SYSEX_FILE, payload)
    client._await_response(rid, timeout=5.0)

    # 3. Assign Project 1, Group A, pad "7" with playback params.
    client.assign_pad(
        project=1, group="A",
        pad_num=pad_num_from_label("7"),
        slot=100, params=PadParams(playmode="oneshot", time_mode="bpm"),
    )
```

`tools/load_one.py` is the canonical example of this 3-step pattern.

Worth knowing: `SampleParams` is **partial-merge** (only fields you
set are written; the rest stay), while `PadParams` is a **full
snapshot** (every field, including defaults, gets written each time).
Full settable surface in [`ep133/payloads.py`](../ep133/payloads.py).

---

## pad numbering, quick reference

There are two numbering conventions you'll run into:

- **Pad label** (what's printed on the pad) — `7`/`8`/`9`/`4`/`5`/`6`
  /`1`/`2`/`3`/`.`/`0`/`ENTER`. This is what `--pad` takes. Unambiguous;
  recommended.
- **SysEx pad_num** — 1..12, counted top-down, left-to-right. `pad_num=1`
  is the top-left pad (label `7`). `pad_num=12` is bottom-right (label
  `ENTER`). This is what `--pad-num` takes, and what the underlying
  Python API uses.

A third convention (`pNN`, bottom-up, used in TAR filenames inside a
`.ppak`) shows up if you ever crack one open. Don't conflate it with
`pad_num` — see [`docs/diagrams/01_pad_numbering.md`](diagrams/01_pad_numbering.md)
for the trap.

---

## see also

- [PROTOCOL.md §5](../PROTOCOL.md#5-sample-slot-json-metadata-17-fields)
  — every field the slot-metadata schema can carry.
- [PROTOCOL.md §6](../PROTOCOL.md#6-pad-json-metadata-12-fields) — the
  pad metadata schema (what `PadParams` writes).
- [`ep133/manifest.py`](../ep133/manifest.py) — the `SampleMeta` /
  `BatchManifest` schema.
- **Want a multi-pattern, multi-scene song-mode project from an
  arrangement?** Different pipeline — see `docs/MANIFEST.md` and the
  song-mode diagram at
  [`docs/diagrams/04_song_pipeline.svg`](diagrams/04_song_pipeline.svg).
