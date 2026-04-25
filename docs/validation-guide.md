# EP-133 BPM + Loop Test — What To Expect

A guide for validating that the `.ppak` your script generated actually
writes the BPM and loop-point bytes correctly.

## Risks & Pre-Flight Concerns

Before running the 12-pad matrix, know what's guessed vs verified:

| Part | Verification status |
|------|--------------------|
| Bytes 0-1 = sample slot u16 LE | ✅ verified (pad 6 `64 00` = slot 100) |
| Byte 8 = low-range BPM companion flag (0x20/0x00) | ✅ verified from BPM=92/100/150 captures |
| Byte 13 = override flag (0x80) | ✅ verified |
| Byte 14 = BPM byte (×2 low / ×1 high) | ✅ verified |
| Byte 15 = precision flag (0x00/0x80) | ✅ verified |
| Bytes 3-5 = trimLeft u24 LE (loopstart) | ⚠️ **phones24 only — UNVERIFIED by us** |
| Bytes 7-9 = trimRight u24 LE (loopend) | ⚠️ **phones24 only — UNVERIFIED by us** |
| Byte 20 = time.mode (0/1/2) | ⚠️ **phones24 says byte 21 (1-indexed) = byte 20 (0-indexed) — UNVERIFIED** |
| Float32 at +12..+15 not written (left zero) | ⚠️ Default pads show `00 00 70 42` (= 60.0) here. All-zero may produce playback glitches if device falls back to this field. |
| Override encoding works on a FRESHLY IMPORTED pad | ⚠️ We only verified override on one pad after on-device knob-save. Fresh `.ppak` import path may normalize/reject it. |
| `settings` as 222 zero bytes | ⚠️ Almost certainly wrong. Real settings file likely has project tempo / master volume / CRC. **Extract a real settings file from a Sample Tool backup** before shipping. |
| `device_sku=TE032AS001` | ⚠️ Hardcoded guess. Wrong SKU = `.ppak` rejected with cryptic error. Extract real SKU from `meta.json` of a Sample Tool backup. |
| Audio is NOT resampled to 46875 Hz mono s16 | ⚠️ Pads will play garbled if input WAV isn't already in that format. Safer: resample first via `stemforge.exporters.ep133.audio.wav_to_ep133_pcm`. |
| Empty patterns directory | ⚠️ Unknown if device requires a placeholder pattern file. |

## Recommended first test: ONE pad

Before the 12-pad matrix, do a **minimum-viable test**:

- Project 7, Group C, pad 1 only (slot 100, one WAV in `/sounds/`)
- BPM=120 (low-range, well inside safe territory, matches a common default)
- No loop region (loopstart=0, loopend=0)
- All other 47 pads empty / default
- **Copy the `settings` file from a real Sample Tool backup** rather than zeroing it

If that loads and plays at 120 BPM, then expand to the 12-pad matrix. If it
doesn't, the issue is in the base `.ppak` format, not our BPM encoding —
don't debug encoding against a broken container.

## ⚠️ Known failure: ERROR CLOCK 43 requires flash format

Observed 2026-04-24 on first attempt with the one-pad MVP `.ppak`:

- `.ppak` import via Sample Tool **appeared to succeed** (no error from the browser)
- On device: `ERROR CLOCK 43` after load
- **Persisted across power cycles** — the device auto-loads project 7 at boot and repeatedly hit the same error
- **Required SHIFT+ERASE flash format** to recover (lost all samples and projects)

Most likely cause: the 222-zero-byte `settings` file, or TAR mtimes of 0
from Python's `tarfile.TarInfo` default, or a future `generated_at`
timestamp in `meta.json` that the device's clock validator rejects.

**Do NOT generate and import another synthetic `.ppak` without first
extracting a real backup from the device via Sample Tool and copying its
`settings` file verbatim.** The write side of this protocol is too
footgun-prone to approximate.

## Stage 0 — Before Upload (verify ZIP structure)

Open a terminal and run:

```bash
unzip -l ep133_bpm_test.ppak
```

Should show (with leading slashes on each path):

```
/meta.json
/projects/P07.tar
/sounds/100 <audiofile>.wav
/sounds/101 <audiofile>.wav
...through...
/sounds/111 <audiofile>.wav
```

If any of those are missing, pathed wrong, or missing the leading slash,
Sample Tool will reject the file without a useful error message. Fix the
packing before bothering to open the tool.

## Quick reference

| Pad | BPM  | Loop region | Encoding mode |
|-----|------|-------------|---------------|
|  1  |  60  | full        | low-range (×2) |
|  2  |  80  | full        | low-range (×2) |
|  3  | 100  | full        | low-range (×2) |
|  4  | 120  | full        | low-range (×2) |
|  5  | 130  | last half   | high-range (×1) |
|  6  | 140  | last half   | high-range (×1) |
|  7  | 150  | last half   | high-range (×1) |
|  8  | 160  | last half   | high-range (×1) |
|  9  | 170  | middle      | high-range (×1) |
| 10  | 180  | middle      | high-range (×1) |
| 11  | 190  | middle      | high-range (×1) |
| 12  | 200  | middle      | high-range (×1) |

## Stage 1 — In EP Sample Tool (browser, before upload)

After dragging `ep133_bpm_test.ppak` into Sample Tool:

**You should see:**
- 12 sample slots populated (slots 100-111), all named like `100 yourfile.wav`
- Project 7 listed
- All 4 groups (A/B/C/D) but only Group C populated

**The waveform view per pad** is the key visual check:

- **Pads 1-4:** Waveform shows the entire sample, no trim markers
- **Pads 5-8:** Waveform shows only the right half — trim marker should be at 50%, end at 100%
- **Pads 9-12:** Waveform shows only the middle — trim markers at 25% and 75%

If the waveforms all look identical (full sample everywhere), **the loop-point bytes didn't write correctly.** This is the most likely failure mode for a first try, since the byte offsets for `loopstart`/`loopend` were inferred from the spec rather than independently verified.

**Sample Tool usually displays per-pad BPM somewhere too** — check the pad inspector or properties panel for each pad. The values shown should match the table above.

## Stage 2 — On-device

After upload, on the device:

1. Press **Project** button, navigate to **P07**, load it
2. Press **Group C** button
3. Press individual pads to trigger them

**What to listen for:**

- All pads play the same source audio
- BUT each pad should stretch/play at a different effective tempo
- The clearest test: **tap pad 1 (60 BPM), then pad 12 (200 BPM)** — the difference should be obvious. Pad 12 should sound much faster/higher-pitched (depending on time-stretch behavior) than pad 1
- The boundary test: **pad 4 (120 BPM) → pad 5 (130 BPM)** crosses the encoding boundary at 128. Both should sound natural and progressively faster. If pad 5 sounds wildly wrong (silence, glitch, half-speed), the high-range encoding has a bug
- Pads 5-8 should play only the back half of the audio
- Pads 9-12 should play only the middle section

**If a pad doesn't play:** the sample slot assignment didn't take. Check that the audio file got correctly placed in `/sounds/` with the expected name format.

**If a pad plays but doesn't stretch tempo:** the BPM was written but `time.mode` isn't set to "bpm" — the script doesn't set this currently. You may need to manually toggle each pad to BPM mode using `SHIFT + TIME` or whatever the device's combo is.

## Stage 3 — Reading the device back via SysEx

Once loaded, you can use your own `project_reader.py` to read Project 7 back and confirm the bytes you wrote actually round-tripped correctly.

For each Group C pad, check:
- Bytes 0-1 should be `slot_number` (LE u16) — 100, 101, 102, ... 111
- Bytes 8, 13, 14, 15 should match the override encoding for the BPM
- Bytes 3-5 (trim left) and 7-9 (trim right) should encode the loop offsets

If bytes 13-15 show your written values but the device played the wrong tempo, the encoding works but `time.mode` is the missing piece. If bytes 13-15 don't match what you wrote, the import path normalized them somehow.

## Common failure modes and what they mean

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `.ppak` rejected with "PAK FILE IS EMPTY" | ZIP paths missing leading slash | Already handled in script |
| `.ppak` rejected with SKU error | Wrong device_sku | Pass real SKU as 3rd arg |
| Project loads but pads silent | `time.mode` not set to "bpm" | On-device fix; or extend script |
| All pads same tempo | BPM bytes didn't write | Check SysEx readback of bytes 13-15 |
| Loop regions all show full sample | Loop point byte offsets wrong | Try different offsets for trim |
| Pad 5 (BPM 130) silent or broken | High-range encoding bug | Verify byte 15 = 0x80 written |
| Pad 4 (BPM 120) silent or broken | Low-range encoding bug | Verify byte 15 = 0x00 written |

## What success looks like, in one sentence

You see three distinct waveform shapes in Sample Tool, and you hear twelve different tempos when you tap pads 1-12 on the device.
