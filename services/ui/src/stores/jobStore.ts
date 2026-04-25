import { create } from 'zustand'
import type { JobDetail, ScrapeConfig, WSEvent } from '@/api/client'

interface ScrapeEvent {
  type: string
  data: Record<string, unknown>
  timestamp: string
}

interface JobStore {
  // Active job being viewed/edited in the wizard
  activeJob: JobDetail | null
  setActiveJob: (job: JobDetail | null) => void
  updateActiveJob: (updates: Partial<JobDetail>) => void

  // Current wizard step (0-indexed)
  currentStep: number
  setCurrentStep: (step: number) => void
  completedSteps: Set<number>
  markStepCompleted: (step: number) => void

  // Draft config used to prefill /job/new (e.g., from a "clone" click in
  // history). Consumed and cleared by the wizard on mount.
  draftConfig: ScrapeConfig | null
  draftName: string | null
  setDraft: (config: ScrapeConfig | null, name: string | null) => void

  // Real-time scrape events
  scrapeEvents: ScrapeEvent[]
  addScrapeEvent: (event: ScrapeEvent) => void
  clearScrapeEvents: () => void

  // Handle WebSocket events
  handleWSEvent: (event: WSEvent) => void
}

export const useJobStore = create<JobStore>((set, get) => ({
  activeJob: null,
  setActiveJob: (job) => set({ activeJob: job }),
  updateActiveJob: (updates) =>
    set((state) => ({
      activeJob: state.activeJob ? { ...state.activeJob, ...updates } : null,
    })),

  currentStep: 0,
  setCurrentStep: (step) => set({ currentStep: step }),
  completedSteps: new Set<number>(),
  markStepCompleted: (step) =>
    set((state) => {
      const next = new Set(state.completedSteps)
      next.add(step)
      return { completedSteps: next }
    }),

  draftConfig: null,
  draftName: null,
  setDraft: (config, name) => set({ draftConfig: config, draftName: name }),

  scrapeEvents: [],
  addScrapeEvent: (event) =>
    set((state) => ({
      scrapeEvents: [...state.scrapeEvents.slice(-500), event],
    })),
  clearScrapeEvents: () => set({ scrapeEvents: [] }),

  handleWSEvent: (event) => {
    const { activeJob } = get()
    if (!activeJob || event.job_id !== activeJob.id) return

    const data = event.data

    // Update progress (pages and resources tracked separately)
    if (event.type === 'SCRAPE_PROGRESS') {
      const pagesTotal = (data.pages_total as number) ?? activeJob.pages_discovered
      const pagesDone = (data.pages_done as number) ?? activeJob.pages_downloaded
      const resTotal = (data.resources_total as number) ?? activeJob.resources_discovered
      const resDone = (data.resources_done as number) ?? activeJob.resources_downloaded
      const totalAll = pagesTotal + resTotal
      const doneAll = pagesDone + resDone
      get().updateActiveJob({
        pages_discovered: pagesTotal,
        pages_downloaded: pagesDone,
        resources_discovered: resTotal,
        resources_downloaded: resDone,
        bytes_downloaded: (data.bytes_downloaded as number) ?? activeJob.bytes_downloaded,
        progress: totalAll > 0 ? (doneAll / totalAll) * 100 : activeJob.progress,
      })
    }

    // Update status
    if (event.type === 'SCRAPE_STATUS' || event.type === 'EXTRACTION_STATUS') {
      const status = data.status as string
      if (status) {
        const updates: Partial<JobDetail> = { status: status as JobDetail['status'] }
        // SCRAPE_STATUS=scraped includes final counters
        if (event.type === 'SCRAPE_STATUS' && status === 'scraped') {
          if (typeof data.pages_discovered === 'number') updates.pages_discovered = data.pages_discovered
          if (typeof data.pages_downloaded === 'number') updates.pages_downloaded = data.pages_downloaded
          if (typeof data.resources_discovered === 'number') updates.resources_discovered = data.resources_discovered
          if (typeof data.resources_downloaded === 'number') updates.resources_downloaded = data.resources_downloaded
          if (typeof data.bytes_downloaded === 'number') updates.bytes_downloaded = data.bytes_downloaded
        }
        get().updateActiveJob(updates)
        if (status === 'scraped') {
          get().markStepCompleted(1)
        } else if (status === 'completed') {
          get().markStepCompleted(4)
        }
      }
    }

    // Record event for the monitor
    get().addScrapeEvent({
      type: event.type,
      data: event.data,
      timestamp: event.timestamp || new Date().toISOString(),
    })
  },
}))
