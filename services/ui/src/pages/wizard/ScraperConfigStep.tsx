import { useState } from 'react'
import type { ScrapeConfig, CrawlMode, AuthMethod, ResourceFilterConfig } from '@/api/client'

const DEFAULT_RESOURCE_FILTERS: Record<string, ResourceFilterConfig> = {
  web_pages: { label: 'Web Pages', extensions: ['html', 'htm', 'php', 'asp', 'aspx', 'jsp'], enabled: true, mode: 'include', exclude_extensions: [] },
  images: { label: 'Images', extensions: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico', 'bmp', 'tiff'], enabled: false, mode: 'include', exclude_extensions: [] },
  media: { label: 'Media', extensions: ['mp4', 'm4v', 'avi', 'mov', 'wmv', 'webm', 'mkv', 'mp3', 'm4a', 'wav', 'ogg', 'oga', 'ogv', 'flac', 'aac'], enabled: false, mode: 'include', exclude_extensions: [] },
  documents: { label: 'Documents', extensions: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'rtf'], enabled: false, mode: 'include', exclude_extensions: [] },
  archives: { label: 'Archives', extensions: ['zip', 'tar', 'gz', 'rar', '7z'], enabled: false, mode: 'include', exclude_extensions: [] },
  code: { label: 'Code', extensions: ['json', 'xml', 'yaml', 'css', 'js'], enabled: false, mode: 'include', exclude_extensions: [] },
}

interface ResourceFilterRowProps {
  filterKey: string
  filter: ResourceFilterConfig
  onToggle: () => void
  onUpdate: (next: ResourceFilterConfig) => void
  onRename: (newKey: string) => void
  onRemove: () => void
}

function ResourceFilterRow({ filterKey, filter, onToggle, onUpdate, onRename, onRemove }: ResourceFilterRowProps) {
  const [expanded, setExpanded] = useState(false)
  const extString = filter.extensions.join(', ')
  return (
    <div
      className={`rounded-lg border transition-all ${
        filter.enabled ? 'border-primary/30 bg-primary/5' : 'border-base-content/10'
      }`}
    >
      <div className="flex items-center gap-3 p-3">
        <input
          type="checkbox"
          className="checkbox checkbox-primary checkbox-sm shrink-0"
          checked={filter.enabled}
          onChange={onToggle}
        />
        <div className="flex-1 min-w-0 grid grid-cols-1 sm:grid-cols-2 gap-2">
          <input
            type="text"
            className="input input-bordered input-xs"
            value={filter.label}
            onChange={(e) => onUpdate({ ...filter, label: e.target.value })}
            placeholder="Label"
          />
          <input
            type="text"
            className="input input-bordered input-xs font-mono"
            value={filterKey}
            onChange={(e) => onRename(e.target.value.replace(/[^a-z0-9_]/gi, '_').toLowerCase())}
            placeholder="key"
          />
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-xs btn-square"
          onClick={() => setExpanded(!expanded)}
          title={expanded ? 'Collapse' : 'Edit extensions'}
        >
          <span className={`icon-[tabler--chevron-${expanded ? 'up' : 'down'}] size-4`} />
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-xs btn-square text-error"
          onClick={onRemove}
          title="Remove category"
        >
          <span className="icon-[tabler--trash] size-4" />
        </button>
      </div>
      {expanded ? (
        <div className="px-3 pb-3 space-y-2 border-t border-base-content/5 pt-2">
          <div className="form-control">
            <label className="label py-0.5">
              <span className="label-text text-[10px] text-base-content/40">Extensions (comma-separated)</span>
            </label>
            <input
              type="text"
              className="input input-bordered input-sm font-mono"
              value={extString}
              onChange={(e) => {
                const exts = e.target.value
                  .split(',')
                  .map((s) => s.trim().toLowerCase().replace(/^\./, ''))
                  .filter(Boolean)
                onUpdate({ ...filter, extensions: exts })
              }}
              placeholder="jpg, png, gif"
            />
          </div>
          <div className="form-control">
            <label className="label py-0.5">
              <span className="label-text text-[10px] text-base-content/40">Exclude extensions (priority)</span>
            </label>
            <input
              type="text"
              className="input input-bordered input-sm font-mono"
              value={filter.exclude_extensions.join(', ')}
              onChange={(e) => {
                const exts = e.target.value
                  .split(',')
                  .map((s) => s.trim().toLowerCase().replace(/^\./, ''))
                  .filter(Boolean)
                onUpdate({ ...filter, exclude_extensions: exts })
              }}
              placeholder="(none)"
            />
          </div>
        </div>
      ) : (
        <p className="px-3 pb-2 -mt-1 text-xs text-base-content/40 truncate">
          {extString || '(no extensions)'}
        </p>
      )}
    </div>
  )
}

interface ScraperConfigStepProps {
  onSubmit: (config: ScrapeConfig, name?: string) => void
  initialConfig?: Partial<ScrapeConfig>
  initialName?: string
  isLoading?: boolean
  readOnly?: boolean
}

export default function ScraperConfigStep({ onSubmit, initialConfig, initialName, isLoading, readOnly = false }: ScraperConfigStepProps) {
  const [name, setName] = useState(initialName ?? '')
  const [seedUrls, setSeedUrls] = useState(initialConfig?.seed_urls?.join('\n') ?? '')
  const [crawlMode, setCrawlMode] = useState<CrawlMode>(initialConfig?.crawl_mode ?? 'http')
  const [depthLimit, setDepthLimit] = useState(initialConfig?.depth_limit ?? 3)
  const [allowedDomains, setAllowedDomains] = useState(initialConfig?.domain_filter?.allowed_domains?.join('\n') ?? '')
  const [pathFilters, setPathFilters] = useState(initialConfig?.domain_filter?.path_filters?.join('\n') ?? '')
  const [respectRobots, setRespectRobots] = useState(initialConfig?.respect_robots ?? true)
  const [requestDelay, setRequestDelay] = useState(initialConfig?.request_delay_ms ?? 500)
  const [maxPerDomain, setMaxPerDomain] = useState(initialConfig?.max_concurrent_per_domain ?? 2)
  const [maxTotal, setMaxTotal] = useState(initialConfig?.max_concurrent_total ?? 10)
  const [maxDownloadSize, setMaxDownloadSize] = useState<string>(
    initialConfig?.max_download_size ? String(initialConfig.max_download_size / 1048576) : ''
  )
  const [authMethod, setAuthMethod] = useState<AuthMethod>(initialConfig?.auth?.method ?? 'none')
  const [authUsername, setAuthUsername] = useState(initialConfig?.auth?.username ?? '')
  const [authPassword, setAuthPassword] = useState(initialConfig?.auth?.password ?? '')
  const [authToken, setAuthToken] = useState(initialConfig?.auth?.token ?? '')
  const [authCookies, setAuthCookies] = useState(
    initialConfig?.auth?.cookies ? Object.entries(initialConfig.auth.cookies).map(([k, v]) => `${k}=${v}`).join('\n') : ''
  )
  const [resourceFilters, setResourceFilters] = useState<Record<string, ResourceFilterConfig>>(
    initialConfig?.resource_filters ?? { ...DEFAULT_RESOURCE_FILTERS }
  )

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const urls = seedUrls.split('\n').map((u) => u.trim()).filter(Boolean)
    if (urls.length === 0) return

    const config: ScrapeConfig = {
      seed_urls: urls,
      crawl_mode: crawlMode,
      depth_limit: depthLimit,
      domain_filter: {
        allowed_domains: allowedDomains.split('\n').map((d) => d.trim()).filter(Boolean),
        path_filters: pathFilters.split('\n').map((p) => p.trim()).filter(Boolean),
      },
      resource_filters: resourceFilters,
      respect_robots: respectRobots,
      request_delay_ms: requestDelay,
      max_concurrent_per_domain: maxPerDomain,
      max_concurrent_total: maxTotal,
      max_download_size: maxDownloadSize ? parseInt(maxDownloadSize) * 1048576 : undefined,
      auth: {
        method: authMethod,
        username: authMethod === 'basic' ? authUsername : undefined,
        password: authMethod === 'basic' ? authPassword : undefined,
        token: authMethod === 'bearer' ? authToken : undefined,
        cookies: authMethod === 'cookie' ? Object.fromEntries(
          authCookies.split('\n').filter(Boolean).map((line) => {
            const [k, ...rest] = line.split('=')
            return [k.trim(), rest.join('=').trim()]
          })
        ) : undefined,
      },
    }
    onSubmit(config, name.trim() || undefined)
  }

  const toggleFilter = (key: string) => {
    setResourceFilters((prev) => ({
      ...prev,
      [key]: { ...prev[key], enabled: !prev[key].enabled },
    }))
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      {readOnly && (
        <div className="flex items-start gap-3 p-3 rounded-lg bg-info/10 border border-info/20">
          <span className="icon-[tabler--lock] size-5 text-info shrink-0 mt-0.5" />
          <div className="text-sm text-base-content/70">
            <p className="font-medium">Scrape configuration is read-only</p>
            <p className="text-xs mt-0.5 text-base-content/50">
              This job has already been scraped. Create a new job to use different scrape settings.
            </p>
          </div>
        </div>
      )}
      <fieldset disabled={readOnly} className={readOnly ? 'opacity-90' : ''}>
        <div className="space-y-8">
      {/* Seed URLs */}
      {/* Job Name */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Job Name
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4">
          <div className="form-control">
            <input
              type="text"
              className="input input-bordered"
              placeholder="My extraction job (optional)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <label className="label">
              <span className="label-text-alt text-base-content/40">
                Give this job a memorable name. Falls back to the job ID if blank.
              </span>
            </label>
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Target URLs
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 space-y-3">
          <div className="form-control">
            <label className="label">
              <span className="label-text text-xs">Seed URLs</span>
            </label>
            <textarea
              className="textarea textarea-bordered h-24 font-mono text-sm"
              placeholder={"https://example.com\nhttps://example.com/docs"}
              value={seedUrls}
              onChange={(e) => setSeedUrls(e.target.value)}
              required
            />
            <label className="label">
              <span className="label-text-alt text-base-content/40">One URL per line</span>
            </label>
          </div>
        </div>
      </section>

      {/* Crawl Mode */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Crawl Mode
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4">
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              className={`flex items-center gap-3 p-4 rounded-lg border-2 transition-all ${
                crawlMode === 'http'
                  ? 'border-primary bg-primary/5'
                  : 'border-base-content/10 hover:border-base-content/20'
              }`}
              onClick={() => setCrawlMode('http')}
            >
              <span className="icon-[tabler--world-www] size-6 text-primary" />
              <div className="text-start">
                <p className="text-sm font-medium">HTTP</p>
                <p className="text-xs text-base-content/50">Fast, static sites</p>
              </div>
            </button>
            <button
              type="button"
              className={`flex items-center gap-3 p-4 rounded-lg border-2 transition-all ${
                crawlMode === 'browser'
                  ? 'border-primary bg-primary/5'
                  : 'border-base-content/10 hover:border-base-content/20'
              }`}
              onClick={() => setCrawlMode('browser')}
            >
              <span className="icon-[tabler--browser] size-6 text-primary" />
              <div className="text-start">
                <p className="text-sm font-medium">Browser</p>
                <p className="text-xs text-base-content/50">JS rendering, SPAs</p>
              </div>
            </button>
          </div>
        </div>
      </section>

      {/* Crawl Settings */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Crawl Settings
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Depth Limit (hops; 0 = seed URLs only)</span></label>
              <input type="number" className="input input-bordered input-sm" min={0} max={20} value={depthLimit} onChange={(e) => { const v = parseInt(e.target.value); setDepthLimit(isNaN(v) ? 3 : v) }} />
            </div>
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Request Delay (ms)</span></label>
              <input type="number" className="input input-bordered input-sm" min={0} max={10000} value={requestDelay} onChange={(e) => setRequestDelay(parseInt(e.target.value) || 500)} />
            </div>
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Max Download (MB)</span></label>
              <input type="number" className="input input-bordered input-sm" min={1} placeholder="500" value={maxDownloadSize} onChange={(e) => setMaxDownloadSize(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Max Concurrent / Domain</span></label>
              <input type="number" className="input input-bordered input-sm" min={1} max={20} value={maxPerDomain} onChange={(e) => setMaxPerDomain(parseInt(e.target.value) || 2)} />
            </div>
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Max Concurrent Total</span></label>
              <input type="number" className="input input-bordered input-sm" min={1} max={50} value={maxTotal} onChange={(e) => setMaxTotal(parseInt(e.target.value) || 10)} />
            </div>
          </div>
          <label className="flex items-center gap-3 cursor-pointer pt-2">
            <input type="checkbox" className="toggle toggle-primary toggle-sm" checked={respectRobots} onChange={(e) => setRespectRobots(e.target.checked)} />
            <span className="label-text text-sm">Respect robots.txt</span>
          </label>
        </div>
      </section>

      {/* Domain Filters */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Domain & Path Filters
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 space-y-4">
          <div className="form-control">
            <label className="label"><span className="label-text text-xs">Allowed Domains</span></label>
            <textarea
              className="textarea textarea-bordered textarea-sm h-20 font-mono text-sm"
              placeholder={"Auto-populated from seed URLs\n*.example.com"}
              value={allowedDomains}
              onChange={(e) => setAllowedDomains(e.target.value)}
            />
            <label className="label">
              <span className="label-text-alt text-base-content/40">
                One per line. Supports wildcards. Leave empty to auto-detect.
              </span>
            </label>
          </div>
          <div className="form-control">
            <label className="label"><span className="label-text text-xs">Path Filters</span></label>
            <textarea
              className="textarea textarea-bordered textarea-sm h-16 font-mono text-sm"
              placeholder={"/docs/*\n/blog/*"}
              value={pathFilters}
              onChange={(e) => setPathFilters(e.target.value)}
            />
            <label className="label">
              <span className="label-text-alt text-base-content/40">Optional. Only crawl matching paths.</span>
            </label>
          </div>
        </div>
      </section>

      {/* Resource Filters */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Resource Filters
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 space-y-2">
          {Object.entries(resourceFilters).map(([key, filter]) => (
            <ResourceFilterRow
              key={key}
              filterKey={key}
              filter={filter}
              onToggle={() => toggleFilter(key)}
              onUpdate={(updated) => setResourceFilters((prev) => ({ ...prev, [key]: updated }))}
              onRename={(newKey) => {
                if (!newKey || newKey === key || resourceFilters[newKey]) return
                setResourceFilters((prev) => {
                  const next: Record<string, ResourceFilterConfig> = {}
                  for (const [k, v] of Object.entries(prev)) {
                    next[k === key ? newKey : k] = v
                  }
                  return next
                })
              }}
              onRemove={() => {
                setResourceFilters((prev) => {
                  const next = { ...prev }
                  delete next[key]
                  return next
                })
              }}
            />
          ))}
          <button
            type="button"
            className="btn btn-ghost btn-sm gap-1.5 mt-1"
            onClick={() => {
              const baseKey = 'category'
              let i = 1
              while (resourceFilters[`${baseKey}_${i}`]) i++
              const newKey = `${baseKey}_${i}`
              setResourceFilters((prev) => ({
                ...prev,
                [newKey]: {
                  label: 'New Category',
                  extensions: [],
                  enabled: false,
                  mode: 'include',
                  exclude_extensions: [],
                },
              }))
            }}
          >
            <span className="icon-[tabler--plus] size-4" />
            Add Category
          </button>
        </div>
      </section>

      {/* Authentication */}
      <section className="space-y-4">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Authentication
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 space-y-4">
          <div className="form-control">
            <label className="label"><span className="label-text text-xs">Method</span></label>
            <select className="select select-bordered select-sm w-full max-w-xs" value={authMethod} onChange={(e) => setAuthMethod(e.target.value as AuthMethod)}>
              <option value="none">None</option>
              <option value="basic">Basic Auth</option>
              <option value="bearer">Bearer Token</option>
              <option value="cookie">Cookies</option>
              {crawlMode === 'browser' && <option value="browser_session">Browser Login</option>}
            </select>
          </div>

          {authMethod === 'basic' && (
            <div className="grid grid-cols-2 gap-3">
              <div className="form-control">
                <label className="label"><span className="label-text text-xs">Username</span></label>
                <input type="text" className="input input-bordered input-sm" placeholder="Username" value={authUsername} onChange={(e) => setAuthUsername(e.target.value)} />
              </div>
              <div className="form-control">
                <label className="label"><span className="label-text text-xs">Password</span></label>
                <input type="password" className="input input-bordered input-sm" placeholder="Password" value={authPassword} onChange={(e) => setAuthPassword(e.target.value)} />
              </div>
            </div>
          )}
          {authMethod === 'bearer' && (
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Token</span></label>
              <input type="text" className="input input-bordered input-sm font-mono" placeholder="Bearer token" value={authToken} onChange={(e) => setAuthToken(e.target.value)} />
            </div>
          )}
          {authMethod === 'cookie' && (
            <div className="form-control">
              <label className="label"><span className="label-text text-xs">Cookies</span></label>
              <textarea className="textarea textarea-bordered textarea-sm h-20 font-mono text-sm" placeholder={"name=value\nsession_id=abc123"} value={authCookies} onChange={(e) => setAuthCookies(e.target.value)} />
            </div>
          )}
          {authMethod === 'browser_session' && (
            <div className="flex items-start gap-3 p-3 rounded-lg bg-warning/10 border border-warning/20">
              <span className="icon-[tabler--alert-triangle] size-5 text-warning shrink-0 mt-0.5" />
              <div className="text-sm text-base-content/70 space-y-1">
                <p className="font-medium">Interactive browser login is not yet available.</p>
                <p>
                  Instead, log in to the target site in your browser, export your cookies
                  (e.g. via DevTools or a cookie export extension), and use the <strong>Cookies</strong> auth method
                  to paste them as <code>name=value</code> pairs.
                </p>
              </div>
            </div>
          )}
        </div>
      </section>

        </div>
      </fieldset>

      {/* Submit */}
      {!readOnly && (
        <div className="flex justify-end pt-2">
          <button type="submit" className="btn btn-primary gap-2" disabled={isLoading}>
            {isLoading ? (
              <span className="icon-[tabler--loader-2] size-5 animate-spin" />
            ) : (
              <span className="icon-[tabler--rocket] size-5" />
            )}
            Create Job & Start Scraping
          </button>
        </div>
      )}
    </form>
  )
}
