import { useEffect, useRef, useState, useCallback } from 'react'
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

// URL-stage <-> step-index mapping. The URL is the source of truth for
// which step the wizard shows, so refresh / back-button / deep-linking
// all work without resetting to step 0 or auto-jumping based on status.
const STAGES = ['config', 'scrape', 'schema', 'mapper', 'results'] as const
type Stage = (typeof STAGES)[number]
const STAGE_TO_INDEX: Record<Stage, number> = {
  config: 0, scrape: 1, schema: 2, mapper: 3, results: 4,
}
const isStage = (s: string | undefined): s is Stage =>
  !!s && (STAGES as readonly string[]).includes(s)

function stageForJobStatus(job: JobDetail | null): Stage {
  if (!job) return 'config'
  switch (job.status) {
    case 'created': return 'config'
    case 'scraping':
    case 'paused':
    case 'failed':
    case 'cancelled': return 'scrape'
    case 'scraped': return 'schema'
    case 'mapping': return 'mapper'
    case 'extracting':
    case 'completed': return 'results'
    default: return 'config'
  }
}

const STEPS = [
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

  const {
    activeJob,
    setActiveJob,
    completedSteps,
    markStepCompleted,
    clearScrapeEvents,
    draftConfig,
    draftName,
    setDraft,
  } = useJobStore()

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

  // Snapshot the draft config (set by HistoryPage's clone button) at mount,
  // then clear it from the store so it doesn't leak into a later /job/new
  // navigation. Lazy useState init captures the value before any re-render
  // can wipe it.
  const [initialDraft] = useState(() =>
    isNew ? { config: draftConfig, name: draftName } : { config: null, name: null },
  )
  useEffect(() => {
    if (isNew && (draftConfig || draftName)) setDraft(null, null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

  // Sync activeJob from query + recompute completion marks for the stepper.
  // No automatic step navigation here — the URL is the source of truth.
  useEffect(() => {
    if (isNew) {
      setActiveJob(null)
      clearScrapeEvents()
      return
    }
    if (!jobData) return
    setActiveJob(jobData)

    // Step completion marks (visual ticks in the stepper) follow status:
    // every step at or below the status-implied stage is "complete".
    const impliedStage = stageForJobStatus(jobData)
    const impliedStep = STAGE_TO_INDEX[impliedStage]
    for (let i = 0; i < impliedStep; i++) markStepCompleted(i)
    if (jobData.status === 'completed') markStepCompleted(4)
  }, [isNew, jobData, setActiveJob, markStepCompleted, clearScrapeEvents])

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
    navigate(`/job/${jobId}/${stageForJobStatus(jobData)}`, { replace: true })
  }, [jobId, urlStage, isNew, jobData, navigate])

  // Scrape progress for stepper animation
  const scrapeProgress: Record<number, number> = {}
  if (activeJob?.status === 'scraping') {
    const totalDiscovered = (activeJob.pages_discovered ?? 0) + (activeJob.resources_discovered ?? 0)
    const totalDone = (activeJob.pages_downloaded ?? 0) + (activeJob.resources_downloaded ?? 0)
    if (totalDiscovered > 0) {
      scrapeProgress[1] = Math.min(totalDone / totalDiscovered, 0.99)
    }
  }

  // Failed steps
  const failedSteps = new Set<number>()
  if (activeJob?.status === 'failed') {
    if (currentStep <= 1) failedSteps.add(1)
    else failedSteps.add(currentStep)
  }

  // Create job + start scrape. Used for both genuinely new jobs and clones —
  // clones arrive at /job/new with a draft config in the store, so the
  // create flow is the only path that actually persists a row.
  const createAndStartMutation = useMutation({
    mutationFn: async ({ config, name }: { config: ScrapeConfig; name?: string }) => {
      const result = await jobs.create(config, name)
      const newJob = await jobs.get(result.job_id)
      setActiveJob(newJob)
      markStepCompleted(0)
      clearScrapeEvents()
      navigate(`/job/${result.job_id}/scrape`, { replace: true })
      await jobs.startScrape(result.job_id)
      const updated = await jobs.get(result.job_id)
      setActiveJob(updated)
    },
  })

  const handleStepClick = useCallback(
    (step: number) => {
      if (completedSteps.has(step) || step === currentStep) {
        goToStage(STAGES[step])
      }
    },
    [completedSteps, currentStep, goToStage],
  )

  const goToStep = useCallback(
    (step: number) => {
      markStepCompleted(step - 1)
      goToStage(STAGES[step])
    },
    [markStepCompleted, goToStage],
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
    markStepCompleted(3)
    goToStage('results')
    startExtractionMutation.mutate()
  }, [markStepCompleted, goToStage, startExtractionMutation])

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
            completedSteps={completedSteps}
            failedSteps={failedSteps}
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
              initialConfig={isNew ? (initialDraft.config ?? undefined) : activeJob?.scrape_config}
              initialName={isNew ? (initialDraft.name ?? '') : (activeJob?.name ?? '')}
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
