# Changelog

## v0.2.0 — Song-mode export (pending device validation of port)

> **Note:** the song-mode surface in this release is a port from
> StemForge's `feat/ep133-song-export` branch (merged 2026-04-28),
> where the same code is hardware-validated against a real EP-133.
> The **port itself** has not yet been validated end-to-end on
> hardware from this repo's package — see "Pending validation" below.

### Added

- **Song-mode `.ppak` export.** New `ep133.song.*` subpackage with byte
  builders for patterns/scenes/pads/settings (`format.py`), arrangement
  → snapshot resolution (`resolver.py`), snapshot → spec synthesis
  (`synthesizer.py`), and EP-133-native WAV conversion (`wav.py`). See
  [docs/PORT_PLAN.md](docs/PORT_PLAN.md) for architectural notes.
- **Build-from-spec writer** at `ep133.ppak.song_writer.build_ppak()` —
  takes a `PpakSpec` and a reference template, authors fresh patterns /
  scenes / sounds, preserves `meta.json` + per-pad templates from the
  reference. Sits alongside the existing patch-from-base writer.
- **`build_synthetic_template_ppak()`** for users who don't yet have a
  device capture — produces a minimal device-default template.
- **`ppak-export-song` CLI** — `arrangement.json + manifest.json → .ppak`.
- **`tools/ep133_capture_reference.py`** — pull a project TAR off live
  hardware over USB-MIDI, validate, wrap as `.ppak` for use as a
  reference template.

### Changed (breaking)

- **Pad records are 26 bytes, not 27** — factory native, verified
  against `factory_default.pak`. Sample Tool's 27-byte form is
  non-canonical (corrupts during scene-switch iteration → `ERR PATTERN
  189`). Affects:
  - `ep133.pad_record.PAD_RECORD_SIZE` and decoder constants
  - `ep133.ppak.writer.PAD_RECORD_SIZE` and `DEFAULT_BLANK_PAD`
  - `ep133.ppak.song_writer` truncates 27-byte records to 26 on read
  
  See [PROTOCOL.md §7.0 erratum](PROTOCOL.md) for the full explanation.

### Tests

195 new tests across 7 new test files (`test_song_format.py`,
`test_song_resolver.py`, `test_song_synthesizer.py`, `test_wav_format.py`,
`test_song_writer.py`, `test_song_integration.py`,
`test_capture_reference.py`) plus round-trip additions to
`test_pad_record.py`.

Suite total: **319 passed, 2 xfailed**. The 2 xfails document a real RE
conflict between the existing `decode_bpm` (`*2` rule, one data point)
and the new `build_pad` (raw float32, hardware-validated by StemForge).
Resolving that needs more device captures.

### Pending validation

- [ ] Manual device load of a `ppak-export-song` output (full
      arrangement → `.ppak` → device → playback). The same code
      already plays correctly when invoked through StemForge; this
      tracks "does the *port* preserve byte-equivalence end-to-end."
- [ ] `factory_default.pak` golden round-trip — fixture is gated as
      optional (26MB file, not committed); tests skip cleanly when
      absent. Drop it at `tests/fixtures/captures/factory_default.pak`
      to enable.
- [ ] Resolution of the `decode_bpm` / `build_pad` xfail conflict.

## v0.1.0 — Initial release

- SysEx live-protocol coverage (sample upload, pad assignment, BPM matrix)
- Patch-from-base `.ppak` writer
- 4 CLI tools: `ppak-writer`, `ppak-load-manifest`, `ppak-bpm-matrix`,
  `ppak-load-one`
- Manifest schema (`SampleMeta`, `BatchManifest`)
