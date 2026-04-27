/**
 * Horizontal wizard stepper. Renders each step's visual state from
 * a PipelineStages object (derived from the server's job record by
 * computePipelineStages) — the component never tracks its own
 * "completed" state.
 *
 * Step order maps to PipelineStages keys positionally via the parent's
 * STAGES list, so the stepper itself stays presentational.
 */

import type { PipelineStages, StageName, StageStatus } from '@/lib/pipelineStages'

interface Step {
  id: StageName
  label: string
  description?: string
}

interface StepperProps {
  steps: Step[]
  currentStep: number
  stages: PipelineStages
  /** 0.0–1.0 per step; only consulted while a stage is in_progress. */
  stepProgress?: Record<number, number>
  onStepClick?: (index: number) => void
}

/** Visual classification — collapses StageStatus down to what the renderer cares about. */
type Visual = 'complete' | 'warning' | 'failed' | 'in_progress' | 'current' | 'reachable' | 'locked'

function visualFor(
  status: StageStatus,
  isCurrent: boolean,
  isReachable: boolean,
): Visual {
  if (status === 'failed') return 'failed'
  if (status === 'complete') return 'complete'
  if (status === 'warning') return 'warning'
  if (status === 'in_progress') return 'in_progress'
  if (isCurrent) return 'current'
  return isReachable ? 'reachable' : 'locked'
}

const CIRCLE_CLASS: Record<Visual, string> = {
  complete:    'bg-success text-success-content',
  warning:     'bg-warning text-warning-content',
  failed:      'bg-error text-error-content',
  in_progress: 'bg-primary text-primary-content ring-4 ring-primary/20',
  current:     'bg-primary text-primary-content ring-4 ring-primary/20',
  reachable:   'bg-base-300 text-base-content/70 group-hover:bg-base-content/20',
  locked:      'bg-base-300/50 text-base-content/30',
}

const LABEL_CLASS: Record<Visual, string> = {
  complete:    'text-success',
  warning:     'text-warning',
  failed:      'text-error',
  in_progress: 'text-primary',
  current:     'text-primary',
  reachable:   'text-base-content/70',
  locked:      'text-base-content/30',
}

const CONNECTOR_CLASS: Record<Visual, string> = {
  complete:    'bg-success',
  warning:     'bg-warning',
  failed:      'bg-error',
  in_progress: 'bg-primary',
  current:     'bg-primary',
  reachable:   'bg-primary',
  locked:      'bg-primary',
}

export default function Stepper({
  steps,
  currentStep,
  stages,
  stepProgress,
  onStepClick,
}: StepperProps) {
  return (
    <div className="flex items-center w-full">
      {steps.map((step, i) => {
        const stageInfo = stages[step.id]
        const status = stageInfo.status
        const isCurrent = i === currentStep
        // A step is reachable if the previous step has been touched at all
        // (any non-pending status), or if it's the first step.
        const prevStatus = i > 0 ? stages[steps[i - 1].id].status : 'complete'
        const isReachable = i === 0 || prevStatus !== 'pending'

        const visual = visualFor(status, isCurrent, isReachable)
        const isAccessible = visual !== 'locked'
        const progress = stepProgress?.[i] ?? (visual === 'complete' || visual === 'warning' ? 1 : 0)

        return (
          <div key={step.id} className="flex items-center flex-1 last:flex-none">
            {/* Step circle + label */}
            <button
              className={`flex flex-col items-center gap-1.5 group shrink-0 ${
                isAccessible ? 'cursor-pointer' : 'cursor-default'
              }`}
              onClick={() => isAccessible && onStepClick?.(i)}
              disabled={!isAccessible}
              title={stageInfo.message ?? undefined}
            >
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-medium transition-all ${CIRCLE_CLASS[visual]}`}
              >
                {visual === 'failed' ? (
                  <span className="icon-[tabler--x] size-5" />
                ) : visual === 'complete' ? (
                  <span className="icon-[tabler--check] size-5" />
                ) : visual === 'warning' ? (
                  <span className="icon-[tabler--alert-triangle] size-5" />
                ) : visual === 'in_progress' ? (
                  <span className="icon-[tabler--loader-2] size-5 animate-spin" />
                ) : (
                  i + 1
                )}
              </div>
              <div className="text-center">
                <p className={`text-xs font-medium whitespace-nowrap ${LABEL_CLASS[visual]}`}>
                  {step.label}
                </p>
                {step.description && (
                  <p className="text-[10px] text-base-content/40 mt-0.5 hidden sm:block">
                    {step.description}
                  </p>
                )}
              </div>
            </button>

            {/* Connecting progress bar — fills based on the step we just rendered. */}
            {i < steps.length - 1 && (
              <div className="flex-1 mx-2 mt-[-1.25rem]">
                <div className="h-0.5 w-full bg-base-300/50 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${CONNECTOR_CLASS[visual]}`}
                    style={{
                      width: `${
                        (visual === 'complete' || visual === 'warning' ? 1 : progress) * 100
                      }%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
