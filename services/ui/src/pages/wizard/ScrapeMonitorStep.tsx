import { useJobStore } from '@/stores/jobStore'
import { jobs as jobsApi, pages as pagesApi } from '@/api/client'
import { useQuery } from '@tanstack/react-query'
import PageTree from '@/components/PageTree'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

interface ScrapeMonitorStepProps {
  onContinue: () => void
}

export default function ScrapeMonitorStep({ onContinue }: ScrapeMonitorStepProps) {
  const { activeJob, scrapeEvents, updateActiveJob } = useJobStore()
  const jobId = activeJob?.id ?? ''
  const isLive = activeJob?.status === 'scraping' || activeJob?.status === 'paused'
  const { data: pageData } = useQuery({
    queryKey: ['pages', jobId],
    queryFn: () => pagesApi.list(jobId, 500),
    enabled: !!jobId,
    refetchInterval: isLive ? 3000 : false,
    staleTime: 5000,
  })

  if (!activeJob) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4 text-base-content/50">
        <span className="icon-[tabler--spider] size-16" />
        <p className="text-lg font-medium">No active job</p>
      </div>
    )
  }

  const isComplete = activeJob.status === 'scraped'
  const isFailed = activeJob.status === 'failed'
  const isRunning = activeJob.status === 'scraping'
  const isPaused = activeJob.status === 'paused'
  const isCancelled = activeJob.status === 'cancelled'
  const canRerun = isComplete || isFailed || isCancelled

  const totalDiscovered = (activeJob.pages_discovered ?? 0) + (activeJob.resources_discovered ?? 0)
  const totalDownloaded = (activeJob.pages_downloaded ?? 0) + (activeJob.resources_downloaded ?? 0)
  const totalErrored = (activeJob.pages_errored ?? 0) + (activeJob.resources_errored ?? 0)
  const progress = totalDiscovered > 0
    ? Math.min((totalDownloaded / totalDiscovered) * 100, 100)
    : 0

  const handlePause = async () => {
    await jobsApi.pause(activeJob.id)
    updateActiveJob({ status: 'paused' })
  }

  const handleResume = async () => {
    await jobsApi.startScrape(activeJob.id)
    updateActiveJob({ status: 'scraping' })
  }

  const handleRerun = async () => {
    if (!confirm('Re-run this scrape? Pages and resources unchanged on the server will be reused from disk; the rest will be re-downloaded.')) return
    await jobsApi.startScrape(activeJob.id)
    updateActiveJob({
      status: 'scraping',
      pages_downloaded: 0,
      pages_discovered: 0,
      resources_downloaded: 0,
      resources_discovered: 0,
      bytes_downloaded: 0,
      error_message: undefined,
    })
  }

  const handleCancel = async () => {
    await jobsApi.cancel(activeJob.id)
    updateActiveJob({ status: 'cancelled' })
  }

  return (
    <div className="space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-6 gap-3">
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Status</p>
          <p className={`text-lg font-semibold mt-1 ${
            isComplete ? 'text-success' : isFailed ? 'text-error' : isRunning ? 'text-info' : isPaused ? 'text-warning' : ''
          }`}>
            {activeJob.status}
          </p>
        </div>
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Pages</p>
          <p className="text-lg font-semibold mt-1">
            {activeJob.pages_downloaded}
            <span className="text-sm text-base-content/40"> / {activeJob.pages_discovered}</span>
          </p>
        </div>
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Resources</p>
          <p className="text-lg font-semibold mt-1">
            {activeJob.resources_downloaded ?? 0}
            <span className="text-sm text-base-content/40"> / {activeJob.resources_discovered ?? 0}</span>
          </p>
        </div>
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Errors</p>
          <p className={`text-lg font-semibold mt-1 ${totalErrored > 0 ? 'text-error' : ''}`}>
            {totalErrored}
          </p>
        </div>
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Total</p>
          <p className="text-lg font-semibold mt-1">
            {totalDownloaded}
            <span className="text-sm text-base-content/40"> / {totalDiscovered}</span>
          </p>
        </div>
        <div className="bg-base-200/50 rounded-xl p-4">
          <p className="text-xs text-base-content/40 uppercase tracking-wider">Size</p>
          <p className="text-lg font-semibold mt-1">{formatBytes(activeJob.bytes_downloaded)}</p>
        </div>
      </div>

      {/* Progress bar */}
      <div className="bg-base-200/50 rounded-xl p-4 space-y-2">
        <div className="flex justify-between text-xs text-base-content/50">
          <span>{totalDownloaded} / {totalDiscovered} URLs</span>
          <span>{Math.round(progress)}%</span>
        </div>
        <div className="h-2 w-full bg-base-300/50 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              isComplete ? 'bg-success' : isFailed ? 'bg-error' : isPaused ? 'bg-warning' : 'bg-primary'
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Controls */}
      {(isRunning || isPaused) && (
        <div className="flex gap-2">
          {isRunning && (
            <button className="btn btn-sm btn-ghost gap-1" onClick={handlePause}>
              <span className="icon-[tabler--player-pause] size-4" />
              Pause
            </button>
          )}
          {isPaused && (
            <button className="btn btn-sm btn-primary gap-1" onClick={handleResume}>
              <span className="icon-[tabler--player-play] size-4" />
              Resume
            </button>
          )}
          <button className="btn btn-sm btn-ghost text-error gap-1" onClick={handleCancel}>
            <span className="icon-[tabler--x] size-4" />
            Cancel
          </button>
        </div>
      )}
      {canRerun && (
        <div className="flex gap-2">
          <button className="btn btn-sm btn-primary gap-1" onClick={handleRerun}>
            <span className="icon-[tabler--refresh] size-4" />
            Re-scrape
          </button>
        </div>
      )}

      {/* Error */}
      {isFailed && activeJob.error_message && (
        <div className="flex items-start gap-3 p-4 rounded-xl bg-error/10 border border-error/20">
          <span className="icon-[tabler--alert-circle] size-5 text-error shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-error">Scrape failed</p>
            <p className="text-sm text-base-content/70 mt-1">{activeJob.error_message}</p>
          </div>
        </div>
      )}

      {/* Event log */}
      {/* Page tree */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Page Tree
        </h3>
        <PageTree pages={pageData?.pages ?? []} />
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-base-content/60">
          Activity Log
        </h3>
        <div className="bg-base-200/50 rounded-xl p-4 max-h-80 overflow-y-auto font-mono text-xs space-y-0.5">
          {scrapeEvents.length === 0 ? (
            <div className="text-base-content/30 text-center py-8">
              {isRunning ? (
                <div className="flex flex-col items-center gap-2">
                  <span className="icon-[tabler--loader-2] size-6 animate-spin" />
                  <span>Waiting for events...</span>
                </div>
              ) : (
                'No events yet'
              )}
            </div>
          ) : (
            scrapeEvents.slice().reverse().map((event, i) => (
              <div key={i} className="flex gap-2 py-0.5">
                <span className="text-base-content/30 shrink-0 w-20">
                  {new Date(event.timestamp).toLocaleTimeString()}
                </span>
                <span
                  className={`shrink-0 w-40 ${
                    event.type === 'SCRAPE_ERROR'
                      ? 'text-error'
                      : event.type === 'PAGE_DOWNLOADED'
                        ? 'text-success'
                        : event.type === 'PAGE_DISCOVERED'
                          ? 'text-info'
                          : event.type === 'RESOURCE_DOWNLOADED'
                            ? 'text-accent'
                            : event.type === 'RESOURCE_DISCOVERED'
                              ? 'text-warning'
                              : 'text-base-content/50'
                  }`}
                >
                  {event.type}
                </span>
                <span className="text-base-content/60 truncate">
                  {(event.data.url as string) ||
                    (event.data.filename as string) ||
                    (event.data.status as string) ||
                    JSON.stringify(event.data)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Continue */}
      {isComplete && (
        <div className="flex justify-end pt-2">
          <button className="btn btn-primary gap-2" onClick={onContinue}>
            Scraping Complete — Continue
            <span className="icon-[tabler--arrow-right] size-5" />
          </button>
        </div>
      )}
    </div>
  )
}
