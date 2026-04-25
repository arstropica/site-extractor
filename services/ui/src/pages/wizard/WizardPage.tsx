import { useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { jobs, extraction, type ScrapeConfig } from '@/api/client'
import { useJobStore } from '@/stores/jobStore'
import Stepper from '@/components/Stepper'
import EditableTitle from '@/components/EditableTitle'
import ScraperConfigStep from './ScraperConfigStep'
import ScrapeMonitorStep from './ScrapeMonitorStep'
import SchemaBuilderStep from './SchemaBuilderStep'
import ContentMapperStep from './ContentMapperStep'
import ResultsStep from './ResultsStep'

const STEPS = [
  { id: 'config', label: 'Scraper Config', description: 'URLs & settings' },
  { id: 'monitor', label: 'Scrape Monitor', description: 'Real-time progress' },
  { id: 'schema', label: 'Schema Builder', description: 'Define structure' },
  { id: 'mapper', label: 'Content Mapper', description: 'Map selectors' },
  { id: 'results', label: 'Results', description: 'View & export' },
]

export default function WizardPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const isNew = jobId === 'new'

  const {
    activeJob,
    setActiveJob,
    currentStep,
    setCurrentStep,
    completedSteps,
    markStepCompleted,
    clearScrapeEvents,
  } = useJobStore()

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

  const initializedJobIdRef = useRef<string | null>(null)
  const prevStatusRef = useRef<string | null>(null)

  useEffect(() => {
    if (isNew) {
      setActiveJob(null)
      setCurrentStep(0)
      clearScrapeEvents()
      initializedJobIdRef.current = null
      prevStatusRef.current = null
      return
    }

    if (!jobData) return

    setActiveJob(jobData)

    // Compute the step implied by job status
    let targetStep = 0
    switch (jobData.status) {
      case 'created':
        targetStep = 0
        break
      case 'scraping':
      case 'paused':
        targetStep = 1
        markStepCompleted(0)
        break
      case 'scraped':
        targetStep = 2
        markStepCompleted(0)
        markStepCompleted(1)
        break
      case 'mapping':
        targetStep = 3
        markStepCompleted(0)
        markStepCompleted(1)
        markStepCompleted(2)
        break
      case 'extracting':
      case 'completed':
        targetStep = 4
        markStepCompleted(0)
        markStepCompleted(1)
        markStepCompleted(2)
        markStepCompleted(3)
        if (jobData.status === 'completed') markStepCompleted(4)
        break
      default:
        targetStep = 0
    }

    const isInitialLoad = initializedJobIdRef.current !== jobData.id
    const statusChanged = prevStatusRef.current !== jobData.status

    if (isInitialLoad) {
      // First load for this job: jump to step implied by status, but never
      // regress below where the wizard's create flow has already set us.
      const current = useJobStore.getState().currentStep
      if (targetStep > current) {
        setCurrentStep(targetStep)
      }
      initializedJobIdRef.current = jobData.id
    } else if (statusChanged) {
      // Status advanced (e.g., scraping → scraped): advance the step too,
      // but only if the user hasn't already navigated past it manually.
      const current = useJobStore.getState().currentStep
      if (targetStep > current) {
        setCurrentStep(targetStep)
      }
    }
    // If neither initial load nor status change, do not touch currentStep —
    // this lets the user click stepper buttons to navigate freely.

    prevStatusRef.current = jobData.status
  }, [isNew, jobData, setActiveJob, setCurrentStep, markStepCompleted, clearScrapeEvents])

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

  // Create job + start scrape
  const createAndStartMutation = useMutation({
    mutationFn: async ({ config, name }: { config: ScrapeConfig; name?: string }) => {
      const result = await jobs.create(config, name)
      const newJob = await jobs.get(result.job_id)
      setActiveJob(newJob)
      markStepCompleted(0)
      setCurrentStep(1)
      clearScrapeEvents()
      navigate(`/job/${result.job_id}`, { replace: true })
      await jobs.startScrape(result.job_id)
      const updated = await jobs.get(result.job_id)
      setActiveJob(updated)
    },
  })

  const handleStepClick = useCallback(
    (step: number) => {
      if (completedSteps.has(step) || step === currentStep) {
        setCurrentStep(step)
      }
    },
    [completedSteps, currentStep, setCurrentStep],
  )

  const goToStep = useCallback(
    (step: number) => {
      markStepCompleted(step - 1)
      setCurrentStep(step)
    },
    [markStepCompleted, setCurrentStep],
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
    setCurrentStep(4)
    startExtractionMutation.mutate()
  }, [markStepCompleted, setCurrentStep, startExtractionMutation])

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
              onSubmit={(config, name) => createAndStartMutation.mutate({ config, name })}
              initialConfig={activeJob?.scrape_config}
              initialName={activeJob?.name ?? ''}
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
