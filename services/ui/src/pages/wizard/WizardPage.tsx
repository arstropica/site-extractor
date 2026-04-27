import { useEffect, useMemo, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { jobs, extraction, type JobDetail, type ScrapeConfig } from '@/api/client'
import { useJobStore } from '@/stores/jobStore'
import Stepper from '@/components/Stepper'
import EditableTitle from '@/components/EditableTitle'
import ScraperConfigStep from './ScraperConfigStep'
import ScrapeMonitorStep from './ScrapeMonitorStep'
import SchemaBuilderStep from './SchemaBuilderStep'
import ContentMapperStep from './ContentMapperStep'
import ResultsStep from './ResultsStep'
import { computePipelineStages, nextStageForJob, type StageName } from '@/lib/pipelineStages'

// URL-stage <-> step-index mapping. The URL is the source of truth for
// which step the wizard shows, so refresh / back-button / deep-linking
// all work without resetting to step 0 or auto-jumping based on status.
// "Where to land the user when the URL has no stage" is a separate
// concern — handled by nextStageForJob() in @/lib/pipelineStages,
// shared with the server-side derivation.
const STAGES = ['config', 'scrape', 'schema', 'mapper', 'results'] as const
type Stage = (typeof STAGES)[number]
const STAGE_TO_INDEX: Record<Stage, number> = {
  config: 0, scrape: 1, schema: 2, mapper: 3, results: 4,
}
const isStage = (s: string | undefined): s is Stage =>
  !!s && (STAGES as readonly string[]).includes(s)

const STEPS: { id: StageName; label: string; description: string }[] = [
  { id: 'config', label: 'Scraper Config', description: 'URLs & settings' },
  { id: 'scrape', label: 'Scrape Monitor', description: 'Real-time progress' },
  { id: 'schema', label: 'Schema Builder', description: 'Define structure' },
  { id: 'mapper', label: 'Content Mapper', description: 'Map selectors' },
  { id: 'results', label: 'Results', description: 'View & export' },
]

export default function WizardPage() {
  const { jobId, stage: urlStage } = useParams<{ jobId: string; stage?: string }>()
  const navigate = useNavigate()
  const isNew = jobId === 'new'

  const { activeJob, setActiveJob } = useJobStore()

  // Pipeline-stage status is a pure projection of the active job record.
  // useMemo just keeps the object identity stable across renders so the
  // Stepper's prop comparison stays cheap; the function itself is fast.
  const stages = useMemo(() => computePipelineStages(activeJob), [activeJob])

  // Derive the current step from the URL stage. If the URL has no stage
  // segment yet (or an invalid one), currentStep falls back to 0 until
  // the redirect effect below kicks in.
  const currentStep = isStage(urlStage) ? STAGE_TO_INDEX[urlStage] : 0

  const goToStage = useCallback(
    (stage: Stage, opts?: { replace?: boolean }) => {
      if (!jobId) return
      navigate(`/job/${jobId}/${stage}`, { replace: opts?.replace })
    },
    [jobId, navigate],
  )

  // Load existing job
  const { data: jobData } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => jobs.get(jobId!),
    enabled: !isNew && !!jobId,
    staleTime: 5000,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      // Poll while scraping or extracting
      return status === 'scraping' || status === 'extracting' ? 2000 : false
    },
  })

  // Sync activeJob from query. Stepper visuals + redirect target both
  // derive from activeJob via computePipelineStages / nextStageForJob.
  // setActiveJob clears scrapeEvents itself when the job id changes,
  // so per-job state can't leak across views.
  useEffect(() => {
    if (isNew) {
      setActiveJob(null)
      return
    }
    if (!jobData) return
    setActiveJob(jobData)
  }, [isNew, jobData, setActiveJob])

  // Stage normalization: redirect once on mount when the URL has no stage
  // (or an invalid stage). Replace-history so the bare /job/<id> URL
  // doesn't sit in browser history.
  useEffect(() => {
    if (!jobId) return
    if (isStage(urlStage)) return
    if (isNew) {
      navigate(`/job/new/config`, { replace: true })
      return
    }
    if (!jobData) return // wait until we know the job's status
    navigate(`/job/${jobId}/${nextStageForJob(jobData)}`, { replace: true })
  }, [jobId, urlStage, isNew, jobData, navigate])

  // Scrape progress for stepper animation. "Completed" = downloaded + errored
  // — both are definitive outcomes; the bar shouldn't sit short of full just
  // because some URLs failed (the ScrapeMonitorStep's stacked bar surfaces
  // the success/error partition separately).
  const scrapeProgress: Record<number, number> = {}
  if (activeJob?.status === 'scraping') {
    const totalDiscovered = (activeJob.pages_discovered ?? 0) + (activeJob.resources_discovered ?? 0)
    const totalDone = (activeJob.pages_downloaded ?? 0) + (activeJob.resources_downloaded ?? 0)
    const totalErrored = (activeJob.pages_errored ?? 0) + (activeJob.resources_errored ?? 0)
    if (totalDiscovered > 0) {
      scrapeProgress[1] = Math.min((totalDone + totalErrored) / totalDiscovered, 0.99)
    }
  }

  // (failedSteps removed — Stepper now derives failure from `stages` directly.)

  // Create job + start scrape. Used for both genuinely new jobs and clones —
  // clones arrive at /job/new with a draft config in the store, so the
  // create flow is the only path that actually persists a row.
  const createAndStartMutation = useMutation({
    mutationFn: async ({ config, name }: { config: ScrapeConfig; name?: string }) => {
      const result = await jobs.create(config, name)
      const newJob = await jobs.get(result.job_id)
      // setActiveJob clears scrapeEvents on id change.
      setActiveJob(newJob)
      navigate(`/job/${result.job_id}/scrape`, { replace: true })
      await jobs.startScrape(result.job_id)
      const updated = await jobs.get(result.job_id)
      setActiveJob(updated)
    },
  })

  const handleStepClick = useCallback(
    (step: number) => {
      // Trust the Stepper's accessibility check (it already disables
      // unreachable steps and only fires onStepClick for accessible ones).
      // No further gating here — the URL is the source of truth, so any
      // accessible step click should navigate.
      goToStage(STAGES[step])
    },
    [goToStage],
  )

  const goToStep = useCallback(
    (step: number) => {
      goToStage(STAGES[step])
    },
    [goToStage],
  )

  const queryClient = useQueryClient()

  // When entering Results step, trigger extraction
  const startExtractionMutation = useMutation({
    mutationFn: async () => {
      if (!activeJob) return
      const result = await extraction.start(activeJob.id)
      const updated = await jobs.get(activeJob.id)
      setActiveJob(updated)
      return result
    },
    onSuccess: () => {
      // Refetch results so the table populates immediately
      if (activeJob) {
        queryClient.invalidateQueries({ queryKey: ['results', activeJob.id] })
      }
    },
  })

  const handleMapperContinue = useCallback(() => {
    goToStage('results')
    startExtractionMutation.mutate()
  }, [goToStage, startExtractionMutation])

  return (
    <>
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            className="btn btn-sm btn-square btn-ghost"
            onClick={() => navigate('/')}
          >
            <span className="icon-[tabler--arrow-left] size-5" />
          </button>
          <div>
            {isNew ? (
              <h2 className="text-2xl font-semibold">New Extraction Job</h2>
            ) : activeJob ? (
              <EditableTitle
                value={activeJob.name ?? ''}
                placeholder={`Job ${activeJob.id.slice(0, 8)}`}
                onSave={async (newName) => {
                  const updated = await jobs.update(activeJob.id, { name: newName || null })
                  setActiveJob(updated)
                }}
              />
            ) : (
              <h2 className="text-2xl font-semibold">Job {jobId?.slice(0, 8)}</h2>
            )}
            {activeJob && (
              <p className="text-sm text-base-content/50 mt-0.5">
                {activeJob.name && (
                  <span className="text-base-content/30 mr-1.5">
                    Job {activeJob.id.slice(0, 8)} ·
                  </span>
                )}
                {activeJob.scrape_config.seed_urls[0]}
                {activeJob.scrape_config.seed_urls.length > 1 &&
                  ` +${activeJob.scrape_config.seed_urls.length - 1} more`}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Stepper */}
      <div className="card shadow-base-300/10 shadow-md">
        <div className="card-body p-5">
          <Stepper
            steps={STEPS}
            currentStep={currentStep}
            stages={stages}
            stepProgress={scrapeProgress}
            onStepClick={handleStepClick}
          />
        </div>
      </div>

      {/* Step content */}
      <div className="card shadow-base-300/10 shadow-md">
        <div className="card-body p-5 sm:p-6">
          {currentStep === 0 && (
            <ScraperConfigStep
              // Remount when the loaded job changes — for an existing job,
              // jobData lands asynchronously after activeJob is still null,
              // so keying on activeJob.id ensures the form picks up the
              // server config when it arrives.
              key={isNew ? 'new' : (activeJob?.id ?? 'loading')}
              onSubmit={(config, name) => createAndStartMutation.mutate({ config, name })}
              initialConfig={isNew ? undefined : activeJob?.scrape_config}
              initialName={isNew ? '' : (activeJob?.name ?? '')}
              isLoading={createAndStartMutation.isPending}
              readOnly={!isNew && !!activeJob && activeJob.status !== 'created'}
            />
          )}
          {currentStep === 1 && <ScrapeMonitorStep onContinue={() => goToStep(2)} />}
          {currentStep === 2 && <SchemaBuilderStep onContinue={() => goToStep(3)} />}
          {currentStep === 3 && <ContentMapperStep onContinue={handleMapperContinue} />}
          {currentStep === 4 && <ResultsStep />}
        </div>
      </div>
    </>
  )
}
