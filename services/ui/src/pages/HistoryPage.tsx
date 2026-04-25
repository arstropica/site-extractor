import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import { jobs, type Job } from '@/api/client'
import { useToast } from '@/components/Toaster'

const statusConfig: Record<string, { icon: string; badge: string }> = {
  created: { icon: 'icon-[tabler--circle-dot]', badge: 'badge-neutral' },
  scraping: { icon: 'icon-[tabler--loader-2]', badge: 'badge-info' },
  scraped: { icon: 'icon-[tabler--circle-check]', badge: 'badge-accent' },
  mapping: { icon: 'icon-[tabler--edit]', badge: 'badge-accent' },
  extracting: { icon: 'icon-[tabler--loader-2]', badge: 'badge-warning' },
  completed: { icon: 'icon-[tabler--circle-check]', badge: 'badge-success' },
  failed: { icon: 'icon-[tabler--circle-x]', badge: 'badge-error' },
  paused: { icon: 'icon-[tabler--player-pause]', badge: 'badge-warning' },
  cancelled: { icon: 'icon-[tabler--circle-minus]', badge: 'badge-neutral' },
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

type DatePreset = 'all' | '7d' | '30d' | '90d' | 'custom'

function isoFor(preset: DatePreset, customFrom: string, customTo: string): { from?: string; to?: string } {
  if (preset === 'all') return {}
  if (preset === 'custom') {
    return {
      from: customFrom ? new Date(customFrom).toISOString() : undefined,
      to: customTo ? new Date(customTo + 'T23:59:59').toISOString() : undefined,
    }
  }
  const days = preset === '7d' ? 7 : preset === '30d' ? 30 : 90
  const from = new Date()
  from.setDate(from.getDate() - days)
  return { from: from.toISOString() }
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [datePreset, setDatePreset] = useState<DatePreset>('all')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo] = useState('')
  const [page, setPage] = useState(0)
  const limit = 20

  const { from: dateFrom, to: dateTo } = isoFor(datePreset, customFrom, customTo)

  const { data, isLoading } = useQuery({
    queryKey: ['jobs', statusFilter, search, dateFrom, dateTo, page],
    queryFn: () =>
      jobs.list({
        status: statusFilter || undefined,
        search: search || undefined,
        date_from: dateFrom,
        date_to: dateTo,
        limit,
        offset: page * limit,
      }),
    staleTime: 5000,
  })

  const queryClient = useQueryClient()
  const toast = useToast()
  const cloneMutation = useMutation({
    mutationFn: (id: string) => jobs.clone(id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      toast.success('Job cloned', `Editing the new copy now`)
      navigate(`/job/${result.job_id}`)
    },
    onError: (e) => toast.error('Clone failed', e instanceof Error ? e.message : String(e)),
  })

  const [deleteTarget, setDeleteTarget] = useState<Job | null>(null)
  const [keepDisk, setKeepDisk] = useState(true)
  const deleteMutation = useMutation({
    mutationFn: ({ id, keep }: { id: string; keep: boolean }) => jobs.delete(id, !keep),
    onSuccess: (_result, vars) => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      toast.success(
        'Job deleted',
        vars.keep ? 'Disk results were kept.' : 'Disk results were removed.',
      )
      setDeleteTarget(null)
    },
    onError: (e) => toast.error('Delete failed', e instanceof Error ? e.message : String(e)),
  })

  const jobList = data?.jobs ?? []
  const totalCount = data?.count ?? 0
  const totalPages = Math.ceil(totalCount / limit)

  return (
    <>
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Job History</h2>
          <p className="text-sm text-base-content/50 mt-1">{totalCount} jobs</p>
        </div>
        <button className="btn btn-primary gap-2" onClick={() => navigate('/job/new')}>
          <span className="icon-[tabler--plus] size-5" />
          New Job
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <span className="icon-[tabler--search] size-4 absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40" />
          <input
            type="text"
            placeholder="Search by name or URL..."
            className="input input-bordered w-full pl-10"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(0)
            }}
          />
        </div>
        <select
          className="select select-bordered w-full sm:w-36"
          value={datePreset}
          onChange={(e) => {
            setDatePreset(e.target.value as DatePreset)
            setPage(0)
          }}
        >
          <option value="all">Any date</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="90d">Last 90 days</option>
          <option value="custom">Custom range</option>
        </select>
        {datePreset === 'custom' && (
          <>
            <input
              type="date"
              className="input input-bordered w-full sm:w-40"
              value={customFrom}
              onChange={(e) => { setCustomFrom(e.target.value); setPage(0) }}
              title="From"
            />
            <input
              type="date"
              className="input input-bordered w-full sm:w-40"
              value={customTo}
              onChange={(e) => { setCustomTo(e.target.value); setPage(0) }}
              title="To"
            />
          </>
        )}
        <select
          className="select select-bordered w-full sm:w-40"
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value)
            setPage(0)
          }}
        >
          <option value="">All Status</option>
          <option value="created">Created</option>
          <option value="scraping">Scraping</option>
          <option value="scraped">Scraped</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="paused">Paused</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <span className="icon-[tabler--loader-2] size-8 animate-spin text-base-content/30" />
        </div>
      ) : jobList.length === 0 ? (
        <div className="card shadow-base-300/10 shadow-md">
          <div className="card-body flex flex-col items-center justify-center py-16 gap-4 text-base-content/50">
            <span className="icon-[tabler--spider] size-16" />
            <div className="text-center">
              <p className="text-lg font-medium">No jobs yet</p>
              <p className="text-sm mt-1">Create a new extraction job to get started.</p>
            </div>
          </div>
        </div>
      ) : (
        <div className="card shadow-base-300/10 shadow-md">
          <div className="card-body p-0">
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Items</th>
                  <th>Duration</th>
                  <th className="text-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobList.map((job: Job) => {
                  const config = statusConfig[job.status] || statusConfig.created
                  const isRunning = job.status === 'scraping' || job.status === 'extracting'
                  const displayName = job.name || `Job ${job.id.slice(0, 8)}`
                  return (
                    <tr
                      key={job.id}
                      className="hover cursor-pointer"
                      onClick={() => navigate(`/job/${job.id}`)}
                    >
                      <td>
                        <span className="text-xs text-base-content/50">
                          {new Date(job.created_at).toLocaleString()}
                        </span>
                      </td>
                      <td>
                        <div className="max-w-xs">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium truncate">
                              {displayName}
                            </span>
                            {(job.seed_urls?.length ?? 0) > 1 && (
                              <span className="badge badge-soft badge-xs badge-primary shrink-0">
                                +{job.seed_urls.length - 1}
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-base-content/40 truncate mt-0.5">
                            {job.seed_urls?.[0] ?? '—'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className={`badge badge-soft badge-sm ${config.badge} gap-1`}>
                          <span
                            className={`${config.icon} size-3 ${isRunning ? 'animate-spin' : ''}`}
                          />
                          {job.status}
                        </span>
                      </td>
                      <td>
                        <span className="text-xs text-base-content/50">
                          {job.extraction_mode ?? '—'}
                        </span>
                      </td>
                      <td>
                        <div className="text-sm leading-tight">
                          <div>
                            <span className="text-base-content/40 mr-1">P</span>
                            {job.pages_downloaded}
                          </div>
                          <div>
                            <span className="text-base-content/40 mr-1">R</span>
                            {job.resources_downloaded ?? 0}
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="text-xs text-base-content/50">
                          {formatDuration(job.duration_seconds)}
                        </span>
                      </td>
                      <td className="text-end" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          <button
                            className="btn btn-ghost btn-xs btn-square"
                            title="Clone job"
                            disabled={cloneMutation.isPending}
                            onClick={() => cloneMutation.mutate(job.id)}
                          >
                            <span className="icon-[tabler--copy] size-4" />
                          </button>
                          <button
                            className="btn btn-ghost btn-xs btn-square text-error/80 hover:text-error"
                            title={
                              job.status === 'scraping' || job.status === 'extracting'
                                ? 'Cancel the job before deleting it'
                                : 'Delete job'
                            }
                            disabled={
                              job.status === 'scraping' || job.status === 'extracting'
                            }
                            onClick={() => {
                              setKeepDisk(true)
                              setDeleteTarget(job)
                            }}
                          >
                            <span className="icon-[tabler--trash] size-4" />
                          </button>
                          <span className="icon-[tabler--chevron-right] size-4 text-base-content/30" />
                        </div>
                      </td>
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

      {/* Delete confirmation modal */}
      <Dialog.Root
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[70] w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-base-content/10 bg-base-100 p-6 shadow-2xl">
            <Dialog.Title className="text-lg font-semibold">Delete this job?</Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-base-content/60">
              The job entry, indexed pages and resources, and Redis cache will be removed.
              Choose what to do with the files on disk.
            </Dialog.Description>

            <div className="mt-4 space-y-2">
              <label className="flex items-start gap-3 rounded-xl border border-base-content/10 p-3 cursor-pointer hover:bg-base-content/5">
                <input
                  type="radio"
                  name="delete-mode"
                  className="radio radio-sm mt-0.5"
                  checked={keepDisk}
                  onChange={() => setKeepDisk(true)}
                />
                <div className="flex-1">
                  <div className="text-sm font-medium">Keep disk results</div>
                  <div className="text-xs text-base-content/50 mt-0.5">
                    Saved pages, assets, and extraction outputs stay in the data directory.
                  </div>
                </div>
              </label>
              <label className="flex items-start gap-3 rounded-xl border border-base-content/10 p-3 cursor-pointer hover:bg-base-content/5">
                <input
                  type="radio"
                  name="delete-mode"
                  className="radio radio-sm mt-0.5"
                  checked={!keepDisk}
                  onChange={() => setKeepDisk(false)}
                />
                <div className="flex-1">
                  <div className="text-sm font-medium">Delete everything</div>
                  <div className="text-xs text-base-content/50 mt-0.5">
                    Also remove pages, assets, and extraction outputs from disk.
                  </div>
                </div>
              </label>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <Dialog.Close asChild>
                <button className="btn btn-sm btn-ghost">Cancel</button>
              </Dialog.Close>
              <button
                className="btn btn-sm btn-error"
                disabled={deleteMutation.isPending}
                onClick={() =>
                  deleteTarget &&
                  deleteMutation.mutate({ id: deleteTarget.id, keep: keepDisk })
                }
              >
                {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  )
}
