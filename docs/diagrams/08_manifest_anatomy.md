# The two manifest shapes

![manifest anatomy](08_manifest_anatomy.svg)

There are two manifests in this codebase and they look almost the same on first
glance, which is a problem worth a diagram. **Shape A — `BatchManifest`** —
describes a flat collection of samples and their per-pad routing hints; that's
the one `ppak-load-manifest` and `ppak-load-one` consume. **Shape B —
`arrangement.json` + `manifest.json`** — is a *pair* of files describing a song
timeline (locators, scenes, clip placements) plus the per-group session-track
stems they reference; `ppak-export-song` consumes both.

Field colors group fields by role: identity, timing, playback, routing. The
green arrow between the two song-mode files marks the `file_path` ↔ `file`
invariant that the resolver enforces. See [`MANIFEST.md`](../MANIFEST.md) for
the full schema reference and resolution rules.
