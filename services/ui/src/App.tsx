import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { QueryClient, QueryClientProvider, MutationCache } from '@tanstack/react-query'
import { useState } from 'react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useJobStore } from '@/stores/jobStore'
import { Toaster, useToast } from '@/components/Toaster'
import HistoryPage from '@/pages/HistoryPage'
import SchemasPage from '@/pages/SchemasPage'
import WizardPage from '@/pages/wizard/WizardPage'

// QueryClient is created inside the provider so MutationCache can call useToast().
function makeQueryClient(toast: ReturnType<typeof useToast>) {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 5000, refetchOnWindowFocus: false },
    },
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        // Only surface a toast if the mutation didn't define its own onError
        if (mutation.options.onError) return
        const msg = error instanceof Error ? error.message : String(error)
        toast.error('Request failed', msg)
      },
    }),
  })
}

function SidebarLink({ to, icon, label }: { to: string; icon: string; label: string }) {
  return (
    <li>
      <NavLink
        to={to}
        end
        className={({ isActive }) =>
          `flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm transition-colors ${
            isActive
              ? 'bg-primary/10 text-primary font-medium'
              : 'text-base-content/70 hover:bg-base-content/5 hover:text-base-content'
          }`
        }
      >
        <span className={`${icon} size-5`} />
        <span>{label}</span>
      </NavLink>
    </li>
  )
}

function AppContent() {
  const handleWSEvent = useJobStore((s) => s.handleWSEvent)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const { connected } = useWebSocket(handleWSEvent)

  return (
    <div data-theme="dark" className="bg-base-200 flex min-h-screen">
      {/* Sidebar */}
      <aside
        className={`
          fixed inset-y-0 start-0 z-50 w-64 border-e border-base-content/10
          bg-base-100 transition-transform duration-300
          lg:translate-x-0 lg:static lg:z-auto
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center gap-3 px-6 py-5 border-b border-base-content/10">
            <span className="icon-[tabler--spider] size-7 text-primary" />
            <div>
              <h1 className="text-base font-semibold">Site Extractor</h1>
              <p className="text-xs text-base-content/50">Spider & Extract</p>
            </div>
          </div>

          {/* Navigation */}
          <nav className="flex-1 overflow-y-auto p-4">
            <p className="text-xs text-base-content/40 uppercase tracking-wider font-medium px-4 mb-2">
              Main
            </p>
            <ul className="space-y-1 mb-6">
              <SidebarLink to="/" icon="icon-[tabler--history]" label="Job History" />
              <SidebarLink to="/schemas" icon="icon-[tabler--schema]" label="Schemas" />
            </ul>

            <p className="text-xs text-base-content/40 uppercase tracking-wider font-medium px-4 mb-2">
              Actions
            </p>
            <ul className="space-y-1">
              <SidebarLink to="/job/new" icon="icon-[tabler--plus]" label="New Job" />
            </ul>
          </nav>

          {/* Version */}
          <div className="px-6 py-4 border-t border-base-content/10">
            <p className="text-xs text-base-content/30">v0.1.0</p>
          </div>
        </div>
      </aside>

      {/* Overlay for mobile sidebar */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-base-content/10 bg-base-100 px-6 h-14">
          <button
            className="btn btn-sm btn-square btn-ghost lg:hidden"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            <span className="icon-[tabler--menu-2] size-5" />
          </button>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <span
              className={`badge badge-soft badge-sm ${connected ? 'badge-success' : 'badge-neutral'}`}
            >
              {connected ? 'Connected' : 'Connecting...'}
            </span>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-6">
          <div className="mx-auto w-full max-w-[1400px] space-y-6">
            <Routes>
              <Route path="/" element={<HistoryPage />} />
              <Route path="/schemas" element={<SchemasPage />} />
              <Route path="/job/:jobId" element={<WizardPage />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  )
}

function QueryProviderWithToast({ children }: { children: React.ReactNode }) {
  const toast = useToast()
  // Memoize so we don't re-create the QueryClient on every render
  const [client] = useState(() => makeQueryClient(toast))
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

export default function App() {
  return (
    <Toaster>
      <QueryProviderWithToast>
        <BrowserRouter>
          <AppContent />
        </BrowserRouter>
      </QueryProviderWithToast>
    </Toaster>
  )
}
