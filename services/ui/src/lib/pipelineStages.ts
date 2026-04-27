/**
 * Pure derivation of pipeline-stage status from a job record.
 *
 * Twin of services/shared/pipeline_stages.py — the canonical fixture
 * table at tests/fixtures/pipeline_stages.json is shared between the
 * pytest and vitest suites so divergence between the two
 * implementations is caught by CI.
 *
 * The stepper component renders a projection of this output. Nothing
 * in the client mutates pipeline state directly; every "step is done"
 * indicator is derived from the job record fetched from the gateway.
 */

export type StageStatus =
  | 'pending'
  | 'in_progress'
  | 'complete'
  | 'warning'
  | 'failed'

export interface StageInfo {
  status: StageStatus
  message?: string | null
}

export interface PipelineStages {
  config: StageInfo
  scrape: StageInfo
  schema: StageInfo
  mapper: StageInfo
  results: StageInfo
}

export const STAGE_NAMES = ['config', 'scrape', 'schema', 'mapper', 'results'] as const
export type StageName = (typeof STAGE_NAMES)[number]

// Loose shape — matches the gateway's job record after JSON deserialization.
// `unknown` so callers can pass a typed `JobDetail` (from api/client.ts), a
// raw decoded JSON object, or null/undefined without casts. Internal narrowing
// rejects anything that isn't a plain object.

function stringField(job: Record<string, unknown>, key: string): string {
  const v = job[key]
  return typeof v === 'string' ? v.trim() : ''
}

function numberField(job: Record<string, unknown>, key: string): number {
  const v = job[key]
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string') {
    const n = Number(v)
    if (Number.isFinite(n)) return n
  }
  return 0
}

function objectField(
  job: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const v = job[key]
  return v && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
}

function nullableString(job: Record<string, unknown>, key: string): string | null {
  const v = job[key]
  return typeof v === 'string' && v.length > 0 ? v : null
}

function scrapeConfigValid(scrapeConfig: Record<string, unknown>): boolean {
  const seeds = scrapeConfig['seed_urls']
  if (!Array.isArray(seeds)) return false
  return seeds.some((u) => typeof u === 'string' && u.trim().length > 0)
}

function schemaComplete(extractionConfig: Record<string, unknown>): boolean {
  if (!extractionConfig['schema_id']) return false
  if (!extractionConfig['mode']) return false
  return true
}

function mapperComplete(extractionConfig: Record<string, unknown>): boolean {
  if (!schemaComplete(extractionConfig)) return false
  const doc = extractionConfig['document']
  if (!doc || typeof doc !== 'object') return false
  const mappings = (doc as Record<string, unknown>)['field_mappings']
  if (!Array.isArray(mappings) || mappings.length === 0) return false
  for (const m of mappings) {
    if (!m || typeof m !== 'object') return false
    const obj = m as Record<string, unknown>
    if (!obj['selector'] && !obj['url_regex']) return false
  }
  return true
}

export function computePipelineStages(jobInput: unknown): PipelineStages {
  if (!jobInput || typeof jobInput !== 'object' || Array.isArray(jobInput)) {
    return {
      config: { status: 'pending', message: null },
      scrape: { status: 'pending', message: null },
      schema: { status: 'pending', message: null },
      mapper: { status: 'pending', message: null },
      results: { status: 'pending', message: null },
    }
  }
  const job = jobInput as Record<string, unknown>

  const status = stringField(job, 'status')
  const failedStage = stringField(job, 'failed_stage') || null
  const errorMessage = nullableString(job, 'error_message')

  const scrapeCfg = objectField(job, 'scrape_config')
  const extractionCfg = objectField(job, 'extraction_config')

  const pagesErrored = numberField(job, 'pages_errored')
  const resourcesErrored = numberField(job, 'resources_errored')

  // ── config ──────────────────────────────────────────────────────────────
  const config: StageInfo = scrapeConfigValid(scrapeCfg)
    ? { status: 'complete', message: null }
    : { status: 'pending', message: null }

  // ── scrape ──────────────────────────────────────────────────────────────
  const scrapeSucceeded =
    status === 'scraped' ||
    status === 'extracting' ||
    status === 'completed' ||
    (status === 'failed' && failedStage === 'extract')

  let scrape: StageInfo
  if (status === 'scraping' || status === 'paused') {
    scrape = { status: 'in_progress', message: null }
  } else if (scrapeSucceeded) {
    if (pagesErrored > 0 || resourcesErrored > 0) {
      const parts: string[] = []
      if (pagesErrored > 0) parts.push(`${pagesErrored} page error(s)`)
      if (resourcesErrored > 0) parts.push(`${resourcesErrored} resource error(s)`)
      scrape = { status: 'warning', message: parts.join(', ') }
    } else {
      scrape = { status: 'complete', message: null }
    }
  } else if (status === 'failed') {
    scrape = { status: 'failed', message: errorMessage }
  } else if (status === 'cancelled') {
    scrape = { status: 'failed', message: 'Cancelled by user' }
  } else {
    scrape = { status: 'pending', message: null }
  }

  // ── schema ──────────────────────────────────────────────────────────────
  const schema: StageInfo = schemaComplete(extractionCfg)
    ? { status: 'complete', message: null }
    : { status: 'pending', message: null }

  // ── mapper ──────────────────────────────────────────────────────────────
  const mapper: StageInfo = mapperComplete(extractionCfg)
    ? { status: 'complete', message: null }
    : { status: 'pending', message: null }

  // ── results ─────────────────────────────────────────────────────────────
  let results: StageInfo
  if (status === 'extracting') {
    results = { status: 'in_progress', message: null }
  } else if (status === 'completed') {
    results = { status: 'complete', message: null }
  } else if (status === 'failed' && failedStage === 'extract') {
    results = { status: 'failed', message: errorMessage }
  } else {
    results = { status: 'pending', message: null }
  }

  return { config, scrape, schema, mapper, results }
}
