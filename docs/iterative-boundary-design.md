# Iterative Boundary Mapping — Design Sketch

Status: proposal, branch `iterative-boundary`. Subject to revision after first prototype.

## Problem

The existing extraction mapper assumes records are *structurally encapsulated*: each
record is a DOM subtree rooted at one element matched by a CSS selector
(`root_boundary`), and fields are descendants of that element. Collections work the
same way at a nested level via `iterator`.

This works for the overwhelming majority of pages, but it fails on layouts where
records are projected along an axis that has no DOM ancestor of its own. The
motivating case (`/Users/x/Desktop/example.html`, parsed against branch
`iterative-boundary`) is a FrontPage-era table layout:

- Each record-table is a 3×3 grid.
- Row 1: three `<td>`s, each containing one record's *title*.
- Row 2: three `<td>`s, each containing one record's *details* (`<blockquote>`).
- Row 3: three `<td>`s, each containing one record's *image(s)*.
- The "record" is column N, projected across all three rows. The cells composing a
  single record share no ancestor below `<table>`.

No selector, including `:nth-of-type(n)` or any combinator chain, can yield a
record root for this layout — CSS lacks a "zip these by index" operator. The
records are real, but they live on the column axis, not the containment axis.

## What we are NOT doing

- **No "transposed table" mode flag.** That bakes a binary axis ontology into the
  mapper. The next pathological site won't be column-vs-row; it will be
  whatever-vs-whatever.
- **No user-supplied scripts/lambdas.** The mapper stays declarative; no DSL.
- **No manual `count` integer** as an alternative to the count selector.
  Reasoning: if the record count cannot be expressed as a DOM selector, the
  field selectors that have to navigate the same DOM are unlikely to survive
  the page either. Forcing DOM-derived counts keeps the mapping honest.
- **No `record_axis: column|row|...` enum.** Pure index variables only — the
  engine has no opinion about what the index is "for."

## Design

Add one primitive: a **named index iterator** declared per boundary, bounded by
a count selector, used as a `{name}` substitution token inside that boundary's
field selectors.

### Mapping config additions

`BoundaryMapping` (and `DocumentExtractionConfig` for the root) gain an optional
`iterators` list:

```yaml
root_boundary: "table[border='7']"
iterators:
  - name: i
    count_selector: "tr:first-of-type > td"
fields:
  title:   "tr:nth-of-type(1) > td:nth-of-type({i}) span"
  details: "tr:nth-of-type(2) > td:nth-of-type({i}) blockquote"
  images:  "tr:nth-of-type(3) > td:nth-of-type({i}) img[src]"
```

Semantics:

1. The boundary matches as it does today (zero-or-more elements via `select`).
2. For each boundary match, evaluate `count_selector` *relative to that match*
   to obtain `N` (the number of matches it returns).
3. For each `i` in `1..N`, substitute `{i}` into every field selector in this
   boundary's scope, then run the existing per-field extraction with the
   resolved selectors.
4. The result: `N` records produced per boundary match, where the existing
   model would have produced one.

### Multiple iterators

`iterators` is a list, not a single object. Declaring two iterators yields
Cartesian iteration:

```yaml
iterators:
  - name: i
    count_selector: "tr:first-of-type > td"   # columns
  - name: j
    count_selector: "div.gallery img"          # images per cell
```

Produces `N × M` records, with both `{i}` and `{j}` substitutable in field
selectors. Order of declaration determines outer/inner loop order; outer is
first.

### Iterators on nested boundaries

The same primitive applies at any boundary depth, not just the root.
Recursion is free: a record's collection sub-boundary can declare its own
iterator, and so on.

### Sparse records — anchor field

When the substituted field selectors return nothing for a given iteration,
that iteration may represent a real-but-empty record (rare) or a non-existent
record (common — see the second record-table in `example.html`, which has
columns 2 and 3 as `&nbsp;` placeholders).

Resolution: each iterator may declare an **anchor field** (defaults to the
first declared field). If every declared anchor's substituted selector
returns nothing for iteration `i`, the entire iteration is skipped — no
record is emitted.

`anchor` accepts either a single field path or a list:

```yaml
iterators:
  - name: i
    count_selector: "tr:first-of-type > td"
    anchor: title                # single anchor (sugar for [title])

  - name: j
    count_selector: "tr:first-of-type > td"
    anchor: [title, details]     # multi-anchor: skip iff BOTH are empty
```

The multi-anchor rule is OR-of-presence: keep the record if *any* listed
anchor has content; skip only when *all* are empty. The aggressive variant
("skip if any anchor is empty") is intentionally not supported — it's too
brittle for the partial-data records this rule is meant to admit.

### Iterator names — closed set

Iterator names are locked to a fixed set: **`i`, `j`, `k`**. This is enough
for any reasonable case (single iteration: `i`; Cartesian: `i`+`j`; rare 3D
nesting: `i`+`j`+`k`). Anything beyond three nested iterators is a code
smell and should prompt a redesign of the boundary structure rather than a
fourth name.

The closed set has two practical benefits:

- The Content Mapper UI presents iterator names as a dropdown, eliminating
  typos and making selector templates self-documenting.
- Engine substitution doesn't need to scan templates for arbitrary names —
  it knows what to look for.

Names are scoped per boundary; nested boundaries may reuse `i` (it shadows
the outer scope's `i`). Preview-time validation surfaces shadows so users
notice unintentional collisions.

### `:scope` and leading-combinator support

Soup Sieve scopes `el.select(...)` to descendants of `el` implicitly. The
`:scope` pseudo-class makes that anchoring explicit and unlocks leading
combinators.

| Form | Meaning |
|---|---|
| `"tr:first-of-type > td"` (implicit) | `<td>` direct child of any `<tr>` that is first-of-type in its own parent, anywhere under the boundary |
| `":scope > tr:first-of-type > td"` | `<td>` direct child of `<tr>` direct child of the boundary itself |
| `":scope tr:first-of-type > td"` | functionally identical to the implicit form |
| `"> tr"` | direct children of boundary (auto-normalized — see below) |

**Auto-injection of `:scope`.** Top-level combinator-leading selectors
(`>`, `+`, `~` as the first non-whitespace character) are rejected by Soup
Sieve. The engine adds a one-line normalization at every `select` /
`select_one` call site: if the selector starts with one of those
combinators, prepend `:scope ` before passing through. This applies
uniformly to boundary selectors, count selectors, and field selectors so
users don't have to remember which contexts allow it.

Effect: previously-invalid selectors like `"> tr"` now work as written and
mean what users intuitively expect. No existing selector is affected — by
definition, no current selector could start with a combinator and parse,
so this is purely additive.

**Picker emits the explicit form.** When the user builds a selector using
the visual picker in the Content Mapper, child-relative paths come out as
`":scope > foo"`, not `"> foo"`. Hand-typed inputs still accept both;
the picker just defaults to the unambiguous form.

**Practical caveat about `<tbody>`.** BeautifulSoup injects implicit
`<tbody>` into `<table>` parses. So `":scope > tr:first-of-type > td"`
against a `<table>` boundary returns nothing — the `<tr>` is a grandchild.
Use the implicit form (`"tr:first-of-type > td"`) or write the full path
through tbody. The live count badge surfaces these mismatches empirically.

### Live count badge (UI)

When the user enters a `count_selector` in the Content Mapper, the UI shows
"matches N elements on sample page" inline next to the input. Same pattern
the existing boundary-selector input uses today — reuse the validation
endpoint and badge component.

### Validation

All checks run at preview time against a sample page. None are static-only.

1. **Iterator must be referenced.** At least one field selector in the
   iterator's boundary scope must contain `{<name>}`. Otherwise the iterator
   is dead code and every iteration produces an identical record.
2. **Count selector must match.** When evaluated against the boundary on the
   sample page, `count_selector` must return ≥ 1 element. Catches typos and
   dead selectors before extraction time.
3. **Substituted field selectors must parse.** After `{i}` substitution, each
   field selector must be valid CSS (verified by Soup Sieve's parser). Catches
   syntax errors that only manifest after substitution.
4. **Anchor field must exist** in the iterator's boundary scope, if declared.

Notably *not* validated:

- "Field selectors are a subset of the count selector." This was considered and
  rejected — CSS-selector subset is not statically decidable, and the strict
  reading would forbid the legitimate case where field selectors target rows
  the count selector never visits (as in the column-projection example).

## Scope of code changes

- **`services/shared/models.py`** — extend `BoundaryMapping` (or a parallel
  `IterativeBoundary`) to carry the `iterators` list. Each iterator: `name`
  (Literal["i", "j", "k"]), `count_selector` (str), optional `anchor`
  (str or list[str]).
- **`services/extraction-service/app/extractor/engine.py`** — extend
  `_extract_record` and `_extract_collection` to detect the `iterators`
  field, evaluate count, loop indices, substitute templates into field
  selectors before passing through to leaf extraction. Substitution is a
  string `.format(**indices)` over selector strings; field paths and other
  non-selector strings are unaffected. Also add a `_normalize_selector(s)`
  helper used at every `select`/`select_one` call site that prepends
  `:scope ` when `s` starts with a top-level combinator (`>`, `+`, `~`).
- **`services/shared/pipeline_stages.py`** — `mapper.complete` rule may need
  to require at least one iterator-validated mapping when iterators are in
  use. (Decide during prototype.)
- **`services/ui/src/...` (Content Mapper)** — UI affordance to declare
  iterators per boundary. Picker that helps users build a count selector
  against a sample page (live count badge: "this matches 3 elements").
- **Tests:**
  - Python unit: substitution, count discovery, single-anchor skip,
    multi-anchor OR-of-presence skip, multi-iterator Cartesian,
    nested-boundary iterators, name-shadowing in nested scope,
    leading-combinator normalization (`> tr` → `:scope > tr`),
    merge_by collapsing two iterations on one page,
    cross-page merge_by of iterator-produced records.
  - End-to-end against `example.html`: produce 3 + 1 records (4 total — the
    second table has only column 1 populated, columns 2 & 3 skipped via
    anchor).
  - Pipeline-stages fixture additions for `mapper.complete` interaction.

## Backwards compatibility

Existing mappings have no `iterators` field. The engine's behavior in that
case is unchanged. No migration needed; no flag day. New jobs opt in
per-boundary by declaring an iterator.

## Illustrative end-to-end against `example.html`

```yaml
extraction_config:
  mode: document
  document:
    root_boundary: "table[border='7']"
    iterators:
      - name: i
        count_selector: "tr:first-of-type > td"
        anchor: title
    field_mappings:
      - field_path: title
        selector:   "tr:nth-of-type(1) > td:nth-of-type({i}) span"
      - field_path: details
        selector:   "tr:nth-of-type(2) > td:nth-of-type({i}) blockquote"
      - field_path: images
        selector:   "tr:nth-of-type(3) > td:nth-of-type({i}) img[src]"
        attribute:  src
        # is_array: true (per schema)
      - field_path: performer
        url_regex:  "member_videos_(\\w+)\\.htm"  # page-scoped, no {i}
```

Expected output: 4 records total — 3 from the first record-table, 1 from the
second (columns 2 and 3 of the second table skipped because their `title` cell
is an `&nbsp;` placeholder).

## `merge_by` interaction

`merge_by` is the existing post-extraction collapse-by-key feature in
`services/extraction-service/app/extractor/engine.py:107` (`_maybe_merge`).
After all records are extracted across all pages, records sharing the same
value at the configured `merge_by` field path are collapsed into one
(first-non-null per field across the group).

Iterator-produced records flow through this phase unchanged — the merge
runs on the final record list and doesn't care whether records came from
the structural path or an iterated boundary. This makes a useful combined
case work for free:

- A site has a **non-encapsulated layout** (column-projected records, etc.)
  AND **scatters per-entity data across multiple pages** (e.g., a video's
  title appears on the index page; its full description and credits appear
  on a per-video detail page; its image gallery appears on a third).
- Per-page extraction uses iterators where the layout demands it.
- `merge_by: "slug"` (typically derived via `url_regex` from the page path)
  collapses all records sharing the same slug, taking first-non-null per
  field across the group.

The result is one merged record per logical entity, with fields populated
from whichever page surfaced them — even though no page ever contained the
entity in a single encapsulated form.

Edge case: two iterations on the same page can produce records sharing a
merge key (if `{i}` doesn't vary the merge-key field). Behavior is identical
to two records from different pages sharing a key — they collapse.

Test fixtures must exercise:

1. Two iterations on one page sharing a merge key.
2. Cross-page merge of iterator-produced records (the combined case above).

## Open questions

None blocking. Decisions captured above:

- Iterator names: locked to `i`, `j`, `k`.
- `:scope`: allowed transparently in count selectors.
- Live count badge: confirmed UI requirement.
- Multi-anchor: supported as `string | string[]`, OR-of-presence.
- `merge_by`: no design change, test coverage required.

## Next step

Prototype on the `iterative-boundary` branch. Validate against `example.html`.
Extend tests. Fold into CLAUDE.md only after the design survives a second
hostile site.
