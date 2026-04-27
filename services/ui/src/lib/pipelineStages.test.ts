/**
 * Mirror of tests/test_pipeline_stages.py — same fixture file, same
 * assertions. If a case is added to the JSON it runs here too. Keeping
 * the two implementations in lockstep is the entire point.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  computePipelineStages,
  nextStageForJob,
  STAGE_NAMES,
  type PipelineStages,
  type StageInfo,
  type StageStatus,
} from './pipelineStages'

const __dirname = dirname(fileURLToPath(import.meta.url))
const FIXTURE_PATH = resolve(
  __dirname,
  '../../../../tests/fixtures/pipeline_stages.json',
)

interface FixtureCase {
  name: string
  job: Record<string, unknown> | null
  expected: Partial<Record<keyof PipelineStages, { status: string; message?: string }>>
}

const cases: FixtureCase[] = JSON.parse(readFileSync(FIXTURE_PATH, 'utf8'))

describe('computePipelineStages — fixture parity with pytest', () => {
  for (const c of cases) {
    it(c.name, () => {
      const result = computePipelineStages(c.job)
      for (const [stage, exp] of Object.entries(c.expected)) {
        const got = result[stage as keyof PipelineStages]
        expect(got.status, `[${c.name}] ${stage} status`).toBe(exp!.status as StageStatus)
        if ('message' in exp!) {
          expect(got.message ?? null, `[${c.name}] ${stage} message`).toBe(
            exp!.message ?? null,
          )
        }
      }
    })
  }
})

describe('computePipelineStages — edge cases', () => {
  it('null job returns all pending', () => {
    const result = computePipelineStages(null)
    for (const stage of STAGE_NAMES) {
      expect(result[stage].status).toBe('pending')
    }
  })

  it('undefined job returns all pending', () => {
    const result = computePipelineStages(undefined)
    for (const stage of STAGE_NAMES) {
      expect(result[stage].status).toBe('pending')
    }
  })

  it('returns a fresh object on every call (no shared references)', () => {
    const a = computePipelineStages({ status: 'created', scrape_config: {} })
    const b = computePipelineStages({ status: 'created', scrape_config: {} })
    // Mutating one must not affect the other.
    ;(a.config as { status: string }).status = 'mutated'
    expect(b.config.status).toBe('pending')
  })

  it('StageInfo includes message field shape', () => {
    const info: StageInfo = { status: 'warning', message: '5 errors' }
    expect(info.message).toBe('5 errors')
  })
})

describe('nextStageForJob — landing-stage picker', () => {
  const cases: Array<{ name: string; job: unknown; expected: string }> = [
    { name: 'null → config', job: null, expected: 'config' },
    {
      name: 'created_no_seeds → config',
      job: { status: 'created', scrape_config: {} },
      expected: 'config',
    },
    {
      name: 'created_with_seeds → scrape',
      job: { status: 'created', scrape_config: { seed_urls: ['https://x/'] } },
      expected: 'scrape',
    },
    {
      name: 'scraping → scrape',
      job: { status: 'scraping', scrape_config: { seed_urls: ['https://x/'] } },
      expected: 'scrape',
    },
    {
      name: 'scraped_no_extraction → schema',
      job: { status: 'scraped', scrape_config: { seed_urls: ['https://x/'] } },
      expected: 'schema',
    },
    {
      name: 'scraped_with_warnings → schema (warning is complete-enough)',
      job: {
        status: 'scraped',
        scrape_config: { seed_urls: ['https://x/'] },
        resources_errored: 12,
      },
      expected: 'schema',
    },
    {
      name: 'scraped_schema_only → mapper',
      job: {
        status: 'scraped',
        scrape_config: { seed_urls: ['https://x/'] },
        extraction_config: { mode: 'document', schema_id: 'abc' },
      },
      expected: 'mapper',
    },
    {
      name: 'scraped_full_config → results',
      job: {
        status: 'scraped',
        scrape_config: { seed_urls: ['https://x/'] },
        extraction_config: {
          mode: 'document',
          schema_id: 'abc',
          document: { field_mappings: [{ field_path: 'x', selector: 'p' }] },
        },
      },
      expected: 'results',
    },
    {
      name: 'extracting → results',
      job: {
        status: 'extracting',
        scrape_config: { seed_urls: ['https://x/'] },
        extraction_config: {
          mode: 'document',
          schema_id: 'abc',
          document: { field_mappings: [{ field_path: 'x', selector: 'p' }] },
        },
      },
      expected: 'results',
    },
    {
      name: 'completed → results',
      job: {
        status: 'completed',
        scrape_config: { seed_urls: ['https://x/'] },
        extraction_config: {
          mode: 'document',
          schema_id: 'abc',
          document: { field_mappings: [{ field_path: 'x', selector: 'p' }] },
        },
      },
      expected: 'results',
    },
    {
      name: 'failed_at_scrape → scrape',
      job: {
        status: 'failed',
        failed_stage: 'scrape',
        scrape_config: { seed_urls: ['https://x/'] },
      },
      expected: 'scrape',
    },
    {
      name: 'failed_at_extract → results',
      job: {
        status: 'failed',
        failed_stage: 'extract',
        scrape_config: { seed_urls: ['https://x/'] },
        extraction_config: {
          mode: 'document',
          schema_id: 'abc',
          document: { field_mappings: [{ field_path: 'x', selector: 'p' }] },
        },
      },
      expected: 'results',
    },
  ]

  for (const c of cases) {
    it(c.name, () => {
      expect(nextStageForJob(c.job)).toBe(c.expected)
    })
  }
})
