# Acknowledgments

This project would not exist without the EP-133 reverse-engineering
work done by the community. The protocol foundations, the SysEx layer,
the broad shape of the `.ppak` format — all of those came from earlier
community efforts that this project builds on, not from scratch work
here.

This file documents who contributed what, and which project to consult
for which part of the stack today. If you're publishing related work,
please credit the upstream chain — many of these efforts predate this
one by 6-12 months.

---

## phones24

**Repository:** [github.com/phones24](https://github.com/phones24)

The foundational `.ppak` archive parser. phones24's RE bootstrapped most
of what's in this document — the broad protocol shape, the file-format
layout, and the fields we observe in the binary pad record were all
established by their work first.

**Foundational contributions:**
- Teenage Engineering manufacturer ID and identity bytes
- Top-level command structure
- 7-bit packing algorithm
- `.ppak` ZIP layout (`/projects/PXX.tar`, `/sounds/`, `/meta.json`)
- Initial pad-record byte interpretation (the field names and ordering)
- `/sounds/` filesystem at fileId 1000
- Project numbering scheme
- The shape of the binary pad record (~27 bytes, ordered fields)

**Where this project diverges:** when we ran the diff-based verification
(see PROTOCOL.md §7.3) against two real Sample Tool backups, the
pad-record byte offsets came out a few bytes different from phones24's
table — slot at byte +1 vs. bytes 0-1, length at bytes 8-11 vs.
overlapping 7-9, and so on. We've documented this with verification
status field-by-field in PROTOCOL.md §7. **Open question for the
community**: more diff captures across more devices and firmware
versions would help reconcile. Either project's parser can be patched
once there's a clear consensus on which interpretation matches
across devices.

**Use phones24's work for:** the canonical `.ppak` shape and the
read-side parsing path. The archive parser is the reason this project
was tractable at all.

---

## ep133-krate (icherniukh)

**Repository:** [github.com/icherniukh/ep133-krate](https://github.com/icherniukh/ep133-krate)

Extensive live-SysEx capture-based protocol RE plus a polished
sample-manager TUI. krate's work on the live wire protocol is notably
deeper than this project's — they have a large captured-traffic archive,
a published confidence matrix per operation, and a number of findings
that complement what's here cleanly.

**Contributions specifically called out:**
- Live capture-based RE of the official Sample Tool's SysEx traffic
- Confirmed FILE LIST + node METADATA GET as the active API path in
  recent firmware
- `{"sym": <slot>}` for pad-to-slot binding
- `{"active": <node>}` for UI/tree navigation (this project doesn't
  cover this layer at all)
- Project switch via cmd `0x7C` with `{"active": <N*1000>}`
- GET_META (`0x75`) unreliability in OS 2.0+ (ghost-data caveat)
- BE16 confirmation across 430+ entries (slots 1–972) — much larger
  evidence base than this project has for that finding
- Pad-node formula and pad row inversion
- WAV upload `smpl` chunk + `LIST/INFO/TNGE` JSON metadata chunk
  requirements
- Download protocol (cmd `0x7D`)
- Confidence-rating model (SOLID / SPECULATION / BLIND-GUESS / UNKNOWN)
  per operation — a nice rigor convention worth borrowing

**Complementary surface area:** krate covers live SysEx + UI navigation
deeply; this project covers the on-disk `.ppak` archive layer and the
binary pad record inside the project TAR — which krate explicitly
notes as out of their scope. The two repos line up well as
complements; cross-reading both gets you the fullest picture.

**One note worth offering back:** byte 6 of the SysEx frame appears to
be a request flag + req-id-hi rather than a per-command session ID.
The bit layout is `BIT_IS_REQUEST=0x40`, `BIT_REQUEST_ID_AVAILABLE=0x20`,
low 5 bits = req-id-hi; this reconciles krate's own observation that
"the cmd byte accepts any value." The actual command lives at byte 8
(`0x01` GREET, `0x05` FILE). PROTOCOL.md §1 has the framing detail; if
useful, happy to file a PR.

**Use krate's work for:** anything live-SysEx, anything pattern/sequencer
related, anything to do with the official tool's network of node IDs
and `active` navigation, and as a reference TUI workflow for sample
management.

---

## garrettjwilke / ep_133_sysex_thingy

**Repository:** [github.com/garrettjwilke/ep_133_sysex_thingy](https://github.com/garrettjwilke/ep_133_sysex_thingy)

Pre-firmware-2.0 SysEx examples — one of the earliest community SysEx
references for this device. Cited by ep133-krate; this project did not
consume from it directly, but it's part of the chain that made the rest
of this work possible.

---

## benjaminr / mcp-koii

**Repository:** [github.com/benjaminr/mcp-koii](https://github.com/benjaminr/mcp-koii)

MIDI control interface for the EP-133, with sound-to-pad mapping
research. Cited by ep133-krate as a useful reference for pad-group
mapping. This project did not consume from it directly.

---

## abrilstudios / rcy

Reference implementation for the FW 2.0.5 upload protocol (Dec 2025).
Cited by ep133-krate as a key reference for the upload sequence on
recent firmware. This project did not consume from it directly.

---

## Teenage Engineering

**[teenage.engineering](https://teenage.engineering)**

For shipping a device whose USB-MIDI implementation is consistent enough
to reverse-engineer without too many corner cases, and whose Sample Tool
web app loads its protocol code in a form motivated humans can inspect.
Also for making something genuinely enjoyable to write software for.

The Teenage Engineering name and trademarks are the property of Teenage
Engineering AB; this project is unaffiliated.

---

## What this project adds to the stack

Briefly, where this repo's emphasis lies:

1. **`.ppak` archive writer** — patch-from-real-backup strategy that
   produces Sample-Tool-compatible archives reliably. Build-from-scratch
   hits silent rejections; the patch-from-real path works on the first
   try.
2. **Diff-method verification** of pad-record byte offsets. Reproducible
   procedure (two backups + `cmp -l`) that anyone with the device can
   apply to confirm or extend the byte layout. See
   `docs/verifying-byte-offsets.md`.
3. **Sample Tool emit conventions** — `meta.json` schema, ZIP entry
   timestamp requirements, the absent-on-purpose `settings` file in the
   project TAR (adding one triggers ERROR CLOCK 43, recoverable only
   via SHIFT+ERASE flash format).
4. **Time-stretch math** for `time.mode=bpm` — `playback_speed =
   project_bpm / sound.bpm` — and the practical implication that each
   loop's `sound.bpm` should be set to its true recorded tempo for
   clean bar inference.
5. **The TAR `pNN` vs. SysEx `pad_num` numbering trap** documented as a
   single page with a translation table, so anyone working across both
   layers can avoid landing assignments on the wrong physical pad.

If you build on these, please link back. If you find errors, please
open an issue or PR — the more eyes on the byte-level details, the
faster the community as a whole gets to a stable, agreed-upon spec.
