# Session Resume — Iterative Boundary Design

Date of session: 2026-05-04
Branch: `iterative-boundary`
Design doc: `docs/iterative-boundary-design.md`

## What we were trying to solve

User encountered a site whose record layout violates the structural-encapsulation
premise of the existing extraction mapper. Sample HTML at
`/Users/x/Desktop/example.html` (FrontPage 6.0 era; circa 2002).

Layout, per record-table:

- 3×3 grid of `<td>`s in a `<table border="7">`.
- Row 1 (3 cells): titles.
- Row 2 (3 cells): details (`<blockquote>`).
- Row 3 (3 cells): image(s).
- Each "record" = column N projected across all three rows. The cells of one
  record share no ancestor below `<table>`.
- Multiple such record-tables appear per page.
- The second record-table on the sample page is sparse: columns 2 and 3 are
  `&nbsp;` placeholders.
- Performer name lives in a header table at the top of the page, not in any
  record-table. User noted they can pull it via URL regex (or page-scoped
  field).

## Why straight CSS does not work

CSS selectors (including `:nth-of-type`, `:nth-child`, attribute selectors,
combinators) are single-element selectors. They can navigate INTO a record but
cannot zip/group cells from disjoint parents into one record. CSS lacks a
"group-by-index" operator.

User initially proposed a "wildcard index" form like
`td:nth-of-type(n)` as a multi-element boundary. That form is already legal
CSS (`n` is the standard variable) and the engine already supports
multi-match boundaries (verified at
`services/extraction-service/app/extractor/engine.py:86-93` —
`body.select(root_boundary)` returns a list and the engine iterates). However,
that selector matches all 9 `<td>`s per table and yields 9 single-field
"records," not 3 full ones. So the multi-match-boundary path doesn't
recover the structure.

User's selector example also mixed jQuery extensions (`:eq(0)`) into CSS;
those won't parse in BeautifulSoup/Soup Sieve. Translations: `:eq(0)` →
`:first-of-type` or `:nth-of-type(1)` (CSS is 1-indexed).

## What we ruled out, and why

- **AI/LLM extraction mode.** I wrongly claimed this existed in the codebase.
  Verified at `services/extraction-service/app/routes/extraction.py:37,121` —
  the only `mode` values are `document` and `file`. No LLM path. Building a
  real one is a v2-class capability (provider dependency, prompt design,
  schema-grounded JSON, eval) and out of scope here.
- **A "transposed table" mapping mode flag.** Bakes a binary axis ontology
  into the mapper. The next pathological site won't be column-vs-row; it
  will be whatever-vs-whatever. User explicitly rejected this framing.
- **User-supplied scripts/lambdas/DSL.** Turns the mapper into a programming
  environment and breaks the schema/UI model. Not on the table.
- **Manual `count` integer override.** User pushed back: "If it can't be
  modeled in the DOM, it's likely to be infeasible during extraction." Agreed
  — DOM-derived counts only.
- **"Field selectors must be subset of count selector" validation.** I argued
  against it; user agreed. CSS-selector subset is not statically decidable,
  and the strict reading would forbid the legitimate case (field selectors
  in rows the count selector never visits).
- **Pre-DOM transform** (rewrite the soup to wrap each column in a synthetic
  `<div class="record">`). Considered as option #2 in earlier discussion;
  rejected as brittle (assumes fixed column count) and as a localized hack
  rather than a general primitive.
- **Hand-flattening into 9 fields per page** (`title_1..3`, `details_1..3`,
  `image_1..3`). Considered as option #1; rejected because the schema stops
  resembling the data and downstream consumers need to reshape.

## Where we landed

**Pure named index iterator** as a new boundary primitive. No axis ontology.
The engine has no opinion about what the index is "for" — it's just an
integer that gets substituted into selector templates.

Key elements:

1. Boundaries gain an optional `iterators` list. Each iterator: `name`,
   `count_selector`, optional `anchor`.
2. `count_selector` is evaluated relative to each boundary match; its match
   count is `N` for that match.
3. Field selectors at that boundary scope are templates with `{name}`
   substitution. The engine substitutes for each `i` in `1..N` and runs
   per-field extraction with the resolved selectors.
4. Multiple iterators per boundary → Cartesian iteration (free).
5. Iterators on nested boundaries → recursion (free).
6. Sparse-record skip via an **anchor field** — empty anchor → skip iteration.
   Defaults to first declared field.

Validation, all at preview time:
- Iterator must be referenced (≥1 field uses `{name}`).
- Count selector must match ≥1 element on the sample page.
- Post-substitution field selectors must parse as valid CSS.
- Anchor field must exist in the boundary's scope, if declared.

Backwards compat: mappings without `iterators` behave exactly as today. No
flag day, no migration.

## Concrete config that should work for `example.html`

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
        selector: "tr:nth-of-type(1) > td:nth-of-type({i}) span"
      - field_path: details
        selector: "tr:nth-of-type(2) > td:nth-of-type({i}) blockquote"
      - field_path: images
        selector: "tr:nth-of-type(3) > td:nth-of-type({i}) img[src]"
        attribute: src
      - field_path: performer
        url_regex: "member_videos_(\\w+)\\.htm"
```

Expected output: 4 records (3 from table 1, 1 from table 2 — columns 2 and 3
of table 2 skipped via anchor).

## Existing engine touchpoints to keep in mind

- `services/shared/models.py:203` — `BoundaryMapping` model. Will need
  extension (or a parallel class) for `iterators`.
- `services/shared/models.py:210` — `DocumentExtractionConfig` carries the
  root-level boundary; root iterators belong here.
- `services/extraction-service/app/extractor/engine.py:280` —
  `_extract_record`. The substitution and loop are inserted around this.
- `services/extraction-service/app/extractor/engine.py:354` —
  `_extract_collection`. Same shape; iterators on nested collections live
  here.
- `services/extraction-service/app/extractor/engine.py:86` — root-boundary
  selection. Multi-match is already correct; no change here.
- `services/shared/pipeline_stages.py:66` — `mapper.complete` predicate.
  Decide whether iterator-bearing mappings need a tighter rule.

## Caveats from the sample HTML

- The HTML is FrontPage 6.0 garbage (note the stray `</span>` at line 131
  outside any open `<span>`). BeautifulSoup will normalize, but `:nth-of-type`
  indices may come out a cell off from raw view-source. Validate against
  parsed soup, not the file.
- Multiple record-tables per page — column iteration must be table-scoped, so
  the boundary correctly matches `table[border='7']` (each table) and the
  iterator runs inside each match.
- Sparse columns in the second table — anchor-skip handles this.

## Open questions for next session

1. Iterator naming — lock to `i`/`j`/`k` or allow arbitrary names? Suggested
   per-boundary scope with shadow warnings.
2. Does `count_selector` need `:scope` support? Soup Sieve handles relative
   selectors against the matched element by default; verify during prototype.
3. UI affordance: live count badge ("this matches 3 elements") next to the
   count selector input in the Content Mapper. Pattern likely already exists
   for boundary selectors.
4. Anchor multi-field AND/OR rule. Probably YAGNI; revisit only if a real
   case appears.
5. Interaction of iterator-produced records with `merge_by`. Likely fine,
   but worth a unit test.

## What the user wants to do next

User created the `iterative-boundary` branch. Plan: prototype this design
against `example.html`. Review the design doc at
`docs/iterative-boundary-design.md` and provide feedback before any
implementation begins.

## Behavioral notes worth carrying forward (not memory-worthy yet)

- I overclaimed once in this session ("AI mode exists" — it doesn't). User
  caught it. The cost was small because I verified on the next turn. Default
  to checking the codebase before referencing features by name.
- User pushes back precisely and is happy when pushback is reciprocated. The
  conversation converged faster after I said "I think you're wrong about
  subset-validation; here's why" than it would have if I'd hedged.
- Project's anti-accumulator stance (CLAUDE.md "the one rule") is real. This
  design honors it: iterators are stateless DOM-derived integers, not
  client-side step-trackers.
