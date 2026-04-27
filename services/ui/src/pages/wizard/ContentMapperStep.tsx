import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  pages as pagesApi,
  schemas as schemasApi,
  jobs as jobsApi,
  type ScrapePage,
  type ExtractionMode,
  type ExtractionConfig,
  type FieldMapping,
  type BoundaryMapping,
  type FilePattern,
  type SchemaField,
} from '@/api/client'
import { useJobStore } from '@/stores/jobStore'
import { useSchemaStore } from '@/stores/schemaStore'

interface ContentMapperStepProps {
  onContinue: () => void
}

export default function ContentMapperStep({ onContinue }: ContentMapperStepProps) {
  const { activeJob, updateActiveJob } = useJobStore()
  const { fields, schemaId, setSchemaId, schemaName, setFields, setSchemaName } = useSchemaStore()

  // Hydrate the schema fields (which live in the schemaStore) from the
  // schema_id persisted on the job. Without this, refreshing the mapper
  // shows zero fields because the in-memory schemaStore was wiped on
  // reload — only the schema_id survived (on the job).
  const persistedSchemaId = activeJob?.extraction_config?.schema_id
  const { data: hydratedSchema } = useQuery({
    queryKey: ['schema', persistedSchemaId],
    queryFn: () => schemasApi.get(persistedSchemaId!),
    enabled: !!persistedSchemaId,
    staleTime: 30000,
  })
  useEffect(() => {
    if (!hydratedSchema) return
    // Only repopulate if the store doesn't already have this schema's
    // fields — avoids clobbering in-progress edits the user made in
    // the Schema Builder before navigating here.
    if (schemaId === hydratedSchema.id && fields.length > 0) return
    setFields(hydratedSchema.fields)
    setSchemaName(hydratedSchema.name)
    setSchemaId(hydratedSchema.id)
  }, [hydratedSchema, schemaId, fields.length, setFields, setSchemaName, setSchemaId])

  // Initialize state from saved extraction_config (if any)
  const savedConfig = activeJob?.extraction_config
  const savedDoc = savedConfig?.document
  const [mode, setMode] = useState<ExtractionMode>(savedConfig?.mode ?? 'document')

  // Document mode state — hydrate from saved config
  const [rootBoundary, setRootBoundary] = useState(savedDoc?.root_boundary ?? '')
  const [urlPattern, setUrlPattern] = useState(savedDoc?.url_pattern ?? '')
  const [mergeBy, setMergeBy] = useState(savedDoc?.merge_by ?? '')
  const [boundaries, setBoundaries] = useState<BoundaryMapping[]>(savedDoc?.boundaries ?? [])
  const [fieldMappings, setFieldMappings] = useState<FieldMapping[]>(savedDoc?.field_mappings ?? [])
  const [filePatterns, setFilePatterns] = useState<FilePattern[]>(savedConfig?.file_patterns ?? [])

  // Re-hydrate when activeJob changes (e.g., navigating from another job)
  const hydratedJobIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!activeJob) return
    if (hydratedJobIdRef.current === activeJob.id) return
    hydratedJobIdRef.current = activeJob.id
    const cfg = activeJob.extraction_config
    if (!cfg) return
    setMode(cfg.mode ?? 'document')
    setRootBoundary(cfg.document?.root_boundary ?? '')
    setUrlPattern(cfg.document?.url_pattern ?? '')
    setMergeBy(cfg.document?.merge_by ?? '')
    setBoundaries(cfg.document?.boundaries ?? [])
    setFieldMappings(cfg.document?.field_mappings ?? [])
    setFilePatterns(cfg.file_patterns ?? [])
    if (cfg.schema_id && cfg.schema_id !== schemaId) {
      setSchemaId(cfg.schema_id)
    }
  }, [activeJob, schemaId, setSchemaId])

  // Page preview
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  // Picker state
  const [pickerActive, setPickerActive] = useState(false)
  const [pickerTarget, setPickerTarget] = useState<string | null>(null) // field_path being picked
  const [pickerMode, setPickerMode] = useState<'field' | 'boundary' | 'iterator'>('field')
  const [matchCount, setMatchCount] = useState<number | null>(null)

  const jobId = activeJob?.id ?? ''

  const { data: pageData } = useQuery({
    queryKey: ['pages', jobId],
    queryFn: () => pagesApi.list(jobId),
    enabled: !!jobId && mode === 'document',
    staleTime: 30000,
  })
  const pageList = pageData?.pages ?? []

  // Flatten schema to dot-notation paths with metadata
  function flattenSchema(flds: SchemaField[], prefix = ''): Array<{ path: string; field: SchemaField }> {
    const result: Array<{ path: string; field: SchemaField }> = []
    for (const f of flds) {
      const path = prefix ? `${prefix}.${f.name}` : f.name
      result.push({ path, field: f })
      if (f.children) {
        result.push(...flattenSchema(f.children, path))
      }
    }
    return result
  }
  const flatFields = flattenSchema(fields)
  const leafFields = flatFields.filter(({ field }) => !field.children)
  const structFields = flatFields.filter(({ field }) => !!field.children)

  // Listen for postMessage from iframe
  useEffect(() => {
    function handleMessage(e: MessageEvent) {
      const msg = e.data
      if (!msg?.type) return

      if (msg.type === 'ELEMENT_SELECTED' && pickerTarget) {
        const selector = msg.selector as string
        // Special case: root boundary picker
        if (pickerTarget === '__root') {
          setRootBoundary(selector)
          disablePicker()
          highlightSelector(selector)
          return
        }
        if (pickerMode === 'field') {
          setFieldMappings((prev) => {
            const existing = prev.findIndex((m) => m.field_path === pickerTarget)
            if (existing >= 0) {
              const next = [...prev]
              next[existing] = { ...next[existing], selector }
              return next
            }
            return [...prev, { field_path: pickerTarget, selector, attribute: null }]
          })
        } else if (pickerMode === 'boundary') {
          setBoundaries((prev) => {
            const existing = prev.findIndex((b) => b.field_path === pickerTarget)
            if (existing >= 0) {
              const next = [...prev]
              next[existing] = { ...next[existing], boundary: selector }
              return next
            }
            return [...prev, { field_path: pickerTarget, boundary: selector, iterator: null }]
          })
        } else if (pickerMode === 'iterator') {
          setBoundaries((prev) => {
            const existing = prev.findIndex((b) => b.field_path === pickerTarget)
            if (existing >= 0) {
              const next = [...prev]
              next[existing] = { ...next[existing], iterator: selector }
              return next
            }
            return [...prev, { field_path: pickerTarget, boundary: null, iterator: selector }]
          })
        }
        // Disable picker after selection
        disablePicker()
        // Highlight matches
        highlightSelector(selector)
      }

      if (msg.type === 'HIGHLIGHT_RESULT') {
        setMatchCount(msg.matchCount as number)
      }

      if (msg.type === 'NAVIGATE_REQUEST') {
        // Find the page by URL and switch to it
        const url = msg.url as string
        const targetPage = pageList.find((p) => p.url === url)
        if (targetPage) {
          setSelectedPageId(targetPage.id)
        }
      }
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [pickerTarget, pickerMode, pageList])

  const enablePicker = useCallback((fieldPath: string, mode: 'field' | 'boundary' | 'iterator') => {
    setPickerTarget(fieldPath)
    setPickerMode(mode)
    setPickerActive(true)
    iframeRef.current?.contentWindow?.postMessage({ type: 'PICKER_ENABLE' }, '*')
  }, [])

  const disablePicker = useCallback(() => {
    setPickerActive(false)
    setPickerTarget(null)
    iframeRef.current?.contentWindow?.postMessage({ type: 'PICKER_DISABLE' }, '*')
  }, [])

  const highlightSelector = useCallback((selector: string) => {
    iframeRef.current?.contentWindow?.postMessage({ type: 'HIGHLIGHT_SELECTOR', selector }, '*')
  }, [])

  const clearHighlights = useCallback(() => {
    iframeRef.current?.contentWindow?.postMessage({ type: 'CLEAR_HIGHLIGHTS' }, '*')
    setMatchCount(null)
  }, [])

  // Get mapping for a field path
  const getMapping = (path: string) => fieldMappings.find((m) => m.field_path === path)
  const getBoundary = (path: string) => boundaries.find((b) => b.field_path === path)

  const updateMapping = (path: string, updates: Partial<FieldMapping>) => {
    setFieldMappings((prev) => {
      const idx = prev.findIndex((m) => m.field_path === path)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = { ...next[idx], ...updates }
        return next
      }
      return [...prev, { field_path: path, selector: null, attribute: null, ...updates }]
    })
  }

  const updateBoundary = (path: string, updates: Partial<BoundaryMapping>) => {
    setBoundaries((prev) => {
      const idx = prev.findIndex((b) => b.field_path === path)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = { ...next[idx], ...updates }
        return next
      }
      return [...prev, { field_path: path, boundary: null, iterator: null, ...updates }]
    })
  }

  // Build extraction config and save to job
  const handleRunExtraction = async () => {
    // Auto-save schema if not already persisted
    let resolvedSchemaId = schemaId
    if (!resolvedSchemaId && fields.length > 0) {
      const name = schemaName.trim() || `Schema for ${activeJob?.scrape_config.seed_urls[0] ?? 'job'}`
      const created = await schemasApi.create({ name, fields })
      resolvedSchemaId = created.id
      setSchemaId(created.id)
    } else if (resolvedSchemaId) {
      // Update existing schema with latest fields
      await schemasApi.update(resolvedSchemaId, { fields })
    }

    const config: ExtractionConfig = {
      mode,
      schema_id: resolvedSchemaId ?? undefined,
      document: mode === 'document' ? {
        root_boundary: rootBoundary || null,
        url_pattern: urlPattern || null,
        merge_by: mergeBy || null,
        boundaries,
        field_mappings: fieldMappings.filter((m) => m.selector || m.url_regex),
      } : null,
      file_patterns: mode === 'file' ? filePatterns : [],
    }

    // Save extraction config to the job. Do NOT change `status` — saving
    // mapper config is a UI activity, not a pipeline transition. The job
    // remains `scraped` until extraction actually starts.
    await jobsApi.update(jobId, {
      extraction_config: config,
      extraction_mode: mode,
    })
    updateActiveJob({ extraction_config: config })
    onContinue()
  }

  const hasValidMappings = mode === 'document'
    ? fieldMappings.some((m) => m.selector)
    : filePatterns.some((p) => p.schema_key && p.regex_pattern)

  return (
    <div className="space-y-6">
      {/* Mode selector */}
      <div className="flex items-center gap-1 bg-base-200/50 rounded-lg p-1 w-fit">
        <button
          type="button"
          className={`btn btn-sm gap-1.5 ${mode === 'document' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setMode('document')}
        >
          <span className="icon-[tabler--file-text] size-4" />
          Document
        </button>
        <button
          type="button"
          className={`btn btn-sm gap-1.5 ${mode === 'file' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setMode('file')}
        >
          <span className="icon-[tabler--files] size-4" />
          File
        </button>
      </div>

      {mode === 'document' ? (
        <>
          {/* Root boundary + URL pattern */}
          <div className="bg-base-200/50 rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
              Extraction Scope
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="form-control">
                <label className="label py-0.5">
                  <span className="label-text text-xs">Root Boundary</span>
                </label>
                <div className="flex gap-1">
                  <input
                    type="text"
                    className="input input-bordered input-sm flex-1 font-mono text-xs"
                    placeholder="div.product (empty = one record per page)"
                    value={rootBoundary}
                    onChange={(e) => setRootBoundary(e.target.value)}
                  />
                  {selectedPageId && (
                    <button
                      className={`btn btn-sm btn-square ${pickerActive && pickerTarget === '__root' ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => pickerActive ? disablePicker() : enablePicker('__root', 'field')}
                      title="Pick element"
                    >
                      <span className="icon-[tabler--pointer] size-4" />
                    </button>
                  )}
                </div>
              </div>
              <div className="form-control">
                <label className="label py-0.5">
                  <span className="label-text text-xs">URL Pattern</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered input-sm font-mono text-xs"
                  placeholder="example.com/products/* (optional)"
                  value={urlPattern}
                  onChange={(e) => setUrlPattern(e.target.value)}
                />
              </div>
            </div>
            <div className="form-control mt-2">
              <label className="label py-0.5">
                <span className="label-text text-xs">Merge By <span className="text-base-content/40">(optional — collapse pages into one record per shared key)</span></span>
              </label>
              <select
                className="select select-bordered select-sm font-mono text-xs"
                value={mergeBy}
                onChange={(e) => setMergeBy(e.target.value)}
              >
                <option value="">No merge — one record per page</option>
                {leafFields.map(({ path }) => (
                  <option key={path} value={path}>{path}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Page preview with picker */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
                  Page Preview
                </h3>
                {pickerActive && (
                  <span className="badge badge-soft badge-sm badge-info gap-1">
                    <span className="icon-[tabler--pointer] size-3" />
                    Click an element
                  </span>
                )}
                {matchCount !== null && (
                  <span className="badge badge-soft badge-sm badge-success">
                    {matchCount} match{matchCount !== 1 ? 'es' : ''}
                  </span>
                )}
              </div>
              <select
                className="select select-bordered select-sm w-full"
                value={selectedPageId ?? ''}
                onChange={(e) => { setSelectedPageId(e.target.value || null); clearHighlights() }}
              >
                <option value="">Select a page...</option>
                {pageList.map((page: ScrapePage) => (
                  <option key={page.id} value={page.id}>
                    {page.title || page.url}
                  </option>
                ))}
              </select>
              {selectedPageId ? (
                <div className="border border-base-content/10 rounded-xl overflow-hidden">
                  <iframe
                    ref={iframeRef}
                    src={pagesApi.viewUrl(jobId, selectedPageId)}
                    className="w-full h-[28rem] bg-white"
                    sandbox="allow-same-origin allow-scripts"
                    title="Page preview"
                  />
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-base-content/30 border border-dashed border-base-content/10 rounded-xl">
                  <span className="icon-[tabler--browser] size-10" />
                  <p className="text-sm">Select a page to preview</p>
                </div>
              )}
            </div>

            {/* Mapping panel */}
            <div className="space-y-4">
              {/* Boundaries for records/collections */}
              {structFields.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
                    Boundaries
                  </h3>
                  {structFields.map(({ path, field }) => {
                    const bm = getBoundary(path)
                    return (
                      <div key={path} className="bg-base-200/50 rounded-xl p-3 space-y-2">
                        <div className="flex items-center gap-2">
                          <span className={`badge badge-soft badge-xs ${field.is_array ? 'badge-accent' : 'badge-info'}`}>
                            {field.is_array ? '[]' : '{}'}
                          </span>
                          <span className="text-sm font-medium font-mono">{path}</span>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          <div className="form-control">
                            <label className="label py-0"><span className="label-text text-[10px] text-base-content/40">Boundary</span></label>
                            <div className="flex gap-1">
                              <input
                                type="text" className="input input-bordered input-xs flex-1 font-mono"
                                placeholder="optional scope"
                                value={bm?.boundary ?? ''}
                                onChange={(e) => updateBoundary(path, { boundary: e.target.value || null })}
                              />
                              {selectedPageId && (
                                <button className="btn btn-ghost btn-xs btn-square" onClick={() => enablePicker(path, 'boundary')} title="Pick boundary">
                                  <span className="icon-[tabler--pointer] size-3" />
                                </button>
                              )}
                            </div>
                          </div>
                          {field.is_array && (
                            <div className="form-control">
                              <label className="label py-0"><span className="label-text text-[10px] text-base-content/40">Iterator</span></label>
                              <div className="flex gap-1">
                                <input
                                  type="text" className="input input-bordered input-xs flex-1 font-mono"
                                  placeholder="repeating element"
                                  value={bm?.iterator ?? ''}
                                  onChange={(e) => updateBoundary(path, { iterator: e.target.value || null })}
                                />
                                {selectedPageId && (
                                  <button className="btn btn-ghost btn-xs btn-square" onClick={() => enablePicker(path, 'iterator')} title="Pick iterator">
                                    <span className="icon-[tabler--pointer] size-3" />
                                  </button>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* Field mappings */}
              <div className="space-y-2">
                <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
                  Field Mappings
                </h3>
                {leafFields.map(({ path, field }) => {
                  const mapping = getMapping(path)
                  return (
                    <div key={path} className="bg-base-200/50 rounded-lg p-3 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className={`badge badge-soft badge-xs ${
                          field.field_type === 'image' ? 'badge-warning' :
                          field.field_type === 'number' ? 'badge-info' : 'badge-neutral'
                        }`}>
                          {field.field_type}
                        </span>
                        <span className="text-sm font-medium font-mono">{path}</span>
                      </div>
                      <div className="flex gap-1">
                        <input
                          type="text"
                          className="input input-bordered input-xs flex-1 font-mono text-xs"
                          placeholder="CSS selector"
                          value={mapping?.selector ?? ''}
                          onChange={(e) => updateMapping(path, { selector: e.target.value || null })}
                          onBlur={(e) => e.target.value && highlightSelector(e.target.value)}
                          disabled={!!mapping?.url_regex}
                          title={mapping?.url_regex ? 'Disabled — URL regex takes priority' : ''}
                        />
                        <input
                          type="text"
                          className="input input-bordered input-xs w-20 font-mono text-xs"
                          placeholder="attr"
                          title="Attribute (optional, e.g. href, src)"
                          value={mapping?.attribute ?? ''}
                          onChange={(e) => updateMapping(path, { attribute: e.target.value || null })}
                          disabled={!!mapping?.url_regex}
                        />
                        {selectedPageId && (
                          <button
                            className={`btn btn-xs btn-square ${pickerActive && pickerTarget === path ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => pickerActive && pickerTarget === path ? disablePicker() : enablePicker(path, 'field')}
                            title="Pick element"
                          >
                            <span className="icon-[tabler--pointer] size-3" />
                          </button>
                        )}
                      </div>
                      <input
                        type="text"
                        className="input input-bordered input-xs w-full font-mono text-[10px] mt-1"
                        placeholder={`URL regex (optional, capture group 1 → value, e.g. /id/(\\d+))`}
                        value={mapping?.url_regex ?? ''}
                        onChange={(e) => updateMapping(path, { url_regex: e.target.value || null })}
                        title="If set, the field value comes from this regex match against the page URL — overrides the selector."
                      />
                    </div>
                  )
                })}
                {leafFields.length === 0 && (
                  <div className="text-sm text-base-content/40 py-4 text-center">
                    Define schema fields in the previous step
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      ) : (
        /* File-based mode */
        <div className="space-y-4">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
            File Patterns
          </h3>
          <div className="flex items-start gap-3 p-3 rounded-lg bg-info/10 border border-info/20">
            <span className="icon-[tabler--info-circle] size-5 text-info shrink-0 mt-0.5" />
            <p className="text-sm text-base-content/70">
              Define regex patterns to match scraped filenames. Each pattern maps to a category key in the output.
            </p>
          </div>
          <div className="space-y-3">
            {filePatterns.map((pattern, i) => (
              <div key={i} className="flex gap-2 items-center bg-base-200/50 rounded-xl p-3">
                <div className="form-control w-36 shrink-0">
                  <label className="label py-0.5"><span className="label-text text-[10px] text-base-content/40">Key</span></label>
                  <input type="text" className="input input-bordered input-sm" placeholder="report"
                    value={pattern.schema_key}
                    onChange={(e) => { const n = [...filePatterns]; n[i] = { ...n[i], schema_key: e.target.value }; setFilePatterns(n) }}
                  />
                </div>
                <div className="form-control flex-1">
                  <label className="label py-0.5"><span className="label-text text-[10px] text-base-content/40">Regex pattern</span></label>
                  <input type="text" className="input input-bordered input-sm font-mono text-xs"
                    placeholder="^report-[0-9]{4}\.pdf$"
                    value={pattern.regex_pattern}
                    onChange={(e) => { const n = [...filePatterns]; n[i] = { ...n[i], regex_pattern: e.target.value }; setFilePatterns(n) }}
                  />
                </div>
                <button className="btn btn-ghost btn-xs btn-square text-error mt-5"
                  onClick={() => setFilePatterns(filePatterns.filter((_, j) => j !== i))}>
                  <span className="icon-[tabler--x] size-4" />
                </button>
              </div>
            ))}
            <button className="btn btn-ghost btn-sm gap-1.5"
              onClick={() => setFilePatterns([...filePatterns, { schema_key: '', regex_pattern: '' }])}>
              <span className="icon-[tabler--plus] size-4" />
              Add Pattern
            </button>
          </div>
        </div>
      )}

      {/* Continue */}
      <div className="flex justify-end pt-2">
        <button
          className="btn btn-primary gap-2"
          disabled={!hasValidMappings}
          onClick={handleRunExtraction}
        >
          <span className="icon-[tabler--play] size-5" />
          Run Extraction
        </button>
      </div>
    </div>
  )
}
