# Site Extractor — Project Contract

This document captures the architectural commitments that the codebase is built on. It is the canonical reference for "how is this thing supposed to work" — read it before making non-trivial changes. The README covers user-facing setup and operations; this file covers the contracts that hold the system together.

For repo orientation, env vars, and dev commands, see `README.md`.

---

## The one rule

**The server's job record is the sole source of truth for pipeline state. The client renders pure projections of that record. There is no client-side accumulator for "what has been done."**

Concretely:

- The wizard stepper visuals derive from `computePipelineStages(activeJob)` (a `useMemo` over the cached job record). Nothing on the client tracks "step N is done" as its own state.
- The redirect target for a bare `/job/<id>` URL derives from `nextStageForJob(activeJob)` — same projection, picking the first non-complete stage.
- The client never mutates pipeline state directly. It issues server actions (start-scrape, pause, cancel, extraction/start) and waits for the resulting job record to arrive back via the action's response, the WebSocket relay, or react-query refetch.
- Transient UI state (form-edit buffers, scroll position, picker selection) lives in `useState` inside the component. It must not outlive the component.

If a future change ever feels like it wants a `markStepCompleted`-style accumulator, that's a sign the change is fighting the model. Stop and reframe.

---

## Pipeline state derivation

The single source of derivation lives in two twin files that must agree:

- **Python**: `services/shared/pipeline_stages.py` — `compute_pipeline_stages(job)` and `next_stage_for_job(job)`.
- **TypeScript**: `services/ui/src/lib/pipelineStages.ts` — `computePipelineStages(job)` and `nextStageForJob(job)`.

Both consume the same fixture table at `tests/fixtures/pipeline_stages.json`. Pytest and vitest each load the JSON and assert against the same expected outputs, so divergence between the two implementations fails one suite or the other.

### `StageStatus` values

```ts
type StageStatus = 'pending' | 'in_progress' | 'complete' | 'warning' | 'failed'
```

- `complete` — green check.
- `warning` — yellow alert-triangle. Sub-state of complete: stage finished, but with per-URL errors that don't promote to whole-stage failure (e.g., `pages_errored > 0` on a `scraped` job).
- `failed` — red X. Whole-stage failure, attributed via `failed_stage`.
- `in_progress` — animated spinner.
- `pending` — numbered circle.

### Per-stage rules

Read directly from `compute_pipeline_stages` — they're short and authoritative. The non-obvious ones:

- **`scrape.complete`** is true even if `status='failed'` *and* `failed_stage='extract'` — the scrape did finish; the failure happened later.
- **`scrape.warning`** fires whenever `pages_errored > 0` or `resources_errored > 0` and the stage is otherwise complete. Per-URL errors do NOT flip the job to `failed`. That's reserved for whole-stage failures.
- **`schema.complete`** requires `extraction_config.schema_id` AND `extraction_config.mode`. Either alone is `pending`.
- **`mapper.complete`** requires `field_mappings.length >= 1` AND each mapping has either `selector` or `url_regex`.

### Per-stage editing happens client-side; persistence happens on the server

The Schema Builder and Content Mapper both edit local component state (form fields, JSON text). Saving sends the result to the gateway via `PATCH /api/jobs/{id}` (extraction_config) or `POST /api/schemas` (schema record). Until that PATCH succeeds, the stage is `pending` from the server's point of view. The Save and Continue handlers on the Schema Builder both auto-commit pending JSON-tab edits and call `saveMutation` before advancing — Continue is "ready for the next step", not "I'll save this for you later."

---

## Job status state machine

Single source of truth: `services/shared/state_machine.py`.

### Statuses

| Status | Meaning |
|---|---|
| `created` | Job exists; scrape_config present; scrape not started |
| `scraping` | Scraper actively running |
| `paused` | Scrape paused; resumable |
| `scraped` | Scrape finished; ready for schema/mapper |
| `extracting` | Extraction-service running |
| `completed` | Extraction finished; results available |
| `failed` | Whole-stage failure. Pair with `failed_stage` ∈ {scrape, extract} |
| `cancelled` | User-cancelled; partial state preserved |

`mapping` was an alias for "scraped + user editing the mapper UI" — UI activity never warranted a distinct job status. Removed.

### Legal transitions

```
created     → scraping
scraping    → scraped | paused | failed | cancelled
paused      → scraping | scraped | failed | cancelled
scraped     → scraping (re-scrape) | extracting
extracting  → completed | failed | cancelled
completed   → scraping (re-scrape) | extracting (re-extract)
failed      → scraping (retry scrape) | extracting (retry extract)
cancelled   → scraping (resume from scratch)
```

Same-status writes (`current → current`) are treated as legal no-ops so callers stay idempotent on duplicate events.

### Enforcement

Every status write goes through `db.update_status(job_id, new_status, extras=None)`, which validates against the graph and raises `IllegalTransition` if the transition is illegal.

There are two enforcement layers:

1. **Route-level pre-check** (e.g., `pause` only accepts `status='scraping'`). Returns **400** with a friendly message ("Can only pause a running scrape"). Most user-driven illegals surface here.
2. **`update_status` graph validator**. Returns **409** when bypassed (background relay events, future endpoints, internal callers).

Both block illegal writes. The pre-checks exist because they're more useful to the user; the validator exists because it can't be bypassed by adding a new endpoint that forgets the pre-check.

### Status changes do NOT flow through `PATCH /api/jobs/{id}`

`status` is intentionally not in `update_job`'s `allowed_fields`. Pipeline transitions go through dedicated endpoints (`/start-scrape`, `/pause`, `/cancel`, `/extraction/{id}/start`, `/internal/jobs/{id}/scraped`). PATCH is for editable metadata (`name`, `extraction_config`, `extraction_mode`).

---

## Failure attribution

When `status='failed'`, `failed_stage` partitions the failure:

- `failed_stage='scrape'` — scraper unreachable, scraper crashed, scrape_config rejected (e.g. credential decrypt error)
- `failed_stage='extract'` — extraction-service unreachable, extraction engine threw, schema lookup failed

Per-URL errors during scraping (broken images, dead sponsor links) do NOT flip the job to `failed`. They live in `pages_errored` / `resources_errored` and surface as a `warning` on the scrape stage.

### Retry clears attribution

`start_scrape` and `start_extraction` both pass `failed_stage: None` (and `error_message: None` on extract retry) when transitioning out of failed → in-progress. So a retry's stepper visual goes red → spinner → green/red based on the new run, not stuck red from the prior failure.

---

## Clone semantics

Cold clone only.

`POST /api/jobs/{id}/clone` body:

```json
{ "name_override": "..." }
```

Both fields optional. Behavior:

- Copies: `scrape_config`, `extraction_config`, `extraction_mode`, `name` (or `name_override` if provided)
- Drops: scrape pages on disk, extraction results, counters, timestamps, failure state
- New job lands in `created` status
- Returns the full new job record (no follow-up GET needed)

The UI's HistoryPage clone button calls this endpoint. There is no client-side draft path.

---

## Client store contract

`services/ui/src/stores/jobStore.ts` is intentionally small:

- `activeJob` — cached server record (mirror of `useQuery(['job', id])`).
- `setActiveJob(job)` — clears `scrapeEvents` atomically when the job id changes. The reset boundary; per-job state cannot leak across views.
- `updateActiveJob(updates)` — partial merge into `activeJob`. Used by `handleWSEvent` to apply server-pushed deltas, and by component-side optimistic updates after a 200 response from a state-changing action.
- `scrapeEvents` — UI-only event log for the activity panel (capped at 500 entries). View-scoped and cleared on `setActiveJob` id change.
- `addScrapeEvent` — append to `scrapeEvents`.
- `handleWSEvent` — routes WS broadcasts to (a) `updateActiveJob` for counters/status and (b) `addScrapeEvent` for the log.

That's the entire surface. If you find yourself adding fields here for "UI step state" or "draft form values" — stop. Step state is derived from `activeJob`. Form drafts are component `useState`.

---

## Multi-window correctness

- **Same job, two windows**: both windows mount the same `useQuery(['job', jobId])`. Each window's `useWebSocket` hook receives the same server-pushed events and dispatches `handleWSEvent` to its own store. Both stay in sync because both consume the same server-driven event stream.
- **Different jobs, two windows**: each window's WS handler filters by `event.job_id === activeJob?.id` and ignores cross-window noise.
- **Same window navigating between jobs**: `setActiveJob(newJob)` clears `scrapeEvents`. The derived `stages` recomputes from the new `activeJob`. No leak.
- **Concurrent action on the same job from two windows**: handled by **server idempotency only**. The server's state-machine rejects a duplicate `start_extraction` while `status='extracting'` (returns 400 from the route pre-check); the second window gets an error toast. Refresh picks up the live status from the server. No client-side mutex.

---

## Test data semantics

- **Re-runs overwrite**, no history. `clear_scrape_data` (re-scrape) wipes `scrape_pages`, `scrape_resources`, and `extraction_results` for the job. `save_extraction_results` (re-extract) deletes prior result rows for the job before insert. The DB always reflects only the current run.
- **Re-scrape from `completed` discards extraction results.** The Re-scrape button's confirm dialog warns about this when the source status is `completed`.

---

## Tests

- **Python**: `pytest` from project root (uses `.venv/`). Covers `compute_pipeline_stages`, `next_stage_for_job`, and the legal-transition matrix. 104 cases at last count.
- **TypeScript**: `npm test` in `services/ui`. Vitest, mirrors the same fixture table. 38 cases.

Both suites must pass before any pipeline-state change ships. A drift in either implementation fails one suite or the other.

---

## Reaction Maker is a style reference, not a code reference

`services/ui` borrows the visual language (dark theme, sidebar nav, FlyonUI tokens, Tabler icons) from the reaction-maker project. It does NOT borrow application architecture. The user is rebuilding reaction-maker as `reaction-maker-2` because reaction-maker's domain-state patterns are bad — specifically the kind of session-scoped accumulator we removed here.

If reaction-maker does it one way and this project does it differently, this project is right.

---

## Out of scope

These would be larger redesigns. Not blocked, not planned.

- Run history (compare past extractions). Today re-runs overwrite; that was an explicit decision.
- Multi-user / multi-tenancy / per-job ownership. Today every job is visible to every client.
- Authentication. The gateway is open on the published port.
- Cross-window action coordination beyond server idempotency.
- Replacing zustand with redux/jotai/etc. The issue is what we put in zustand, not which library we use.
