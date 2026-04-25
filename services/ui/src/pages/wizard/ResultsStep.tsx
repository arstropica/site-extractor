import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { extraction, jobs as jobsApi } from '@/api/client'
import { useJobStore } from '@/stores/jobStore'

export default function ResultsStep() {
  const { activeJob, setActiveJob } = useJobStore()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(0)
  const [sortBy, setSortBy] = useState<string | undefined>()
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [normalize, setNormalize] = useState(false)
  const limit = 50

  const jobId = activeJob?.id ?? ''

  const { data, isLoading } = useQuery({
    queryKey: ['results', jobId, page, sortBy, sortDir],
    queryFn: () =>
      extraction.results(jobId, {
        limit,
        offset: page * limit,
        sort_by: sortBy,
        sort_dir: sortDir,
      }),
    enabled: !!jobId,
    staleTime: 10000,
  })

  const rerunMutation = useMutation({
    mutationFn: async () => {
      const result = await extraction.start(jobId)
      const updated = await jobsApi.get(jobId)
      setActiveJob(updated)
      return result
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['results', jobId] })
    },
  })

  const results = (data?.results ?? []) as Array<{ data?: Record<string, unknown> }>
  const totalCount = data?.count ?? 0
  const totalPages = Math.ceil(totalCount / limit)

  // Collect all column keys from results
  const columns = new Set<string>()
  for (const row of results) {
    const rowData = row.data ?? row
    flattenKeys(rowData as Record<string, unknown>).forEach((k) => columns.add(k))
  }
  const columnList = Array.from(columns)

  function flattenKeys(obj: Record<string, unknown>, prefix = ''): string[] {
    const keys: string[] = []
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        keys.push(...flattenKeys(v as Record<string, unknown>, key))
      } else {
        keys.push(key)
      }
    }
    return keys
  }

  function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
    return path.split('.').reduce<unknown>((o, k) => {
      if (o && typeof o === 'object') return (o as Record<string, unknown>)[k]
      return undefined
    }, obj)
  }

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col)
      setSortDir('asc')
    }
    setPage(0)
  }

  return (
    <div className="space-y-6">
      {/* Header with actions */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
            Extraction Results
          </h3>
          {totalCount > 0 && (
            <p className="text-xs text-base-content/40 mt-0.5">{totalCount} rows</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-ghost btn-sm gap-1.5"
            disabled={rerunMutation.isPending}
            onClick={() => rerunMutation.mutate()}
            title="Re-run extraction with current mappings"
          >
            {rerunMutation.isPending ? (
              <span className="icon-[tabler--loader-2] size-4 animate-spin" />
            ) : (
              <span className="icon-[tabler--refresh] size-4" />
            )}
            Re-run
          </button>
          {totalCount > 0 && (
            <>
              <label
                className="flex items-center gap-2 cursor-pointer text-xs text-base-content/60 px-2"
                title="Convert smart quotes/dashes/ellipses to ASCII for compatibility with tools that don't handle Unicode well"
              >
                <input
                  type="checkbox"
                  className="checkbox checkbox-xs checkbox-primary"
                  checked={normalize}
                  onChange={(e) => setNormalize(e.target.checked)}
                />
                Normalize
              </label>
              <a
                href={extraction.exportUrl(jobId, 'json', normalize)}
                className="btn btn-ghost btn-sm gap-1.5"
                download
              >
                <span className="icon-[tabler--download] size-4" />
                JSON
              </a>
              <a
                href={extraction.exportUrl(jobId, 'csv', normalize)}
                className="btn btn-ghost btn-sm gap-1.5"
                download
              >
                <span className="icon-[tabler--download] size-4" />
                CSV
              </a>
            </>
          )}
        </div>
      </div>

      {/* Re-run success message */}
      {rerunMutation.isSuccess && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-success/10 border border-success/20">
          <span className="icon-[tabler--check] size-4 text-success" />
          <span className="text-sm text-success">
            Extraction complete — {rerunMutation.data?.rows_extracted ?? 0} rows extracted
          </span>
        </div>
      )}

      {/* Results table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <span className="icon-[tabler--loader-2] size-8 animate-spin text-base-content/30" />
        </div>
      ) : results.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-base-content/40">
          <span className="icon-[tabler--table] size-16" />
          <div className="text-center">
            <p className="text-lg font-medium">No results yet</p>
            <p className="text-sm mt-1">
              {activeJob?.status === 'extracting'
                ? 'Extraction in progress...'
                : 'Run extraction to populate results.'}
            </p>
          </div>
        </div>
      ) : (
        <div className="bg-base-200/50 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th className="w-12 text-base-content/40">#</th>
                  {columnList.map((col) => (
                    <th
                      key={col}
                      className="cursor-pointer hover:bg-base-content/5 select-none transition-colors"
                      onClick={() => handleSort(col)}
                    >
                      <div className="flex items-center gap-1">
                        <span className="text-xs">{col}</span>
                        {sortBy === col && (
                          <span
                            className={`icon-[tabler--chevron-${sortDir === 'asc' ? 'up' : 'down'}] size-3`}
                          />
                        )}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.map((row, i) => {
                  const rowData = (row.data ?? row) as Record<string, unknown>
                  return (
                    <tr key={i} className="hover">
                      <td className="text-xs text-base-content/30">{page * limit + i + 1}</td>
                      {columnList.map((col) => {
                        const value = getNestedValue(rowData, col)
                        return (
                          <td key={col} className="max-w-xs truncate text-sm">
                            {value === null || value === undefined ? (
                              <span className="text-base-content/20">—</span>
                            ) : typeof value === 'object' ? (
                              <code className="text-xs bg-base-300/50 px-1.5 py-0.5 rounded">
                                {JSON.stringify(value)}
                              </code>
                            ) : (
                              String(value)
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2">
          <button
            className="btn btn-sm btn-ghost"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            <span className="icon-[tabler--chevron-left] size-4" />
            Previous
          </button>
          <span className="btn btn-sm btn-ghost pointer-events-none">
            {page + 1} / {totalPages}
          </span>
          <button
            className="btn btn-sm btn-ghost"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(page + 1)}
          >
            Next
            <span className="icon-[tabler--chevron-right] size-4" />
          </button>
        </div>
      )}
    </div>
  )
}
