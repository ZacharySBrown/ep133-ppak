# Acknowledgments

This project would not exist without the EP-133 reverse-engineering work
done by the community over the last two years. The findings in this
repository are corrections, supplements, and clean-room re-implementations
on top of foundations laid elsewhere. This file documents who contributed
what, and where each upstream effort still leads on a particular topic.

If you're publishing related work, please credit the upstream chain
appropriately — many of these efforts predate this one by 6-12 months.

---

## phones24

**Repository:** [github.com/phones24](https://github.com/phones24)

**What they figured out first:**
- The Teenage Engineering manufacturer ID and identity bytes
- Top-level command structure
- 7-bit packing algorithm
- The `.ppak` ZIP layout (existence of `/projects/PXX.tar`, `/sounds/`, `/meta.json`)
- Initial pad-record byte interpretation (with offsets that we later corrected)
- `/sounds/` filesystem at fileId 1000
- Project numbering scheme
- The shape of the binary pad record (~27 bytes, ordered fields)

**What we corrected here:**
- Pad-record byte offsets were shifted by 1-2 bytes vs. the actual format
  (verified by diffing two real Sample Tool backups, see PROTOCOL.md §7
  and `docs/verifying-byte-offsets.md`)

**Where to use phones24's work:** their archive parser remains the
canonical reference for the broad `.ppak` shape; only the per-field
offset table needs the correction. Read-side projects should consider
both.

---

## ep133-krate (icherniukh)

**Repository:** [github.com/icherniukh/ep133-krate](https://github.com/icherniukh/ep133-krate)

**What they figured out / nailed down:**
- Live capture-based RE of the official Sample Tool's SysEx traffic
- Confirmed FILE LIST + node METADATA GET as the active API path
- `{"sym": <slot>}` for pad-to-slot binding
- `{"active": <node>}` for UI/tree navigation
- Project switch via cmd `0x7C` with `{"active": <N*1000>}`
- GET_META (`0x75`) is unreliable in OS 2.0+ (returns ghost data)
- BE16 confirmation across 430+ entries (slots 1-972)
- Pad-node formula `node = 2000 + (project * 1000) + 100 + group_offset + file_num`
- Pad row inversion (physical bottom-up, filesystem top-down)
- WAV upload requires `smpl` chunk + `LIST/INFO/TNGE` JSON metadata chunk
- Download protocol (cmd `0x7D`)
- Confidence-rating model for protocol findings (SOLID / SPECULATION /
  BLIND-GUESS / UNKNOWN)

**What we cover here that they explicitly punt on:**
- The 27-byte binary pad record inside the project TAR (their work
  operates on live JSON metadata; ours operates on TAR bytes)
- The full `.ppak` archive format with verified `meta.json` schema,
  ZIP entry conventions, and the absent-on-purpose `settings` file
- WAV format requirements specifically for `.ppak` import (44.1 kHz
  stereo) vs. SysEx upload (46875 Hz mono after transcode)
- Time-stretch math semantics for `time.mode=bpm`
- The two pad-numbering conventions trap (TAR `pNN` vs. SysEx `pad_num`)

**One thing we'd offer back:** byte 6 of the SysEx frame is a request
flag + req-id-hi (`BIT_IS_REQUEST=0x40`, `BIT_REQUEST_ID_AVAILABLE=0x20`,
low 5 bits = req-id-hi), not a per-command "session ID." Their `0x61
INIT, 0x77 INFO, 0x7C PROJECT, 0x7D DOWNLOAD, 0x7E UPLOAD` table
represents different request IDs with the request flag set, not
different commands. The actual command lives at byte 8 (`0x01` GREET,
`0x05` FILE). This reconciles their own observation that "the cmd byte
accepts any value" — see PROTOCOL.md §1.

---

## garrettjwilke / ep_133_sysex_thingy

**Repository:** [github.com/garrettjwilke/ep_133_sysex_thingy](https://github.com/garrettjwilke/ep_133_sysex_thingy)

Pre-firmware-2.0 SysEx examples. Cited by ep133-krate as one of the
earliest community SysEx references. We did not consume from this repo
directly, but it's part of the lineage.

---

## benjaminr / mcp-koii

**Repository:** [github.com/benjaminr/mcp-koii](https://github.com/benjaminr/mcp-koii)

MIDI control interface for the EP-133. Contains sound-to-pad mapping
research. Cited by ep133-krate as a useful reference for pad-group
mapping. We did not consume from this repo directly.

---

## abrilstudios / rcy

Reference implementation for FW 2.0.5 upload protocol (Dec 2025). Cited
by ep133-krate as a key reference. We did not consume from this repo
directly.

---

## Teenage Engineering

**[teenage.engineering](https://teenage.engineering)**

For shipping a device whose USB-MIDI implementation is consistent enough
to reverse-engineer without too many corner cases, and whose Sample Tool
web app loads its protocol code in a form that motivated humans can
inspect. Also for the device itself, which is genuinely fun to write
software for.

The Teenage Engineering name and trademarks are the property of Teenage
Engineering AB; this project is unaffiliated.

---

## Notes on this project's contributions

Where this project pushes specifically:

1. **Diff-method verification** of pad-record byte offsets via two
   Sample Tool backups (before/after a single UI change). Reproducible;
   anyone with the device can confirm or extend. See
   `docs/verifying-byte-offsets.md`.
2. **Patch-from-real `.ppak` writer** — generating from scratch hits
   silent rejections in Sample Tool's parser; cloning a real backup and
   modifying only the necessary bytes works on the first try.
3. **Corrected pad-record offsets** — slot at byte +1 (u8), length at
   +8..11 (u32 LE), BPM float32 at +12..15 (not BPM/2 as previously
   hypothesized), default values at +16/20/21/23/24 verified from real
   Sample Tool blank-pad output.
4. **Time-stretch math** for `time.mode=bpm` — speed = project_bpm /
   sound.bpm — and the practical implication that each loop's
   `sound.bpm` should match its true recorded tempo for clean bar
   inference.
5. **Sample Tool emit conventions** — `meta.json` schema, ZIP entry
   timestamp requirements, the absent-on-purpose `settings` file in
   the project TAR (adding one triggers ERROR CLOCK 43, recoverable
   only via SHIFT+ERASE flash format).

If you build on these, please link back. If you find errors, PRs welcome.
