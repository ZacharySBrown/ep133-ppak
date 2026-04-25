# Verifying / extending pad-record byte offsets — the diff method

The pad-record byte layout in [PROTOCOL.md §7](../PROTOCOL.md#7-pad-binary-record-27-bytes-in-project-tar)
was reverse-engineered by diffing two `.ppak` exports from the same device,
before and after a single UI change in Sample Tool. This method is
reproducible: anyone with an EP-133 and Sample Tool can verify the
offsets in this doc, or extend them to fields we haven't decoded yet
(bytes 2-7, 22, 25-26).

## What you need

- An EP-133 K.O. II (any firmware; recorded against OS 2.0.5)
- Chrome with WebMIDI access to
  [teenageengineering.com/apps/ep-sample-tool](https://teenageengineering.com/apps/ep-sample-tool)
- `unzip`, `cmp`, and a hex dumper (`xxd`, `hexdump`, etc.)

## Procedure

1. **Take a baseline backup**

   Connect the device to Sample Tool. Hit **Backup**. Save as
   `before.ppak`.

2. **Make exactly one UI change**

   For decoding a single field, change one specific thing in the UI:

   - To verify the slot field: assign a sample to a pad
   - To verify the pitch field: change pitch on a pad
   - To verify the loop region: set sample.start / sample.end via trim
   - For an unknown field: rotate just that knob in the UI, save

3. **Take a second backup**

   Hit **Backup** again. Save as `after.ppak`.

4. **Compare the project TARs**

   ```bash
   unzip -o before.ppak -d before/
   unzip -o after.ppak  -d after/

   cmp -l before/projects/PXX.tar after/projects/PXX.tar
   ```

   Output looks like:
   ```
    33282   0 144
    33290   0 204
    33291   0   1
    33293   0 110
    33294   0 322
    33295 360 237
   ```

   Each line: `<byte_position> <old_octal> <new_octal>`.
   `cmp` uses 1-indexed positions; subtract 1 for 0-indexed.

5. **Locate the affected pad record**

   Pad records sit at predictable offsets in the TAR. Walk it:

   ```python
   pos = 0
   while pos + 512 <= len(tar):
       hdr = tar[pos:pos+512]
       name = hdr[:100].rstrip(b"\x00").decode("ascii", errors="replace")
       size = int(hdr[124:135].rstrip(b"\x00 ") or b"0", 8)
       typeflag = chr(hdr[156]) if hdr[156] else "0"
       data_start = pos + 512
       if name.startswith("pads/") and len(name) == 10:
           record = tar[data_start:data_start+27]
           # check if this pad's data range contains a diff
           ...
       blocks = (size + 511) // 512
       pos += 512 + blocks * 512
   ```

   Find which pad's `[data_start, data_start+27)` range overlaps the
   diff bytes. That's the modified pad.

6. **Decode the changed bytes**

   Subtract the pad's `data_start` from each diff position to get the
   field offset within the 27-byte record. Cross-reference against the
   field you changed.

   Example: diff at byte 33282 (1-indexed), pad C-07 starts at 33281
   (0-indexed). 33282 − 1 − 33281 = 0... wait:
   - cmp 1-idx → 0-idx: subtract 1 → 33281
   - field offset: 33281 − 33280 = **1**
   - byte +1 changed from `0x00` to `0x64` (= 100)
   - You set the slot to 100. **Verified: slot is at offset +1 (u8).**

## Worked example: the slot + length + BPM trio

Procedure: empty device → take backup → drag a single sample into Sample
Tool → assign that sample to one pad → take another backup → diff.

Result (six bytes change in the affected pad's 27-byte record):

| Offset | Before | After | Decoded |
|---|---|---|---|
| +1 | 0x00 | 0x64 | slot 100 (u8) |
| +9 | 0x00 | 0x84 | length byte 1 |
| +10 | 0x00 | 0x01 | length byte 2 |
| +12 | 0x00 | 0x48 | BPM float byte 0 |
| +13 | 0x00 | 0xd2 | BPM float byte 1 |
| +14 | 0xf0 | 0x9f | BPM float byte 2 (overwrites default 120.0 high byte) |

Bytes +8..+11 as `<I` LE = 99,328 — matches the WAV's frame count.
Bytes +12..+15 as `<f` LE = 79.91 — Sample Tool's auto-computed BPM
(3 beats / 2.252 sec). Three fields decoded from one diff.

## Tips

- Start with a known-good baseline (empty device, single sample, known
  pad). Less noise = cleaner diff.
- `cmp -l` shows bytes in **octal**. Convert with `printf "0x%x\n" 0$octal`.
- If diff bytes spread across multiple pad records, you changed more
  than one thing — re-do step 2 more carefully.
- Some Sample Tool changes (e.g., trim) write to multiple records that
  reference each other — those need a more careful diff than this
  procedure covers.

## Open offsets to attack with this method

From PROTOCOL.md §7, these positions remain inferred or unknown:

- **Byte +2** — likely midiChannel (phones24); change MIDI channel on
  a pad and diff
- **Bytes +3..+5** — likely trimLeft u24 LE (phones24); set
  `sample.start` to a known non-zero value and diff
- **Bytes +6..+7** — unknown
- **Byte +17 (pitch i8)** — change pitch on a pad
- **Byte +18 (pan i8)** — change pan
- **Byte +19 (attack u8)** — change attack
- **Byte +22 (chokeGroup u8)** — assign pad to choke group
- **Bytes +25..+26** — unknown; try changing every field that hasn't
  been mapped yet to find these

PRs welcome.
