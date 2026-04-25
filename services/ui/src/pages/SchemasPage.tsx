import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { schemas, type Schema } from '@/api/client'

export default function SchemasPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [isTemplate, setIsTemplate] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['schemas'],
    queryFn: () => schemas.list(),
    staleTime: 5000,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      schemas.create({ name: newName, description: newDescription, is_template: isTemplate }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schemas'] })
      setShowCreate(false)
      setNewName('')
      setNewDescription('')
      setIsTemplate(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => schemas.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schemas'] }),
  })

  const schemaList = data?.schemas ?? []

  return (
    <>
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Extraction Schemas</h2>
          <p className="text-sm text-base-content/50 mt-1">{schemaList.length} schemas</p>
        </div>
        <button className="btn btn-primary gap-2" onClick={() => setShowCreate(true)}>
          <span className="icon-[tabler--plus] size-5" />
          New Schema
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card shadow-base-300/10 shadow-md">
          <div className="card-body">
            <h3 className="text-lg font-semibold">Create Schema</h3>
            <div className="grid gap-4 mt-2">
              <div className="form-control">
                <label className="label">
                  <span className="label-text text-xs">Name</span>
                </label>
                <input
                  type="text"
                  placeholder="My extraction schema"
                  className="input input-bordered"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="form-control">
                <label className="label">
                  <span className="label-text text-xs">Description</span>
                </label>
                <input
                  type="text"
                  placeholder="Optional description"
                  className="input input-bordered"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                />
              </div>
              <label className="label cursor-pointer justify-start gap-3">
                <input
                  type="checkbox"
                  className="toggle toggle-primary toggle-sm"
                  checked={isTemplate}
                  onChange={(e) => setIsTemplate(e.target.checked)}
                />
                <span className="label-text text-sm">Save as template</span>
              </label>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button className="btn btn-ghost btn-sm" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button
                className="btn btn-primary btn-sm"
                disabled={!newName.trim() || createMutation.isPending}
                onClick={() => createMutation.mutate()}
              >
                {createMutation.isPending && (
                  <span className="icon-[tabler--loader-2] size-4 animate-spin" />
                )}
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Schema list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <span className="icon-[tabler--loader-2] size-8 animate-spin text-base-content/30" />
        </div>
      ) : schemaList.length === 0 ? (
        <div className="card shadow-base-300/10 shadow-md">
          <div className="card-body flex flex-col items-center justify-center py-16 gap-4 text-base-content/50">
            <span className="icon-[tabler--schema] size-16" />
            <div className="text-center">
              <p className="text-lg font-medium">No schemas yet</p>
              <p className="text-sm mt-1">Create one to define extraction structure.</p>
            </div>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {schemaList.map((schema: Schema) => (
            <div key={schema.id} className="card shadow-base-300/10 shadow-md group">
              <div className="card-body p-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="icon-[tabler--schema] size-5 text-primary shrink-0" />
                      <h3 className="font-semibold truncate">{schema.name}</h3>
                    </div>
                    {schema.description && (
                      <p className="text-sm text-base-content/60 mt-1.5 line-clamp-2">
                        {schema.description}
                      </p>
                    )}
                  </div>
                  {schema.is_template && (
                    <span className="badge badge-soft badge-sm badge-accent shrink-0">template</span>
                  )}
                </div>

                <div className="flex items-center justify-between mt-4 pt-3 border-t border-base-content/5">
                  <span className="text-xs text-base-content/40">
                    {schema.fields.length} field{schema.fields.length !== 1 ? 's' : ''}
                    <span className="mx-1.5">·</span>
                    {new Date(schema.updated_at).toLocaleDateString()}
                  </span>
                  <button
                    className="btn btn-ghost btn-xs btn-square opacity-0 group-hover:opacity-100 transition-opacity text-error"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm('Delete this schema?')) {
                        deleteMutation.mutate(schema.id)
                      }
                    }}
                  >
                    <span className="icon-[tabler--trash] size-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
