/**
 * Global toast notification system using Radix Toast.
 *
 * Usage:
 *   const toast = useToast()
 *   toast.error('Failed to save', detail?: string)
 *   toast.success('Saved')
 *   toast.info('...')
 *
 * Wrap the app in <Toaster> once near the root.
 */
import * as RToast from '@radix-ui/react-toast'
import { createContext, useCallback, useContext, useState } from 'react'
import type { ReactNode } from 'react'

type ToastKind = 'error' | 'success' | 'info' | 'warning'

interface ToastItem {
  id: number
  kind: ToastKind
  title: string
  description?: string
}

interface ToastApi {
  show: (kind: ToastKind, title: string, description?: string) => void
  error: (title: string, description?: string) => void
  success: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const KIND_STYLES: Record<ToastKind, { border: string; icon: string; iconColor: string }> = {
  error: { border: 'border-error/30 bg-error/10', icon: 'icon-[tabler--alert-circle]', iconColor: 'text-error' },
  success: { border: 'border-success/30 bg-success/10', icon: 'icon-[tabler--circle-check]', iconColor: 'text-success' },
  info: { border: 'border-info/30 bg-info/10', icon: 'icon-[tabler--info-circle]', iconColor: 'text-info' },
  warning: { border: 'border-warning/30 bg-warning/10', icon: 'icon-[tabler--alert-triangle]', iconColor: 'text-warning' },
}

export function Toaster({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const remove = useCallback((id: number) => {
    setItems((cur) => cur.filter((t) => t.id !== id))
  }, [])

  const show = useCallback((kind: ToastKind, title: string, description?: string) => {
    const id = Date.now() + Math.random()
    setItems((cur) => [...cur, { id, kind, title, description }])
  }, [])

  const api: ToastApi = {
    show,
    error: (t, d) => show('error', t, d),
    success: (t, d) => show('success', t, d),
    info: (t, d) => show('info', t, d),
    warning: (t, d) => show('warning', t, d),
  }

  return (
    <ToastContext.Provider value={api}>
      <RToast.Provider swipeDirection="right" duration={5000}>
        {children}
        {items.map((item) => {
          const styles = KIND_STYLES[item.kind]
          return (
            <RToast.Root
              key={item.id}
              onOpenChange={(open) => { if (!open) remove(item.id) }}
              className={`flex items-start gap-3 p-3 rounded-xl shadow-lg border ${styles.border} backdrop-blur bg-base-100/95 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-right-full`}
            >
              <span className={`${styles.icon} size-5 shrink-0 mt-0.5 ${styles.iconColor}`} />
              <div className="flex-1 min-w-0">
                <RToast.Title className="text-sm font-medium">{item.title}</RToast.Title>
                {item.description && (
                  <RToast.Description className="text-xs text-base-content/60 mt-0.5 break-words">
                    {item.description}
                  </RToast.Description>
                )}
              </div>
              <RToast.Close className="btn btn-ghost btn-xs btn-square shrink-0">
                <span className="icon-[tabler--x] size-3" />
              </RToast.Close>
            </RToast.Root>
          )
        })}
        <RToast.Viewport className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 w-96 max-w-[calc(100vw-2rem)] outline-none" />
      </RToast.Provider>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used inside <Toaster>')
  }
  return ctx
}
