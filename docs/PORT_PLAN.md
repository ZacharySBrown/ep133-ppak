# Song-Mode Port Plan: StemForge → ep133-ppak

**Status:** draft, 2026-04-29
**Direction:** port StemForge's song-mode export surface (the new code that landed on `feat/ep133-song-export`, merged as PR #34) down to ep133-ppak so external consumers — including StemForge itself, eventually — depend on the library instead of vendoring it.

---

## 1. Why this port exists

ep133-ppak today is a **sample-mode** library: upload a WAV, assign it to a pad, set BPM. It cannot produce a multi-pattern, multi-scene EP-133 project.

StemForge's `feat/ep133-song-export` branch added the missing layer: a complete writer for **song mode**, with patterns, scenes, song-position playback lists, settings, and arrangement-driven pipeline. That code lives in `stemforge/exporters/ep133/` and was developed against a real device with byte-level captures — it works on hardware as of 2026-04-28.

The plan is to lift everything that isn't StemForge-specific into ep133-ppak, then publish v0.2.0 with full song-mode support. StemForge can later swap its vendored copy for a `pip install ep133-ppak` dependency.

## 2. Scope

### In scope (port to ep133-ppak)

| Module | StemForge LOC | What it does |
|---|---|---|
| `song_format.py` | 486 | byte builders for `patterns/{g}{NN}`, `scenes`, `pads/{g}/p{NN}`, `settings` files |
| `song_synthesizer.py` | 476 | `Snapshot` list → `PpakSpec` (dedupes patterns, allocates empty markers, derives scene lengths from locator gaps, fans out short slices into multi-event patterns) |
| `song_resolver.py` | 176 | arrangement-shaped JSON (locators + tracks A/B/C/D) → per-locator `Snapshot` |
| `wav_format.py` | 192 | `convert_wav_to_ep133()` — mono 16-bit 46875 Hz + `smpl` + `LIST/INFO/TNGE` JSON metadata + slicing |
| New `ppak/song_writer.py` | ~330 | spec-driven `.ppak` builder (current ep133-ppak writer is a base-patcher, not a from-scratch builder) |
| `tools/ep133_capture_reference.py` | ~250 | live-device project-TAR pull → wraps as `.ppak` for use as a reference template |
| New CLI: `ppak-export-song` | new | `arrangement.json` + `manifest.json` → `.ppak` |

### Out of scope

- **`stemforge/exporters/ep133/exporter.py`** (the session-mode entry point that StemForge's pipeline calls) — keeps the StemForge data-model coupling. Stays in StemForge.
- **`bundle_synthesizer.py`** — referenced by stale .pyc but no .py exists; was an earlier track that got superseded by song_synthesizer.
- **Legacy `ep133*.py` modules at `stemforge/exporters/`** (`ep133.py`, `ep133_v2.py`, `ep133_mapping.py`, `ep133_stem_export.py`, `ep133_upload.py`) — predate the package layout, presumed dead. Not porting.

## 3. Architectural decisions

These are the calls that need to be made up front because they're load-bearing on every test.

### 3.1 Pad record size: 26 bytes (breaking change for ep133-ppak)

**Today:**
- ep133-ppak's `ppak/writer.py` writes 27-byte pad records (`PAD_RECORD_SIZE = 27`).
- StemForge's `song_format.py` writes 26-byte pad records (`PAD_RECORD_SIZE = 26`).

**Verified facts (from StemForge memory + `factory_default.pak`):**
- Factory device backups (e.g. `factory_default.pak`) emit **26-byte** pad records.
- Sample Tool backups emit **27-byte** pad records (extra trailing 0x00).
- The device tolerates both on import but **the 27-byte form corrupts during scene-switch iteration** (stride bug → `ERR PATTERN 189`).

**Decision:** unify on 26 bytes. ep133-ppak's writer migrates from 27 → 26.

**Migration path:**
- New constant `PAD_RECORD_SIZE = 26` in `ep133/pad_record.py` (currently lives only in `ppak/writer.py`).
- The decoder side (`ep133.pad_record.decode_bpm`) is already length-agnostic — no change needed.
- `ppak/writer.py:DEFAULT_BLANK_PAD` updates to 26 bytes (drop the trailing 0x00).
- `find_pad_record_offsets` already handles arbitrary records by reading TAR header `size` — no change needed.
- `tests/test_pad_record.py` already uses 32-byte synthetic records to exercise `decode_bpm` (it ranges `+12..+15`); **does not break**.
- `PROTOCOL.md` §7 needs an erratum note: "factory native is 26B; Sample Tool emits 27B which is non-canonical".

### 3.2 Two writer styles, kept side-by-side

**Today, ep133-ppak has only:**
- `ep133.ppak.writer.build_from_base()` — patch-from-base. Read a real `.ppak`, modify pad bytes in place, repack.

**StemForge adds:**
- `ep133.ppak.song_writer.build_ppak()` (new module) — build-from-spec. Take a `PpakSpec`, author fresh patterns/scenes/sounds, only borrow `meta.json` + per-pad templates from a reference.
- `ep133.ppak.song_writer.build_synthetic_template_ppak()` — make a minimal device-default reference for tests + first-time users.

**Decision:** keep both. They serve different users — `build_from_base` is for "tweak my existing project's BPMs"; `build_ppak` is for "synthesize a song from external data."

### 3.3 BPM override encoding: keep both

ep133-ppak's `ppak/writer.py:encode_bpm_override` emits the device's bytes-13-15 form (`0x80 value precision`); StemForge's `song_format.py:build_pad` only emits float32 LE.

**Decision:** unified `build_pad` accepts `bpm_encoding="float32" | "override"` (default `"float32"` — that's what every captured factory pad uses). Override is for compatibility with on-device knobY-set BPMs.

### 3.4 Sample slot allocation: configurable, default 700

`song_synthesizer.global_sample_slot()` hard-codes a 700+ base (StemForge convention to avoid clobbering 1..699 user library).

**Decision:** make `slot_base` and `per_group_offset` arguments to `synthesize()`, defaulting to StemForge's 700/20 layout. Other consumers can pass `slot_base=1` to use bare slots.

### 3.5 Arrangement schema: not coupled to Ableton

`song_resolver.py` takes a JSON dict with `locators` + `tracks`. Nothing in the resolver knows about Ableton — it's a generic "arrangement" shape. We document the schema in `docs/ARRANGEMENT_SCHEMA.md` so other DAWs (FL, Bitwig, Reaper, generated arrangements) can produce the same shape.

### 3.6 Package layout

```
ep133/
├── pad_record.py       # decoder (existing) + new 26-byte builder ported from song_format
├── song/               # NEW subpackage
│   ├── __init__.py
│   ├── format.py       # byte builders (patterns, scenes, pads, settings)
│   ├── resolver.py     # arrangement → snapshots
│   ├── synthesizer.py  # snapshots → PpakSpec
│   └── wav.py          # convert_wav_to_ep133
└── ppak/
    ├── writer.py       # existing patch-from-base writer (migrates to 26-byte)
    └── song_writer.py  # NEW build-from-spec writer
tools/
└── export_song.py      # NEW CLI: ppak-export-song
```

This keeps the song-mode surface scoped under `ep133.song.*` so the simpler sample-mode use case stays uncluttered.

## 4. Test-first strategy (build red, then port green)

Every test in §5 lands **before** any `ep133/song/` source code is written. The structure:

| Layer | Test | Strategy |
|---|---|---|
| Unit | `test_song_format.py` | round-trip: write bytes with builder, parse with mini-parser mirroring phones24's `parsers.ts`, assert input == decoded |
| Unit | `test_song_resolver.py` | resolve from synthetic arrangement JSON; check overlap rules, time-ordering, error paths |
| Unit | `test_song_synthesizer.py` | snapshot lists → PpakSpec; assert pattern dedupe, empty-marker allocation, scene-length math |
| Unit | `test_wav_format.py` | feed synthetic PCM in/out of `convert_wav_to_ep133`; verify chunk order, frame count, JSON shape |
| Unit | `test_pad_record.py` (updated) | merge: keep override-encoding tests + add 26-byte builder tests + cross-check decoder reads writer output |
| Container | `test_song_writer.py` | feed PpakSpec, read back built bytes, parse ZIP/TAR, assert layout (leading slash, project tar path, pad files only for assigned pads, settings omitted) |
| Container | `test_capture_reference.py` | port from StemForge — exercises `build_meta`, `validate_project_tar`, `wrap_tar_as_ppak` against the leading-slash gotcha |
| End-to-end | `test_song_integration.py` | full pipeline arrangement → manifest → reference.ppak → bytes; parse with same routines the device uses; assert bar counts, scene counts, BPM, pad slots |
| Golden-byte | `test_song_writer.py::test_factory_pak_pad_records_round_trip` | parse `factory_default.pak`'s pad records, rebuild with `build_pad`, assert byte-identical (validates 26-byte format claim) |

### Test fixtures to copy from StemForge

| Source path | Destination | Purpose |
|---|---|---|
| `/tmp/sf-song-export/tests/ep133/fixtures/sample_arrangement.json` | `tests/fixtures/sample_arrangement.json` | input for resolver/synthesizer tests |
| `/tmp/sf-song-export/tests/ep133/fixtures/sample_manifest.json` | `tests/fixtures/sample_manifest.json` | session_tracks fixture |
| `/tmp/sf-song-export/docs/ep133-song-triage/factory_default.pak` | `tests/fixtures/captures/factory_default.pak` | golden byte source for 26-byte pad record claim |
| `/tmp/sf-song-export/docs/ep133-song-triage/reference_minimal.ppak` | `tests/fixtures/captures/reference_minimal.ppak` | minimal capture for integration tests |
| `/tmp/sf-song-export/docs/ep133-song-triage/smack_song.ppak` | `tests/fixtures/captures/smack_song.ppak` | full song-mode capture (multi-scene + song-positions) |

### Confidence levels we can hit without device access

- **High confidence (publishable):** all unit tests pass + container tests pass + `factory_default.pak` round-trip is byte-identical + integration test on `reference_minimal.ppak` round-trip. This is what publishing v0.2.0 requires.
- **Highest confidence (ship-with-receipts):** the user manually loads an output `.ppak` onto a device and verifies playback. This stays the user's call after CI is green.

## 5. Test inventory (write these first)

Order of writing matches dependency order — tests that fail-import on missing source modules come before tests that depend on them.

### 5.1 New tests to write

```
tests/
├── conftest.py                          # extend: add fixtures_dir(), captures_dir(), arrangement(), manifest()
├── fixtures/
│   ├── sample_arrangement.json          # COPY from stemforge
│   ├── sample_manifest.json             # COPY from stemforge
│   └── captures/
│       ├── factory_default.pak          # COPY
│       ├── reference_minimal.ppak       # COPY
│       └── smack_song.ppak              # COPY
├── test_song_format.py                  # PORT 26-byte builder unit tests (~40 tests)
├── test_song_resolver.py                # PORT resolver unit tests (~12 tests)
├── test_song_synthesizer.py             # PORT synthesizer unit tests (~30 tests)
├── test_wav_format.py                   # NEW (StemForge has none) — write fresh
├── test_song_writer.py                  # PORT + EXPAND ppak_writer tests (~25 tests)
├── test_song_integration.py             # PORT integration test (~10 tests)
└── test_capture_reference.py            # PORT capture-tool unit tests (~10 tests)
```

### 5.2 Existing tests to update

- `tests/test_pad_record.py` — keep all existing override-encoding tests, **add** new tests covering the 26-byte builder ported from StemForge. The decoder + builder must round-trip cleanly.

### 5.3 Existing tests untouched

- `test_packing.py`, `test_assign_pad.py`, `test_payloads.py`, `test_sample_params.py`, `test_manifest.py`, `test_load_from_manifest_placement.py` — sample-mode tests, unaffected by the port.

## 6. Source-port checklist (only after tests are red)

1. Create `ep133/song/` package skeleton (empty modules + `__init__.py`).
2. Port `song_format.py` → `ep133/song/format.py`. **Run unit tests; expect green.**
3. Port `song_resolver.py` → `ep133/song/resolver.py`. **Run unit tests; expect green.**
4. Port `song_synthesizer.py` → `ep133/song/synthesizer.py`. Make `slot_base` configurable. **Run unit tests; expect green.**
5. Port `wav_format.py` → `ep133/song/wav.py`. **Run unit tests; expect green.**
6. Update `ep133/pad_record.py` — add the 26-byte builder + `bpm_encoding` parameter. **Run pad_record tests; expect green.**
7. Migrate `ep133/ppak/writer.py` from 27 → 26 bytes. **Run ppak/writer tests; expect green.**
8. Port `ppak_writer.py` (build-from-spec) → `ep133/ppak/song_writer.py`. **Run container tests; expect green.**
9. Port `tools/ep133_capture_reference.py`. **Run capture-reference tests; expect green.**
10. Add `tools/export_song.py` CLI + `ppak-export-song` entry in `pyproject.toml`. **Run integration test; expect green.**
11. Update `PROTOCOL.md` §7 — pad record is 26 bytes, not 27.
12. Update `README.md` — document the song-mode flow.
13. Bump `pyproject.toml` version to `0.2.0`.
14. Run full test suite once more.

## 7. Definition of done (before publishing)

- [ ] All ported tests in §5 pass.
- [ ] `pytest tests/ -v` reports zero failures.
- [ ] `factory_default.pak` round-trip (parse → rebuild → diff) is byte-identical for at least one pad record.
- [ ] `reference_minimal.ppak` integration test passes.
- [ ] `PROTOCOL.md` updated with 26-byte pad-record erratum.
- [ ] User manually verifies a synthesized `.ppak` plays on hardware (one-time gate before pushing to PyPI).
- [ ] CHANGELOG entry written.
- [ ] Tagged `v0.2.0`.

## 8. Risks & open questions

- **Open:** does StemForge's `_event_positions_bars` musical-quantization belong in a generic library? It's opinionated (snaps to powers of 2). Argument for: it's the only thing that makes short-slice tiling sound musical. Argument against: it's a policy decision a library shouldn't impose. **Tentative answer:** keep it in `synthesizer.py` but expose `quantization="musical" | "exact"` so callers can opt out.
- **Open:** the 700+ slot-base default is a StemForge convention. Other ep133-ppak users may not want it. Configurable per §3.4 mitigates but the *default* is still opinionated. **Tentative answer:** default to `slot_base=1` in the library; let StemForge pass `slot_base=700` explicitly when it consumes ep133-ppak as a dep.
- **Risk:** the 27→26 byte migration may break downstream code that constructs `bytes(27)` arrays. ep133-ppak is at v0.1.0 with no public users, so a v0.2.0 breaking change is fine *now* — would be costly later.
- **Risk:** `audioop` is removed in Python 3.13. StemForge's `wav_format.py` already has a TODO for this. Address before tagging v0.2.0 (likely `scipy.signal.resample_poly` or `numpy` inline implementations).

---

*This plan is the contract. Tests get written against it. Source code follows.*
