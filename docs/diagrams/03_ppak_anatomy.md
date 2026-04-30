# The .ppak archive format

![Anatomy of a .ppak archive](03_ppak_anatomy.svg)

A `.ppak` is a plain ZIP archive with three entries: `/projects/PXX.tar`,
one or more `/sounds/<slot> <name>.wav` files (44.1 kHz stereo, 16-bit —
Sample Tool transcodes to 46875 Hz mono on upload), and a 10-field
`/meta.json` describing the pak. The project TAR inside is sparse: it
contains a `pads/` tree with binary pad records for *only the assigned
pads* (factory-P06 emits zero pad files; demo P02/P03 only emit the
populated groups), a `patterns/` directory holding pattern files named
`{group}{NN}` (no slash between group letter and number — `patterns/a/01`
would be silently ignored), and a top-level `scenes` file (712 bytes:
7-byte header + 99 × 6-byte slots + 111-byte trailer). Pad records are
26 bytes each — factory native — see [PROTOCOL.md §7.0](../../PROTOCOL.md)
for the 27-byte erratum (Sample Tool roundtrip artifact, non-canonical).
ZIP entry mtimes reflect export time; TAR entry mtimes are all 0 (Unix
epoch) — Sample Tool emits it that way and the device relies on it.

The unmissable trap: **there is no `settings` file inside the project
TAR**, and adding one — even an empty one — is fatal. The device
imports the project, fails an internal check, and throws **ERR 82 /
ERROR CLOCK 43** (wedge-class). Once that fires, the file session is
wedged; recovery requires a factory-reset. See [PROTOCOL.md §8](../../PROTOCOL.md)
for the full failure mode and observed device state.

The safest generator strategy is still **patch-from-real**: take a
`.ppak` that Sample Tool actually emitted, swap the WAV payloads and
patch the pad records you need to change, and re-zip. This sidesteps
almost every format pitfall — you inherit the correct entry order, the
epoch mtimes inside the TAR, the directory mode bits, the pNN ordering,
and (critically) the absence of a `settings` entry. For users who
don't have a backup to start from, this repo now also ships a
from-spec writer (`build_ppak()` in `ep133/ppak/song_writer.py`) that
constructs a valid project TAR — pads, patterns, and scenes — without
a real .ppak as a template.

For the song-mode pipeline overview (how a song spec turns into the
patterns + scenes side-tables), see
[`04_song_pipeline.svg`](04_song_pipeline.svg).
