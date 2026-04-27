# Pipeline State Design

## Status

Draft — proposed redesign of how site-extractor handles job pipeline state across the API gateway, the database, and the React UI. Targets the bugs surfaced when running cloned jobs through the wizard, but the scope is broader: replace the current "session-scoped accumulator" model with a "server-authoritative, client-projected" model.

This is a focused redesign of one concern (pipeline state). It is not a full architecture overhaul. The reaction-maker-2 effort is a useful reference for direction; site-extractor will move toward that direction without going as far.

---

## Why this redesign exists

### What's wrong today

The wizard's step indicators (✓ checks on Scraper Config / Scrape Monitor / Schema Builder / Content Mapper / Results) are driven by `completedSteps: Set<number>` in the zustand store at `services/ui/src/stores/jobStore.ts:19,45`. The store exposes `markStepCompleted(step: number)` (line 46) which is the **only** way the set is mutated — there is no `reset` or per-job clear. The set is monotonic for the lifetime of the page session.

`WizardPage.tsx:114-119` re-derives implied step marks from job status whenever a job loads:

```ts
const impliedStage = stageForJobStatus(jobData);
const impliedStep = STAGE_TO_INDEX[impliedStage];
for (let i = 0; i < impliedStep; i++) markStepCompleted(i);
if (jobData.status === "completed") markStepCompleted(4);
```

But this only **adds** to the set. So when the user navigates from a `completed` job (set = {0,1,2,3,4}) to a freshly-cloned `created` job (impliedStep = 0, loop adds nothing), the cloned job's stepper still renders all five checks because the prior session's marks leak through.

The Clone action makes this acutely visible: `HistoryPage.tsx:79-83` does a purely client-side prefill — it fetches the source job, calls `setDraft(src.scrape_config, ...)`, and navigates to `/job/new`. It does **not** call `POST /api/jobs/{id}/clone` (which already exists and correctly copies `extraction_config` and `extraction_mode` — `services/extractor-gateway/app/routes/jobs.py:164`). So the cloned job arrives with no extraction config but inherits the previous job's stepper checks. Clicking Re-run on Results then fails with "No extraction config set on job" while the wizard still claims every stage is done.

### Why this is a structural problem, not a small bug

- **Scope mismatch**: pipeline state describes a job, not a session. Putting it in session state breaks every multi-job scenario — sequential clones, two windows on different jobs, two windows on the same job, refresh, deep-link.
- **Multi-window**: each window has its own zustand store. Two windows on the same job will diverge after the first interaction in either. Two windows on different jobs are independent silos but neither sees server-driven progress for the other.
- **No source of truth**: pipeline progress is partially derivable from server fields (`status`, `extraction_config`, `*_at` timestamps) and partially stored client-side (`completedSteps`, manual `markStepCompleted` calls in handlers). When they disagree, the client wins on display and the server wins on action — which is how we got "wizard says all done" + "extraction says no config."
- **Two clone paths**: the server has a correct `clone_job`; the UI bypasses it with its own incomplete client-side prefill. Two implementations of the same operation guarantee drift.

---

## Core principle

**The server's job record is the sole source of truth for pipeline state. The client renders pure projections of that record. There is no client-side accumulator for "what has been done."**

Concretely:

- `completedSteps` ceases to exist as state. Step completion is a `useMemo` over `activeJob`.
- `markStepCompleted` ceases to exist as a method. Nothing in the client mutates pipeline state directly; it issues server actions and waits for the resulting job record to come back.
- Cross-window correctness comes from (a) react-query as the per-window cache of server state, and (b) WebSocket broadcasts as the cross-window invalidation signal. Two windows on the same job stay in sync because both subscribe to the same server-driven updates.
- Transient UI state (mid-edit form values, scroll position, "user has clicked but server hasn't confirmed yet") is **local component state** (`useState`/`useReducer`), not store state. It must not outlive the component.

---

## Server-side: explicit job state machine

### Status enum

The job's `status` column is the canonical state. Today's values, audited:

| Status       | Meaning                                                                                        | Set by                             |
| ------------ | ---------------------------------------------------------------------------------------------- | ---------------------------------- |
| `created`    | Job exists; scrape_config present; scrape not started                                          | `POST /api/jobs`, `clone_job`      |
| `scraping`   | Scraper actively running                                                                       | `POST /api/jobs/{id}/start-scrape` |
| `paused`     | Scrape paused by user; resumable                                                               | `POST /api/jobs/{id}/pause`        |
| `scraped`    | Scrape finished cleanly; ready for schema/mapper                                               | scraper → `mark_scraped`           |
| `mapping`    | (Currently used as alias for "scraped, user editing mappings"; ambiguous — see proposal below) | `PATCH /api/jobs/{id}`             |
| `extracting` | Extraction-service running                                                                     | `POST /api/extraction/{id}/start`  |
| `completed`  | Extraction finished; results available                                                         | `start_extraction` finalizer       |
| `failed`     | Terminal failure at any stage; `error_message` set                                             | various                            |
| `cancelled`  | User-cancelled; partial state preserved                                                        | `POST /api/jobs/{id}/cancel`       |

### Proposed cleanup

1. **Remove `mapping` status.** It conflates "scraped and ready" with "user editing the mapper UI." Since editing the mapper is a UI activity that doesn't change job state, the underlying job is just `scraped`. The mapper page shows up because the URL stage is `mapper`, not because the job has a `mapping` status.

2. **Add a `scrape_failed_at` distinction from `extract_failed_at`.** Currently `failed` is one bucket. The wizard needs to know "scrape failed" vs "extract failed" to highlight the right step. Either two timestamp columns, or a `failed_stage` column (`scrape | extract`) alongside `failed`.

3. **Document the legal transition graph** (no enforcement code yet — just write it down in this spec and add an assertion in `update_status` later):

   ```
   created     → scraping → {scraped | failed | cancelled}
   scraping    ↔ paused
   scraped     → extracting → {completed | failed}
   scraped     → scraping (re-scrape)
   completed   → extracting (re-extract)
   completed   → scraping (re-scrape after full pipeline)
   failed      → scraping  (retry after a scrape failure)
   failed      → extracting (retry after an extract failure, only if scrape data still present)
   cancelled   → scraping  (resume from scratch)
   any (non-running) → cancelled
   ```

   Illegal transitions (e.g. `created → completed`, `extracting → scraping`, `scraping → extracting`) should be rejected at the gateway boundary, not silently accepted.

   `completed → scraping` is **already implemented** in `start_scrape` today (line 202 accepts `completed`; `clear_scrape_data` wipes the page index and counters). Two follow-ups close the gaps:
   - `clear_scrape_data` must also `DELETE FROM extraction_results WHERE job_id = ?` so re-scraping doesn't leave orphan result rows. Today the orphans are hidden by `get_extraction_results`'s "latest" filter but they accumulate forever.
   - `ScrapeMonitorStep.tsx`'s Re-scrape button is gated on `status === 'scraped'`; widen to `status === 'scraped' || status === 'completed'`. Same for failed/cancelled if not already.

### Per-URL errors do not promote to job-level `failed`

A scrape ending with successful pages but per-URL errors (broken sponsor images, dead asset URLs) stays `scraped`. The job-level `failed` status is reserved for **whole-stage** failures: scraper-service crashed, scrape_config rejected, network completely dead, extraction engine threw, etc.

Per-URL errors live in the `*_errored` counters and surface in the wizard as a "completed with warnings" indicator on the affected stage (yellow/warning color, not red/failure color). The `failed_stage` field is only set for whole-stage failures.

### Pipeline-stage derivation (single function on the server, mirrored on the client)

A pure function: given a job record, return per-stage status. This lives in two places, but shares the same logic:

- Python: `services/shared/pipeline_stages.py` (new file)
- TypeScript: `services/ui/src/lib/pipelineStages.ts` (new file)

Both compute the same result from the same inputs. The shapes:

```ts
type StageStatus =
  | "pending"
  | "in_progress"
  | "complete"
  | "failed"
  | "blocked";

interface PipelineStages {
  config: StageStatus; // scrape_config present + valid
  scrape: StageStatus; // status in {scraped, extracting, completed, failed-after-scrape}
  schema: StageStatus; // extraction_config.schema_id resolves
  mapper: StageStatus; // extraction_config.document.field_mappings present + valid
  results: StageStatus; // status === 'completed'
}
```

Rules (simplified — the spec function defines them precisely):

- `config.complete` ⇔ `scrape_config.seed_urls.length > 0`
- `scrape.in_progress` ⇔ `status in {scraping, paused}`
- `scrape.complete` ⇔ `status in {scraped, extracting, completed}` or (failed/cancelled at extract stage)
- `scrape.warning` ⇔ `scrape.complete` AND (`pages_errored > 0` OR `resources_errored > 0`) — partial-success scrape
- `scrape.failed` ⇔ `status === 'failed' && failed_stage === 'scrape'`
- `schema.complete` ⇔ `extraction_config?.schema_id` exists AND the schema is fetchable AND `extraction_config.mode` is set
- `mapper.complete` ⇔ `extraction_config.document.field_mappings.length >= 1` AND each mapping has selector or url_regex
- `results.in_progress` ⇔ `status === 'extracting'`
- `results.complete` ⇔ `status === 'completed'`
- `results.failed` ⇔ `status === 'failed' && failed_stage === 'extract'`

`blocked` exists for "this stage requires an earlier stage to complete first." E.g., `mapper.blocked` when `schema` is `pending`. `warning` is a sub-state of `complete` — the stage rendered yellow/warning instead of green, but the user is still allowed to proceed; this exists because per-URL scrape errors are not job-level failures (per locked-in design decision).

Add `warning` to the `StageStatus` union:
```ts
type StageStatus = 'pending' | 'in_progress' | 'complete' | 'warning' | 'failed' | 'blocked'
```

### Clone semantics — make them explicit (cold clone only)

Clone is **cold**: copy `scrape_config` + `extraction_config` + `extraction_mode` + `name`, land in `created` status with no scraped pages on disk and no extraction_results. The user re-scrapes from scratch in the clone. (A "warm" clone — duplicating the scraped page set on disk so the clone is `scraped` and ready to re-extract — is explicitly out of scope for v1; if the use case emerges later it'll be exposed as a separate "Re-extract from snapshot" action, not as a clone-flag.)

The wire format:

```
POST /api/jobs/{id}/clone
Body: {
  "name_override": "..."   // optional; default "{src.name} (copy)"
}
```

Returns the new job record (full, after persistence), not just `{job_id}`. The client takes the response, navigates to `/job/<new-id>/config` (since the cloned job is always `created`), and react-query is already populated with the new job.

The existing `clone_job` in `services/extractor-gateway/app/routes/jobs.py:164` is already *almost* correct — it copies `scrape_config`, `extraction_config`, and `extraction_mode`. Two small changes: (1) accept the optional `name_override` body field, (2) return the full job record instead of `{job_id, status, message}`.

The client's HistoryPage clone handler is rewritten to call this endpoint. The `setDraft` / `draftConfig` / `draftName` plumbing in `jobStore` is **deleted** — it was only there to support the client-side clone hack and has no other consumer.

---

## Client-side: projection, not accumulation

### Removals from `jobStore`

| Field / method                         | Reason                                                                                       |
| -------------------------------------- | -------------------------------------------------------------------------------------------- |
| `completedSteps: Set<number>`          | Replaced by `useMemo` selector over `activeJob`                                              |
| `markStepCompleted(step)`              | Pipeline state isn't mutated by the client; replaced by server actions + react-query refetch |
| `draftConfig`, `draftName`, `setDraft` | Only existed to support client-side clone; replaced by server clone endpoint                 |

### Keeps in `jobStore` (with guardrails)

| Field                                                 | Why it stays                                                                                                                 |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `activeJob`, `setActiveJob`, `updateActiveJob`        | Tabular cache of the currently-viewed job. Mirrors react-query but lets non-component code (like `handleWSEvent`) update it. |
| `scrapeEvents`, `addScrapeEvent`, `clearScrapeEvents` | UI-only event log for the activity panel; legitimately ephemeral and view-scoped. Cleared on `setActiveJob`.                 |
| `handleWSEvent`                                       | Routes WS broadcasts to (a) `updateActiveJob` for counters/status and (b) `addScrapeEvent` for log.                          |

The store shrinks to: one cached job, one event log, and the methods to update them. Everything else is derived.

### `setActiveJob` becomes the reset boundary

`setActiveJob(job)` clears `scrapeEvents` (and any future per-job state we add) atomically when the job ID changes. This eliminates the leak class entirely — there is no per-session accumulator left to leak.

### Stepper props derive from the job

`Stepper` (`services/ui/src/components/Stepper.tsx`) takes `stages: PipelineStages` instead of `completedSteps: Set<number>`. The component renders each step's icon/color from its `StageStatus`. The wizard composes:

```tsx
const stages = useMemo(() => computePipelineStages(activeJob), [activeJob])
return <Stepper stages={stages} currentStep={currentStep} onStepClick={...} />
```

`isAccessible` (currently `Stepper.tsx:37`) becomes "all earlier stages are `complete` or `in_progress`." The current `completedSteps.has(i - 1)` check is replaced by `stages[STAGES[i-1]].status in {complete, in_progress}`.

### Multi-window correctness

- **Same job, two windows**: both windows mount the same `useQuery(['job', jobId])`. Each window's `useWebSocket` hook subscribes to the same server channel and dispatches `handleWSEvent`, which calls `updateActiveJob` and `addScrapeEvent` in its own store. Both stay in sync because both consume the same server-driven event stream. (TanStack Query also broadcasts cache updates across tabs via `broadcastQueryClient` if we want belt-and-braces.)
- **Different jobs, two windows**: each window's WS handler filters by `event.job_id === activeJob?.id` (already implemented at `jobStore.ts:66`). Cross-window noise is dropped. ✓
- **One window navigating between jobs**: `useEffect` calls `setActiveJob(newJob)` which clears `scrapeEvents`. The derived `stages` recomputes from the new `activeJob`. No leak. ✓
- **Concurrent action across windows (same job)**: handled by **server idempotency only**. If window A has clicked Start Extract and the job is `extracting`, window B clicking Start Extract gets a 400 from `start_extraction` ("Cannot extract from status 'extracting'") and a toast. No client-side mutex, no presence indicator, no "another window is doing this" UI. If window B is refreshed it picks up the live status from the server. This was an explicit scope decision — cross-window action coordination is more complexity than the rare collision warrants.

---

## Migration plan (incremental, each step independently shippable)

1. **Add `services/shared/pipeline_stages.py` and `services/ui/src/lib/pipelineStages.ts`** with the `computePipelineStages` function and exhaustive tests against fabricated job records covering every status × every extraction_config shape. No call sites changed yet.

2. **Convert `Stepper` to take `stages: PipelineStages`.** Update `WizardPage` to compute `stages` via `useMemo` and pass it down. Keep `completedSteps` in the store temporarily so other code paths still work; just stop using it for the Stepper. Verify the wizard renders correctly across all statuses by clicking through job history.

3. **Rip out `markStepCompleted` calls** from `WizardPage.tsx` (lines 118-119, 161, 183, 209) and `jobStore.ts:113,115`. After this step, `completedSteps` is unused.

4. **Delete `completedSteps` and `markStepCompleted`** from `jobStore` and the `JobStore` interface.

5. **Add `setActiveJob` reset semantics**: when the new job's id !== the old job's id, clear `scrapeEvents`. (Already mostly correct; just make it explicit.)

6. **Server**: add `failed_stage` column + migration. Update `mark_scraped` and `start_extraction` to set it on failure. Update `computePipelineStages` to use it. Drop the `mapping` status.

7. **Server**: rewrite `clone_job` to take the optional flags, return the full new job record. Add request validation.

8. **Client**: rewrite HistoryPage clone handler to call the server endpoint, navigate to the returned job's URL. Delete `setDraft` / `draftConfig` / `draftName` from the store and all references.

9. **Server**: enforce the legal transition graph in `db.update_status` (reject illegal transitions with 409). Catch any callers depending on illegal transitions and fix them.

10. **Final audit**: grep for any remaining client-side pipeline state. Document any survivors with rationale or remove them.

Each step is mergeable on its own. Steps 1–4 fix the immediate visible bugs. Steps 5–10 close the design gap so the same shape doesn't reappear.

---

## Verification

Per-step verification at the bottom of each migration step:

- **Steps 1-4**: Run job 05874db6 (or a fresh one) through Job History → Clone → wizard. Stepper checks should reflect _only_ the cloned job's actual state, not the source's. Open the cloned job in a second window; both windows should show identical stepper state. Refresh either; same.
- **Step 5**: Navigate back-and-forth between two jobs in the same window; `scrapeEvents` panel must clear on each navigation.
- **Step 6**: A job that fails during scrape vs during extraction should show the failure on the correct stepper step.
- **Step 7-8**: `POST /api/jobs/{id}/clone` from curl returns a new job with `extraction_config` populated; the UI's Clone button produces the same result and navigates to the new job page.
- **Step 9**: `curl -X PATCH .../jobs/{id} -d '{"status":"completed"}'` on a `created` job returns 409.
- **Step 10**: Two windows on the same scraping job both show counter ticks at the same rate, both show the same Activity Log.

---

## Out of scope (deliberately)

- Full architectural rewrite à la reaction-maker-2 (event sourcing, CQRS, projection workers).
- Multi-user / multi-tenancy semantics.
- Replacing zustand with redux/jotai/etc — the issue is what we put in zustand, not which library we use.
- Authentication, authorization, or per-user job ownership.

These may matter eventually, but the goal here is to fix the structural bug class without expanding scope.
