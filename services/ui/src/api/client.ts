const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const jobs = {
  list: (params?: {
    status?: string
    search?: string
    date_from?: string
    date_to?: string
    limit?: number
    offset?: number
  }) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.search) qs.set('search', params.search)
    if (params?.date_from) qs.set('date_from', params.date_from)
    if (params?.date_to) qs.set('date_to', params.date_to)
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.offset) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return request<{ jobs: Job[]; count: number }>(`/jobs${q ? `?${q}` : ''}`)
  },
  get: (id: string) => request<JobDetail>(`/jobs/${id}`),
  create: (scrapeConfig: ScrapeConfig, name?: string) =>
    request<{ job_id: string; status: string; message: string }>('/jobs', {
      method: 'POST',
      body: JSON.stringify({ name, scrape_config: scrapeConfig }),
    }),
  update: (id: string, data: Record<string, unknown>) =>
    request<JobDetail>(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string, deleteData = true) =>
    request<{ message: string }>(`/jobs/${id}?delete_data=${deleteData}`, { method: 'DELETE' }),
  clone: (id: string, nameOverride?: string) =>
    request<JobDetail>(`/jobs/${id}/clone`, {
      method: 'POST',
      body: nameOverride?.trim() ? JSON.stringify({ name_override: nameOverride.trim() }) : undefined,
    }),
  startScrape: (id: string) =>
    request<{ job_id: string; status: string }>(`/jobs/${id}/start-scrape`, { method: 'POST' }),
  pause: (id: string) =>
    request<{ job_id: string; status: string }>(`/jobs/${id}/pause`, { method: 'POST' }),
  cancel: (id: string) =>
    request<{ job_id: string; status: string }>(`/jobs/${id}/cancel`, { method: 'POST' }),
}

export const schemas = {
  list: (templatesOnly = false) =>
    request<{ schemas: Schema[]; count: number }>(`/schemas?templates_only=${templatesOnly}`),
  get: (id: string) => request<Schema>(`/schemas/${id}`),
  create: (data: { name: string; description?: string; fields?: SchemaField[]; is_template?: boolean }) =>
    request<Schema>('/schemas', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: string, data: Partial<Schema>) =>
    request<Schema>(`/schemas/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id: string) => request<{ message: string }>(`/schemas/${id}`, { method: 'DELETE' }),
}

export const extraction = {
  start: (jobId: string) =>
    request<{ job_id: string; result_id: string; rows_extracted: number; status: string }>(
      `/extraction/${jobId}/start`,
      { method: 'POST' },
    ),
  preview: (jobId: string, data: { extraction_config: ExtractionConfig; schema_fields?: SchemaField[]; limit?: number }) =>
    request<{ preview: unknown[]; total_matched: number; limit: number }>(`/extraction/${jobId}/preview`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  validateSelector: (jobId: string, selector: string, limit = 10) =>
    request<ValidateSelectorResponse>(`/extraction/${jobId}/validate-selector`, {
      method: 'POST',
      body: JSON.stringify({ selector, limit }),
    }),
  results: (jobId: string, params?: { limit?: number; offset?: number; sort_by?: string; sort_dir?: string }) => {
    const qs = new URLSearchParams()
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.offset) qs.set('offset', String(params.offset))
    if (params?.sort_by) qs.set('sort_by', params.sort_by)
    if (params?.sort_dir) qs.set('sort_dir', params.sort_dir)
    const q = qs.toString()
    return request<{ results: unknown[]; count: number }>(`/extraction/${jobId}/results${q ? `?${q}` : ''}`)
  },
  exportUrl: (jobId: string, format: 'json' | 'csv', normalize = false) =>
    `${BASE}/extraction/${jobId}/results/export/${format}${normalize ? '?normalize=true' : ''}`,
}

export const pages = {
  list: (jobId: string, limit = 200, offset = 0) =>
    request<{ pages: ScrapePage[]; count: number }>(`/pages/${jobId}?limit=${limit}&offset=${offset}`),
  resources: (jobId: string, category?: string) => {
    const qs = category ? `?category=${category}` : ''
    return request<{ resources: ScrapeResource[]; count: number }>(`/pages/${jobId}/resources${qs}`)
  },
  viewUrl: (jobId: string, pageId: string) => `${BASE}/pages/${jobId}/view/${pageId}`,
  assetUrl: (jobId: string, resourcePath: string) => `${BASE}/pages/${jobId}/assets/${resourcePath}`,
  tree: (jobId: string) => request<{ tree: Record<string, PageTreeNode>; count: number }>(`/pages/${jobId}/tree`),
}

export const system = {
  health: () => request<{ status: string; services: Record<string, string>; version: string }>('/health'),
}

// ── Types ────────────────────────────────────────────────────────────────────

export type JobStatus = 'created' | 'scraping' | 'scraped' | 'extracting' | 'completed' | 'failed' | 'paused' | 'cancelled'

export type FailedStage = 'scrape' | 'extract'
export type ExtractionMode = 'document' | 'file'
export type CrawlMode = 'http' | 'browser'
export type AuthMethod = 'none' | 'basic' | 'bearer' | 'cookie' | 'browser_session'
export type SchemaFieldType = 'string' | 'number' | 'image'

export interface DomainFilter {
  allowed_domains: string[]
  path_filters: string[]
}

export interface AuthConfig {
  method: AuthMethod
  username?: string
  password?: string
  token?: string
  cookies?: Record<string, string>
  session_data?: Record<string, unknown>
}

export interface ResourceFilterConfig {
  label: string
  extensions: string[]
  enabled: boolean
  mode: 'include' | 'exclude'
  exclude_extensions: string[]
}

export interface DedupConfig {
  enabled: boolean
}

export interface ScrapeConfig {
  seed_urls: string[]
  crawl_mode: CrawlMode
  depth_limit: number
  domain_filter: DomainFilter
  resource_filters: Record<string, ResourceFilterConfig>
  dedup: DedupConfig
  respect_robots: boolean
  request_delay_ms: number
  max_concurrent_per_domain: number
  max_concurrent_total: number
  max_download_size?: number
  auth: AuthConfig
  user_agent?: string
  retry_limit?: number
}

export interface SchemaField {
  name: string
  field_type: SchemaFieldType
  is_array: boolean
  children?: SchemaField[] | null
}

export interface Schema {
  id: string
  name: string
  description: string
  fields: SchemaField[]
  is_template: boolean
  created_at: string
  updated_at: string
}

export interface FieldMapping {
  field_path: string
  selector?: string | null
  attribute?: string | null
  url_regex?: string | null
}

export interface BoundaryMapping {
  field_path: string
  boundary?: string | null
  iterator?: string | null
}

export interface DocumentExtractionConfig {
  root_boundary?: string | null
  url_pattern?: string | null
  boundaries: BoundaryMapping[]
  field_mappings: FieldMapping[]
  merge_by?: string | null
  merge_strategy?: string
}

export interface FilePattern {
  schema_key: string
  regex_pattern: string
}

export interface ExtractionConfig {
  mode: ExtractionMode
  schema_id?: string
  document?: DocumentExtractionConfig | null
  file_patterns: FilePattern[]
}

export interface ValidateSelectorResponse {
  selector: string
  match_count: number
  pages_checked: number
  sample_matches: Array<{ page_url: string; text: string; tag: string; classes: string[] }>
}

export interface Job {
  id: string
  name?: string | null
  status: JobStatus
  extraction_mode?: ExtractionMode
  seed_urls: string[]
  pages_downloaded: number
  resources_downloaded: number
  created_at: string
  completed_at?: string
  duration_seconds?: number
}

export interface JobDetail {
  id: string
  name?: string | null
  status: JobStatus
  scrape_config: ScrapeConfig
  extraction_config?: ExtractionConfig
  extraction_mode?: ExtractionMode
  progress: number
  progress_message: string
  pages_discovered: number
  pages_downloaded: number
  pages_errored: number
  resources_discovered: number
  resources_downloaded: number
  resources_errored: number
  bytes_downloaded: number
  error_message?: string
  failed_stage?: FailedStage | null
  created_at: string
  started_at?: string
  scraped_at?: string
  completed_at?: string
}

export interface ScrapePage {
  id: string
  job_id: string
  url: string
  local_path: string
  status: string
  content_type?: string
  size: number
  depth: number
  parent_url?: string
  title?: string
}

export interface ScrapeResource {
  id: string
  job_id: string
  url: string
  local_path: string
  filename: string
  category: string
  size: number
  mime_type: string
  content_hash: string
}

export interface PageTreeNode {
  id: string
  url: string
  depth: number
  parent_url?: string
  status: string
  title?: string
  size: number
  content_type?: string
}

export interface WSEvent {
  type: string
  job_id: string
  data: Record<string, unknown>
  timestamp?: string
}
