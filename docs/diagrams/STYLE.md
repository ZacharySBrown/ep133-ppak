# Diagram style guide — ep133-ppak

A consistent visual language across the illustrated docs. Loosely inspired
by Jay Alammar's *Illustrated Transformer*, with subtle nods to TE's user
guide aesthetic (lowercase, geometric, room to breathe) but a different
palette so we're not impersonating their materials.

## Palette

| Token | Hex | Use |
|---|---|---|
| `bg` | `#fafaf7` | Page background |
| `ink` | `#1a1a1a` | Primary text, strokes |
| `muted` | `#666666` | Secondary text, light annotations |
| `line` | `#e8e8e3` | Grid lines, soft dividers |
| `panel` | `#f0efe9` | Soft block fills (different from bg) |
| `accent` | `#3a85ff` | Highlights, arrows, emphasis |
| `accent-soft` | `#dbeaff` | Accent block fills |
| `warn` | `#e85d3a` | "Don't do this" / footgun callouts (used sparingly) |
| `warn-soft` | `#ffe5dc` | Warn block fills |
| `ok` | `#3aaa6e` | "Verified" / "this works" annotations |
| `ok-soft` | `#dcf2e4` | Ok block fills |

## Type

- **All lowercase** for headings, body, labels. Numbers stay numerals.
- **Sans (labels, body)**: `Inter, system-ui, -apple-system, sans-serif`
- **Mono (code, byte values, SysEx)**: `"IBM Plex Mono", "JetBrains Mono", Menlo, Consolas, monospace`
- **Hand-feel callouts** (sparingly): `"Caveat", "Kalam", cursive` — for arrows-with-words like "←—— here's the gotcha"

Type sizes (in SVG units, viewBox-relative; scale via CSS):

- Hero / page title: 28px, 600 weight
- Section header: 18px, 600
- Body / description: 14px, 400
- Code / byte values: 13px, mono
- Caption / annotation: 12px, 400, often italic
- Hand callouts: 14px, cursive

## Geometry

- **Grid**: 8px base unit. Major spacing on multiples of 16 or 24.
- **Corner radius**: 6px on small chips, 12px on pad-shaped or panel boxes
  (echoes EP-133's pad shape without being on the nose).
- **Stroke width**: 1.5px default, 2px for emphasis, 1px for grid lines.
- **Arrows**: slight curve (Bezier with ~10% offset) preferred to stiff
  straight lines. Arrowhead: 8px filled triangle.
- **Whitespace**: be generous. Diagrams have margins of 24-40px on the
  outside edge of the viewBox.

## Composition rules

- Lead with the visual; text is annotation.
- Color-code by **field group** or **functional zone**, not by aesthetic
  whim. Reuse colors with consistent meaning across diagrams.
- Each diagram has a small title (lowercase) and one-line caption. Don't
  bury the lede.
- Footgun callouts use `warn` color and minimal text; verified facts use
  `ok` color when worth flagging.

## SVG conventions

- `viewBox="0 0 W H"` with sane W/H for the diagram type:
  - Wide overview: 1200 × 600
  - Square-ish layout: 800 × 800
  - Narrow strip: 1200 × 300
  - Comic strip: 1200 × 320 per panel
- No `width`/`height` attributes — let the embedding context size it.
- Embed fonts via `font-family` attribute, no @import.
- Reusable elements as `<defs>` + `<use>`.
- All text uses `text-rendering="optimizeLegibility"`.
- Arrowhead defs: `<marker>` element pattern, reused.

## Nods to TE (sparingly)

- Lowercase, sans-serif labels.
- 4×3 pad grid motif used as a visual anchor in 2-3 diagrams.
- A tiny 🥊 (boxing-glove emoji = K.O. II) in the lower-right of the
  hero diagram only.
- Numbers in monospace where they're "device values" (slot numbers,
  byte offsets, BPMs); proportional sans elsewhere.

## Don't

- TE's signature orange (`#ff6532`-ish) — we're complementing, not
  imitating.
- All-caps anywhere.
- Drop shadows, gradients (except very subtle ones if absolutely needed),
  3D effects.
- "Cute" emoji or stickers beyond the 🥊 nod.
- Arrows that cross other arrows or labels.
