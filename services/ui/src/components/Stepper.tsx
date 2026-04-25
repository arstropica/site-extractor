/**
 * Horizontal wizard stepper with numbered circles, progress-bar connectors,
 * and active/completed/pending/failed states.
 *
 * Matches the reaction-maker Stepper component styling.
 */

interface Step {
  id: string
  label: string
  description?: string
}

interface StepperProps {
  steps: Step[]
  currentStep: number
  completedSteps: Set<number>
  failedSteps?: Set<number>
  stepProgress?: Record<number, number> // 0.0–1.0 per step
  onStepClick?: (index: number) => void
}

export default function Stepper({
  steps,
  currentStep,
  completedSteps,
  failedSteps,
  stepProgress,
  onStepClick,
}: StepperProps) {
  return (
    <div className="flex items-center w-full">
      {steps.map((step, i) => {
        const isCompleted = completedSteps.has(i)
        const isFailed = failedSteps?.has(i)
        const isCurrent = i === currentStep
        const isAccessible = isCompleted || isCurrent || isFailed || completedSteps.has(i - 1)
        const progress = stepProgress?.[i] ?? (isCompleted ? 1 : 0)

        return (
          <div key={step.id} className="flex items-center flex-1 last:flex-none">
            {/* Step circle + label */}
            <button
              className={`flex flex-col items-center gap-1.5 group shrink-0 ${
                isAccessible ? 'cursor-pointer' : 'cursor-default'
              }`}
              onClick={() => isAccessible && onStepClick?.(i)}
              disabled={!isAccessible}
            >
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-medium transition-all ${
                  isFailed
                    ? 'bg-error text-error-content'
                    : isCompleted
                      ? 'bg-success text-success-content'
                      : isCurrent
                        ? 'bg-primary text-primary-content ring-4 ring-primary/20'
                        : isAccessible
                          ? 'bg-base-300 text-base-content/70 group-hover:bg-base-content/20'
                          : 'bg-base-300/50 text-base-content/30'
                }`}
              >
                {isFailed ? (
                  <span className="icon-[tabler--x] size-5" />
                ) : isCompleted ? (
                  <span className="icon-[tabler--check] size-5" />
                ) : isCurrent && progress > 0 && progress < 1 ? (
                  <span className="icon-[tabler--loader-2] size-5 animate-spin" />
                ) : (
                  i + 1
                )}
              </div>
              <div className="text-center">
                <p
                  className={`text-xs font-medium whitespace-nowrap ${
                    isFailed
                      ? 'text-error'
                      : isCurrent
                        ? 'text-primary'
                        : isCompleted
                          ? 'text-success'
                          : isAccessible
                            ? 'text-base-content/70'
                            : 'text-base-content/30'
                  }`}
                >
                  {step.label}
                </p>
                {step.description && (
                  <p className="text-[10px] text-base-content/40 mt-0.5 hidden sm:block">
                    {step.description}
                  </p>
                )}
              </div>
            </button>

            {/* Connecting progress bar */}
            {i < steps.length - 1 && (
              <div className="flex-1 mx-2 mt-[-1.25rem]">
                <div className="h-0.5 w-full bg-base-300/50 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      isFailed ? 'bg-error' : isCompleted ? 'bg-success' : 'bg-primary'
                    }`}
                    style={{ width: `${(isCompleted ? 1 : progress) * 100}%` }}
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
