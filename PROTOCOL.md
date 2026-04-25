# EP-133 K.O. II — Complete SysEx Protocol & .ppak Format Reference

**Version:** 2026-04-25 (post-RE-session)
**Firmware tested against:** OS 2.0.5
**Author:** zak@raindog.ai (StemForge project)

A reverse-engineered specification of the Teenage Engineering EP-133 K.O. II's
SysEx protocol, internal `.ppak` archive format, and on-disk binary pad-record
layout. Compiled from live USB-MIDI device probing, EP Sample Tool emit
diffs, and cross-checks against [phones24's open-source archive parser](https://github.com/phones24).

This document was distilled from a 2-day RE session inside the StemForge project; an earlier draft
(2026-04-24) — the older draft inherited several incorrect byte offsets from
phones24's notes and from corrupted live-SysEx reads. Today's findings come
from byte-level diffing of two real Sample Tool backups (empty device vs.
post-pad-assignment), which is the cleanest source available for the binary
formats.

---

## TL;DR — what's verified, what's not

This is a community RE document. Prior public sources got some details
wrong; we got some details wrong before today; **this document marks
verification status field-by-field**. Use accordingly.

**What's verified by direct byte-level testing:**
- All wire framing (manufacturer ID, command bytes, request-id flags, 7-bit packing)
- FileId hierarchy formula (`pad_fid = 3200 + (project-1)×1000 + group_idx×100 + pad_num`)
- All FILE sub-commands and their behaviors
- The complete 17-field sample-slot JSON metadata schema (paginated `FILE_METADATA_GET`)
- The 12-field pad JSON metadata schema
- `.ppak` ZIP structure, internal TAR layout, `meta.json` field list
- Pad binary record byte layout for offsets 1, 8-11, 12-15, 16, 20, 21, 23, 24
  (verified by diffing two real Sample Tool backups)
- BPM override encoding at bytes 13-15 (3 distinct on-device captures)
- Playmode ↔ envelope.release coupling
- Integer-vs-string accept/reject at write time

**What's inferred or uncertain:**
- Pad binary record bytes 2-7 (likely midi channel + trim regions, but no diff data)
- Pad binary record bytes 22 (chokeGroup), 25-26
- The exact semantics of `sound.bars` clamping
- Whether `sound.loopstart`/`loopend` ever auto-loop or are pure trim
- Project-file write path (write sub-command unmapped, probing wedges device)
- Pattern/scene storage location

**What's a published correction vs. existing public sources:**
- phones24's pad-record offset table is **shifted by 1-2 bytes** vs. real format
- phones24's `pads/x/pNN` numbering convention isn't documented but is **bottom-up
  (p01 = "."), not top-down**
- Sample Tool's `.ppak` format has **no separate `settings` file** in the project TAR;
  trying to add one triggers ERROR CLOCK 43 on import (recovered only by SHIFT+ERASE format)

---

## 1. Wire Framing

Every SysEx message to/from the device:

```
F0 00 20 76 33 40 <flags> <req_lo> <cmd> <packed_payload> F7
   ^─────┬─────┘ ^┬ ^─┬─┘  ^─┬─┘    ^─┬─┘  ^──────┬──────┘
         │       │   │       │        │             │
    TE mfg ID    │  flags/req_id_hi    │       7-bit packed data
    (00 20 76)   │  bit6=is_request    │
                 │  bit5=has_req_id    │
       identity (always 0x33)          command (top-level)
       TE constant (always 0x40)
```

- `F0` / `F7`: standard MIDI SysEx start/end
- `00 20 76`: Teenage Engineering manufacturer ID
- `0x33`: identity code (specific to EP-133 K.O. II)
- `0x40`: TE constant
- Byte 6 flags: `BIT_IS_REQUEST=0x40`, `BIT_REQUEST_ID_AVAILABLE=0x20`,
  low 5 bits = high bits of 12-bit request ID
- Byte 7: low 7 bits of request ID
- Byte 8: top-level command (§3)
- Bytes 9..−2: 7-bit packed payload (§2)
- Byte −1: `F7`

## 2. 7-bit Packing

MIDI SysEx payload bytes must be ≤ `0x7F`. The EP-133 packs raw 8-bit data
into 9-byte groups: 1 flag byte + 7 data bytes. Flag byte bit `N` set ⇒
data byte `N` had its high bit set originally (restore before decoding).

Trailing partial group (< 7 bytes) still carries its own flag byte.

**Reference impl:** [`ep133/packing.py`](ep133/packing.py)
— `pack_to_buffer()`, `unpack_in_place()`, `packed_length()`.

## 3. FileId Hierarchy

The device exposes a filesystem-like namespace via 16-bit fileIds.

| fileId (hex) | fileId (dec) | Meaning |
|--------------|--------------|---------|
| `0x03E8` | 1000 | `sounds/` directory root |
| `<slot>` | 1..999 | individual sample slot |
| `0x07D0` | 2000 | `projects/` directory root |
| `0x0BB8 + (N-1)×1000` | 3000 + (N-1)×1000 | project N (1..99) |
| `project_fid + 100` | | `groups/` directory cursor |
| `project_fid + 200/300/400/500` | | group A/B/C/D cursor |
| `group_cursor + pad_num` | | pad fileId |

**Pad fileId formula** (verified against 8 fixtures):

```
pad_fid = 3200 + (project - 1) × 1000 + group_index × 100 + pad_num
        = group_cursor + pad_num
```

Where `group_index` ∈ {A=0, B=1, C=2, D=3}, `pad_num` ∈ {1..12}.

### 3.1 Pad numbering — TWO DIFFERENT CONVENTIONS

Important pitfall, not previously documented. The EP-133 uses **two different
pad numbering conventions** for the same physical 4×3 pad grid:

**SysEx `pad_num` convention** (used in `assign_pad`, `FILE_METADATA_SET` to
pad fileIds, etc.) — *top-down, left-right*:

```
row 0 (top):     pad_num 1="7"    2="8"    3="9"
row 1:           pad_num 4="4"    5="5"    6="6"
row 2:           pad_num 7="1"    8="2"    9="3"
row 3 (bottom):  pad_num 10="."  11="0"   12="ENTER"
```

**Project TAR `pads/x/pNN` convention** (filename-based, inside the project
archive) — *bottom-up, left-right*:

```
row 3 (bottom):  p01="."   p02="0"   p03="ENTER"
row 2:           p04="1"   p05="2"   p06="3"
row 1:           p07="4"   p08="5"   p09="6"
row 0 (top):     p10="7"   p11="8"   p12="9"
```

So `pads/c/p01` (bottom-left of group C) corresponds to `assign_pad(group="C",
pad_num=10)`. Translation table:

| TAR pNN | SysEx pad_num | Label |
|---|---|---|
| 01 | 10 | "." |
| 02 | 11 | "0" |
| 03 | 12 | "ENTER" |
| 04 | 7 | "1" |
| 05 | 8 | "2" |
| 06 | 9 | "3" |
| 07 | 4 | "4" |
| 08 | 5 | "5" |
| 09 | 6 | "6" |
| 10 | 1 | "7" |
| 11 | 2 | "8" |
| 12 | 3 | "9" |

The user-facing "first pad" (bottom-left, label ".") is the natural starting
point for the bar_index → pad_num mapping at
[`tools/load_from_manifest.py`](tools/load_from_manifest.py).

Why this matters: when patching a `.ppak` programmatically (writing pad
records into the TAR), `pads/c/p01` is the bottom-left pad. When sending
SysEx pad assignments to the same physical pad, you write to `pad_num=10`.
Mixing these up silently lands assignments on the wrong physical pad.

## 4. Top-level Commands

Command byte at SysEx frame byte 8.

| Byte 8 | Name | Notes |
|--------|------|-------|
| `0x01` | `GREET` | No payload; first message of a session |
| `0x05` | `FILE` | All filesystem operations |

### FILE sub-commands

| Sub-cmd | Args | Purpose | Safe? |
|---|---|---|---|
| `0x01 <flags:u8> <max_resp_len:u32 BE>` | FILE_INIT | flags=0 read, flags=1 write | ✅ |
| `0x02 0x00 <metadata>` | FILE_PUT_META | Create file + metadata | ✅ for samples |
| `0x02 0x01 <page:u16 BE> <data>` | FILE_PUT_DATA | Stream data | ✅ for samples |
| `0x03 0x00 <fid:u16 BE> <offset:u32 BE>` | FILE_READ_OPEN | Open for raw read; offset must be 0 | ⚠️ Only for known-readable fileIds |
| `0x03 0x01 <page:u16 BE>` | FILE_READ_DATA | Stream 327-byte page; short = EOF | ✅ if a file is open |
| `0x04 0x00 0x00 <fid:u16 BE>` | GROUP_DUMP | Returns 151-byte list of pads in a group cursor | ✅ |
| `0x05 0x01 <fid:u16 BE> <pad bytes>` | PLAY | Trigger a pad/slot | ✅ |
| `0x06 0x02 <fid:u16 BE>` | FILE_DELETE | Destructive. | ❌ |
| `0x07 0x01 <fid:u16 BE> <json bytes> 0x00` | FILE_METADATA_SET | Partial JSON write | ✅ |
| `0x07 0x02 <fid:u16 BE> 0x00 <page:u8>` | FILE_METADATA_GET | JSON read, paginated | ✅ |
| `0x0B <fid:u16 BE>` | STAT | Filename + small metadata; safe for any fileId | ✅ |

### Response framing

- Success: `BIT_IS_REQUEST` cleared. First unpacked byte = status: `0x00` OK,
  `0x01` error followed by ASCII reason.
- Observed error strings: `invalid id`, `invalid area`, `invalid offset`,
  `unknown command`, `not initialized`, `failed to delete`, `sample load
  failed`, `offset not supported for raw`, `directory not op…` (truncated).

## 5. Sample-Slot JSON Metadata (17 fields)

Accessed at fileId = slot number (1..999). `FILE_METADATA_GET`/`SET` work
with ASCII JSON. Reads paginated; append `<page:u8>` to the GET payload
(`00 00` = page 0). Writes are **partial-merge**: only included fields are
modified.

| Field | Type | Range / values | Writable? | Honored at playback? |
|---|---|---|---|---|
| `channels` | u8 | 1 or 2 | no (set at upload) | yes |
| `samplerate` | u32 | e.g. 46875 | no (set at upload) | yes |
| `format` | string | `"s16"` | no | yes |
| `crc` | u32 | computed | no | yes |
| `sound.loopstart` | i32 | -1 = none | ✅ | **trim-only**; does NOT cause held-key auto-loop |
| `sound.loopend` | i32 | -1 = none | ✅ | trim-only (see above) |
| `name` | string ≤20 ASCII | filename | ✅ | display only |
| `sound.amplitude` | u8 | 0..100 | ✅ | yes |
| `sound.playmode` | string | `"oneshot"` / `"key"` / `"legato"` | ✅ string only | gate behavior |
| `sound.pan` | i8 | -16..16 | ✅ | yes |
| `sound.pitch` | float | semitones -12..12 | ✅ | yes |
| `sound.rootnote` | u8 | 0..127 (default 60) | ✅ | yes for keys mode |
| `time.mode` | string | `"off"` / `"bar"` / `"bpm"` | ✅ string only | yes (stretch) |
| `sound.bpm` | float | 1.0..200.0 (240 rejected) | ✅ | yes via stretch math |
| `sound.bars` | float | clamped to power-of-2 | ✅ | inferred from length+bpm if not set |
| `envelope.attack` | u8 | 0..255 | ✅ | yes |
| `envelope.release` | u8 | 0..255 | ✅ | yes; **must pair with playmode** |

### Time-stretch semantics (`time.mode = "bpm"`)

```
playback_speed = project_bpm / sound.bpm
```

- `sound.bpm < project_bpm`: device interprets sample as "slow source" → speeds it up
- `sound.bpm > project_bpm`: device interprets sample as "fast source" → slows it down
- `sound.bpm == project_bpm`: native speed
- Inferred bar count: `bars = audio_seconds × sound.bpm / 240` (4/4 assumed)

Counterintuitive consequence: setting all your loops' `sound.bpm` to the
true recording tempo and the device's project tempo to the same value gives
1.0× playback. This is the **correct configuration** for "loop at native
speed" — a recorded 4-bar loop will play in exactly 4 bars at any project
tempo.

### Playmode ↔ envelope.release coupling

Writing `sound.playmode` alone does NOT gate playback unless paired with the
matched `envelope.release` in the same write. On-device UI always writes
both atomically:

| playmode | paired envelope.release | effect |
|---|---|---|
| `"oneshot"` | 255 | plays full, long tail |
| `"key"` | 15 | gates hard on release |
| `"legato"` | (inherits prior) | smooth retrigger |

[`SampleParams`](ep133/payloads.py) auto-pairs these
unless the caller sets `release=` explicitly.

## 6. Pad JSON Metadata (12 fields)

Accessed at a pad's fileId (§3). Same `FILE_METADATA_GET`/`SET` interface.

```json
{
  "sym": 100,                    // sample slot this pad plays
  "sound.playmode": "oneshot",   // string; rejected if int
  "sample.start": 0,             // u32 sample-frame index
  "sample.end": 336870,          // u32; omitted if no trim
  "envelope.attack": 0,          // u8 0..255
  "envelope.release": 255,       // u8; MUST pair with playmode
  "sound.pitch": 0.00,           // float, semitones
  "sound.amplitude": 100,        // u8 0..100
  "sound.pan": 0,                // i8 -16..16
  "sound.mutegroup": false,      // bool
  "time.mode": "off",            // string
  "midi.channel": 0              // u8 0..15
}
```

No per-pad BPM in this schema — that lives in the binary record (§7).

## 7. Pad Binary Record (27 bytes, in project TAR)

**This section supersedes prior published research.** phones24's offset
table is shifted by 1-2 bytes vs. the actual format; this layout was
verified 2026-04-25 by diffing the same project on the same device, before
and after a single sample assignment via Sample Tool's UI.

Default-blank pad (verbatim from real backup, 48 of these in any project):

```
00 00 00 00 00 00 00 00  00 00 00 00 00 00 f0 42
64 00 00 00 ff 00 00 00  3c 00 00
```

Field layout (0-indexed, relative to record start):

| Offset | Field | Default (blank pad) | Verified? |
|---|---|---|---|
| 0 | (zero) | `0x00` | implicit |
| **1** | **slot u8** | `0x00` (no sample) | ✅ verified by diff |
| 2 | midiChannel | `0x00` | ⚠️ likely (phones24) |
| 3-5 | trimLeft u24 LE | zeros | ⚠️ phones24, untested |
| 6 | (unknown) | `0x00` | ⬜ |
| 7-9 | trimRight u24 LE | zeros | ⚠️ phones24, untested |
| 10-11 | (unknown) | `0x00` | ⬜ |
| **8-11** | **sample length frames u32 LE** (overrides phones24's interpretation of bytes 7-9) | zeros | ✅ verified by diff (99,328 frames for 8.916s @ 44.1kHz native = match) |
| **12-15** | **BPM float32 LE** (when override flag at +13 ≠ 0x80) | `00 00 f0 42` = 120.0 | ✅ verified |
| **13-15** | **OVERRIDE BPM** (when byte +13 = 0x80) | (none in default) | ✅ verified |
| 16 | volume u8 | `0x64` = 100 | ✅ verified by diff |
| 17 | pitch i8 | `0x00` | ⚠️ position likely correct |
| 18 | pan i8 | `0x00` | ⚠️ |
| 19 | attack u8 | `0x00` | ⚠️ |
| 20 | release u8 | `0xff` = 255 | ✅ verified by diff (matches JSON default) |
| 21 | time.mode u8 | `0x00` = off | ✅ verified by diff (matches JSON default) |
| 22 | inChokeGroup u8 | `0x00` | ⚠️ |
| 23 | playMode u8 (0=oneshot, 1=key, 2=legato) | `0x00` | ✅ verified by diff (matches JSON default) |
| 24 | rootNote u8 | `0x3c` = 60 | ✅ verified by diff |
| 25-26 | (unknown) | zeros | ⬜ |

### 7.1 BPM override encoding (bytes 13-15)

When byte +13 = `0x80`, the device uses an override-BPM interpretation,
ignoring the float32 at +12..+15.

| Range | byte +13 | byte +14 | byte +15 | Decoded BPM |
|---|---|---|---|---|
| Low (BPM < 128) | `0x80` | `bpm × 2` | `0x00` | `byte_14 / 2` |
| High (BPM ≥ 128) | `0x80` | `bpm` | `0x80` | `byte_14` |

Verified across three on-device captures (pad C-9 at BPMs 92/100/150).

### 7.2 BPM float32 (bytes 12-15) — Sample Tool's path

When Sample Tool assigns a sample to a pad via its UI, it writes:

- byte +1 = slot
- bytes +8..+11 = sample length in transcoded frames (u32 LE)
- bytes +12..+15 = BPM as IEEE 754 float32 LE — **NOT divided by 2**, despite
  earlier hypotheses
- All other bytes inherit defaults from the blank-pad template

The "BPM/2" hypothesis from earlier RE work was based on a single corrupted
data point; today's clean diff shows the float32 is BPM directly.

### 7.3 Diff method (reproducible)

Anyone can verify or extend this layout with two `.ppak` exports from the
same device:

1. Sample Tool **Backup** → save → call this `before.ppak`
2. Make a single change in Sample Tool's UI (assign a sample to a pad, set
   a knob, etc.)
3. Sample Tool **Backup** → save → call this `after.ppak`
4. `unzip` both, then `cmp -l before/projects/PXX.tar after/projects/PXX.tar`
5. Diffed bytes lie in exactly the pad record(s) that changed; subtract
   the pad's data offset to get field offsets

This is how the §7 table above was filled in.

## 8. Project File Structure

Each project is a TAR archive (~53 KB for a populated project):

```
pads/                 (dir, mode 0755, mtime 0)
pads/a/               (dir)
pads/a/p01            (file, 27 bytes — pad binary record §7)
pads/a/p02
... pads/a/p12
pads/b/p01..p12       (same)
pads/c/p01..p12
pads/d/p01..p12
patterns/             (dir, empty in all observed exports)
```

Total: 5 dir entries + 48 pad-record file entries + 1 patterns dir = 54 entries.

**No `settings` file inside the TAR** — adding one in our generator triggered
ERROR CLOCK 43 on import, which persisted across power cycles and required
SHIFT+ERASE flash format to recover. Don't add files the format doesn't have.

### 8.1 TAR header format

Sample Tool emits a minimal-padding ustar variant. Most fields beyond
`name`, `mode`, `size`, `chksum`, `magic`, and `typeflag` are zeroed.
Python's standard `tarfile` module emits a more verbose form (with
`0000000` octal padding); both are valid TAR per spec, and the device
accepts both.

- All file mtimes: 0 (Unix epoch — Sample Tool emits this; not a bug)
- All dir entries use mode 0755
- File entries use mode 0644
- Directory names: NO trailing slash (Python's tarfile adds one; functionally
  equivalent)

### 8.2 Project read procedure

```
1. FILE_INIT(flags=0)                              # enter read mode
2. 03 00 <project_fid:u16 BE> <offset:u32 BE = 0>  # open; offset MUST be 0
3. while True:
       03 01 <page:u16 BE>
       if response shorter than 327 bytes: EOF
4. strip 3-byte page header (00 00 NN) from each page; concat
```

Implementation: [`ep133/project_reader.py`](ep133/project_reader.py)
`read_project_file()`.

### 8.3 Project write path — UNKNOWN

`FILE_INIT(flags=1)` + `03 00 <project_fid>` is accepted by the handshake,
but the data-phase sub-command is unmapped. Speculative probing wedged the
device twice during discovery, requiring power cycle. The known path for
writing whole projects is the `.ppak` archive import flow (§9), not live
SysEx.

## 9. .ppak Archive Format

`.ppak` is a ZIP archive containing one project, its samples, and metadata.
Sample Tool's "Backup" emits these; "Upload" / "Load Project" imports them.

### 9.1 ZIP structure

```
/projects/PXX.tar       (TAR per §8)
/sounds/<slot> <name>.wav
... (one .wav entry per slot referenced)
/meta.json
```

- All ZIP entry paths have a leading `/` (Sample Tool emits this; required
  for Load Project to recognize the file)
- ZIP entry order in real exports: `projects` → `sounds` → `meta`. Order
  shouldn't matter to the parser, but matching is safer.
- Entry mtimes: current time at export
- Compression: deflate

### 9.2 meta.json (10 fields)

```json
{
  "info": "teenage engineering - pak file",
  "pak_version": 1,
  "pak_type": "project",
  "pak_release": "1.2.0",
  "device_name": "EP-133",
  "device_sku": "TE032AS001",
  "device_version": "2.0.5",
  "generated_at": "2026-04-25T02:31:04.894Z",
  "author": "computer",
  "base_sku": "TE032AS001"
}
```

- `pak_type`: `"project"` for single-project exports (Sample Tool's emit);
  `"user"` exists but is rejected by Load Project on inspection — intended
  semantics unclear
- `device_sku` / `base_sku`: model identifier `TE032AS001` for K.O. II
- `generated_at`: ISO 8601 with millisecond precision; Sample Tool refuses
  to import paks with stale or stub timestamps (we hit this initially)
- `author`: arbitrary; `"computer"` is what Sample Tool emits

### 9.3 WAV format inside .sounds/

- 44.1 kHz, **stereo**, 16-bit PCM (Sample Tool's user-facing native format;
  it transcodes to 46875 Hz mono internally on upload)
- Naming: `<slot> <name>.wav` (note space-separated, not `<slot>_<name>`)

A `.ppak` containing a 46875 Hz mono WAV (the device's internal transcoded
format) is silently rejected by Sample Tool's parser — verified.

### 9.4 Generating a load-safe .ppak (recommended approach)

Don't synthesize from scratch. Take a real `.ppak` from a Sample Tool
Backup as a base, modify only the bytes that need to change, and re-zip.
This guarantees format conformance.

The `ep133.ppak.writer` module does exactly this. Our v1/v2
attempts to build from scratch (matching format on every dimension we'd
documented) were silently rejected by Sample Tool's parser. Path B
(byte-clone from real) succeeded on the first try.

## 10. Behavioral gotchas

### 10.1 String-only enums (writes)

The device EMITS integer values for `sound.playmode` and `time.mode` in
unsolicited broadcasts after on-device UI changes. Writing those integers
back via `FILE_METADATA_SET` returns `status=1` error. **Always write strings.**

| Field | Accepted on write |
|---|---|
| `sound.playmode` | `"oneshot"`, `"key"`, `"legato"` |
| `time.mode` | `"off"`, `"bar"`, `"bpm"` (note: singular `"bar"`, not `"bars"`) |

Integer enum values seen in responses (for parsers):
- `sound.playmode`: 0=oneshot, 1=key, 2=legato
- `time.mode`: 0=off, 1=bpm, 2=bar

### 10.2 Silently-inert fields

Some fields accept writes but don't affect playback:
- `sound.playmode` written without paired `envelope.release` (§5)
- `time.bpm` — not a real field; ACKs but ignored
- The slot's `sound.bpm` is overridden by the pad's binary BPM (§7) when both are set

### 10.3 Partial writes can clobber

`FILE_METADATA_SET` writes are partial-merge (unspecified fields keep current
values). BUT — writing partial fields to a PAD fileId can cause the device
to **re-sync all pad fields from the pad's sample slot**, clobbering other
overrides. Don't assume a partial write leaves everything else untouched.

### 10.4 Continuous loop affordance is not what you'd guess

`sound.loopstart`/`loopend` are **trim regions only**, not held-key auto-loop
markers. The EP-133's documented "play this loop continuously" technique is
**Note Repeat hold**:

1. Press `TIMING`
2. Hold `SHIFT` + tap pad
3. Sets per-bar retrigger via `KNOB X` interval

Or use the sequencer with a multi-bar pattern. The on-device "LOOP" button
is OB-4-style performance looping, separate from per-sample playback.

## 11. Safety rules (for live SysEx development)

Hard-won from two ERROR 8200 incidents (device wedge requiring power cycle):

1. **Never `03 00 <fileId>` without first confirming `0B <fileId>` returns
   non-`invalid id`.** Speculative opens accumulate state.
2. **Never `FILE_INIT(flags=1)` + `03 00` on unknown fileIds.** Open write
   handles wedge the device.
3. **`06 02 <fileId>` is FILE_DELETE — destructive.**
4. **`0B <fileId>` is always safe.** Use to enumerate.
5. **Power-cycle (not USB unplug) on error state.** Internal state doesn't
   clear on USB reset.
6. **Never synthesize a `.ppak` from scratch without a real-backup reference
   for the project TAR shape.** The CLOCK 43 incident persisted across power
   cycles and required SHIFT+ERASE flash format. Patch from real exports
   instead.

## 12. Comparison vs. existing public sources

### phones24

The most comprehensive public source as of writing
([github.com/phones24](https://github.com/phones24)). Covers:
- Wire framing, manufacturer ID, command byte map
- 7-bit packing
- FileId structure (samples and projects)
- Initial pad-record byte interpretation
- `.ppak` ZIP overview

**Where this document supplements / corrects:**
- **Pad-record byte offsets**: phones24's table places `slot` at bytes 0-1
  LE, `volume` at 15, `release` at 19, `time.mode` at 20, `playMode` at 22.
  Real format (verified by diff): slot at byte **1** (u8 — not LE u16),
  length at **8-11**, volume at **16**, release at **20**, time.mode at
  **21**, playMode at **23**. All offsets shifted by 1-2 bytes.
- **`pads/x/pNN` numbering**: phones24 doesn't document the bottom-up
  convention vs. SysEx top-down convention (§3.1).
- **`.ppak` Sample Tool emit format**: phones24 has the ZIP shell but not
  the `meta.json` field list, the timestamp requirements, the absent-on-
  purpose `settings` file (CLOCK 43 trigger), or the WAV format
  requirement (44.1 kHz stereo, not the device's internal 46875 mono).
- **Behavioral gotchas**: integer-vs-string accept on writes, playmode/
  release coupling, and the partial-write clobber risk are documented
  here for the first time we know of.
- **Time-stretch math** for `time.mode=bpm` and the consequence that
  `sound.bpm` should match the loop's true recorded tempo (not be
  invented as a "play faster" knob).

### TE official docs

The user-facing manual covers playback, sequencing, and FX, but does not
expose the SysEx protocol or `.ppak` format. The Sample Tool web app's
JavaScript bundle has the protocol details obfuscated; phones24 reverse-
engineered most of it from there.

### Recommendation for publishing this work

Publishing as a complementary doc with credit to phones24 is the cleanest
move:

1. Lead with what's new/corrected in this doc (the diff-method + corrected
   pad-record offsets are the most valuable contributions)
2. Direct readers to phones24 for the parts of the protocol both repos
   cover, to avoid duplicate maintenance burden
3. Encourage cross-pollination — phones24's archive parser can be patched
   with the corrected offsets here

## 13. Implementation map (StemForge Python lib)

| Module | Purpose |
|---|---|
| [`ep133/commands.py`](ep133/commands.py) | Command bytes, sub-cmd bytes, pad fileId formula, label maps |
| [`ep133/sysex.py`](ep133/sysex.py) | Frame build/parse, request id allocator, status codes |
| [`ep133/packing.py`](ep133/packing.py) | 7-bit pack/unpack |
| [`ep133/transport.py`](ep133/transport.py) | MIDI port discovery + I/O |
| [`ep133/client.py`](ep133/client.py) | High-level `EP133Client.upload_sample`, `.assign_pad`, session |
| [`ep133/payloads.py`](ep133/payloads.py) | `PadParams`, `SampleParams`, `build_*` payload builders |
| [`ep133/audio.py`](ep133/audio.py) | WAV → 46875 Hz mono PCM transcode |
| [`ep133/transfer.py`](ep133/transfer.py) | Upload message-sequence generator |
| [`ep133/project_reader.py`](ep133/project_reader.py) | Live project TAR read via SysEx |
| [`ep133/pad_record.py`](ep133/pad_record.py) | TAR scan + per-pad BPM decoder |
| [`tools/load_from_manifest.py`](tools/load_from_manifest.py) | CLI: bulk-load curated manifest into a project (with per-slot BPM tagging) |
| [`tools/bpm_matrix.py`](tools/bpm_matrix.py) | CLI: 12 pads × 12 BPMs via slot replication |
| [`ep133/ppak/writer.py`](ep133/ppak/writer.py) | `.ppak` generator using a real backup as base |

Tests: 100+ in `tests/ep133/`, all passing.

## 14. Open questions / future work

1. **Float32 BPM scaling** — verified that Sample Tool stores BPM directly
   (not BPM/2). Independent on-device captures should reconfirm with more
   data points.
2. **Project-file write sub-command** — `03 00` opens but data phase
   unmapped. Live writes would unlock fully programmatic per-pad bytes
   without the `.ppak` round-trip.
3. **Pad-record bytes 2-7, 22, 25-26** — diff method (§7.3) can resolve
   each one with targeted Sample Tool UI changes.
4. **Pattern and scene storage** — the project TAR we read stops at
   `pads/d/...` and `patterns/`; sequencer data must live elsewhere.
5. **Multi-project switching** — is there a "current project" pointer
   readable/writable to switch projects programmatically?
6. **`sound.bars` clamping behavior** — observed device ignores fractional
   bar counts and clamps to power-of-2; exact formula unverified.

## 15. Acknowledgments

- **phones24** for the foundational protocol RE that bootstrapped this
  work. Their archive parser is the reason the `.ppak` format was tractable
  at all. The corrections here are corrections to specific byte offsets,
  not to the broader work.
- **Teenage Engineering** for shipping a device whose USB-MIDI implementation
  is consistent enough to RE without too many corner cases (and whose Sample
  Tool web UI loads its protocol code unminified-enough to inspect).

---

*This document is community RE; expect errors. Verify against real device
output before using for anything load-bearing. PRs / corrections welcome.*
