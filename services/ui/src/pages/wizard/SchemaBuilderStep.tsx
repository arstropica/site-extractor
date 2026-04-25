import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { schemas as schemasApi, type SchemaField, type SchemaFieldType } from '@/api/client'
import { useSchemaStore } from '@/stores/schemaStore'
import { useJobStore } from '@/stores/jobStore'

interface SchemaBuilderStepProps {
  onContinue: () => void
}

function FieldEditor({
  field,
  depth,
  onUpdate,
  onRemove,
}: {
  field: SchemaField
  depth: number
  onUpdate: (updated: SchemaField) => void
  onRemove: () => void
}) {
  const maxDepth = 5

  const addChild = () => {
    const children = field.children || []
    onUpdate({
      ...field,
      children: [...children, { name: '', field_type: 'string', is_array: false, children: null }],
    })
  }

  const updateChild = (index: number, updated: SchemaField) => {
    const children = [...(field.children || [])]
    children[index] = updated
    onUpdate({ ...field, children })
  }

  const removeChild = (index: number) => {
    const children = [...(field.children || [])]
    children.splice(index, 1)
    onUpdate({ ...field, children: children.length ? children : null })
  }

  const typeIcons: Record<string, string> = {
    string: 'icon-[tabler--text-size]',
    number: 'icon-[tabler--hash]',
    image: 'icon-[tabler--photo]',
  }

  return (
    <div
      className={`border-l-2 pl-4 py-2 ${depth === 0 ? 'border-primary/30' : 'border-base-content/10'}`}
      style={{ marginLeft: depth > 0 ? 12 : 0 }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          className="input input-bordered input-sm w-36"
          placeholder="Field name"
          value={field.name}
          onChange={(e) => onUpdate({ ...field, name: e.target.value })}
        />
        <div className="flex items-center gap-1 bg-base-200 rounded-lg px-1">
          {(['string', 'number', 'image'] as SchemaFieldType[]).map((t) => (
            <button
              key={t}
              type="button"
              className={`btn btn-xs gap-1 ${field.field_type === t ? 'btn-primary' : 'btn-ghost text-base-content/50'}`}
              onClick={() => onUpdate({ ...field, field_type: t })}
              title={t}
            >
              <span className={`${typeIcons[t]} size-3`} />
              <span className="text-xs hidden sm:inline">{t}</span>
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            className="checkbox checkbox-xs checkbox-primary"
            checked={field.is_array}
            onChange={(e) => onUpdate({ ...field, is_array: e.target.checked })}
          />
          <span className="text-xs text-base-content/60">[]</span>
        </label>
        {depth < maxDepth && (
          <button
            type="button"
            className="btn btn-ghost btn-xs btn-square"
            onClick={addChild}
            title="Add nested field"
          >
            <span className="icon-[tabler--row-insert-bottom] size-3.5" />
          </button>
        )}
        <button
          type="button"
          className="btn btn-ghost btn-xs btn-square text-error"
          onClick={onRemove}
        >
          <span className="icon-[tabler--x] size-3.5" />
        </button>
      </div>

      {field.children?.map((child, i) => (
        <FieldEditor
          key={i}
          field={child}
          depth={depth + 1}
          onUpdate={(updated) => updateChild(i, updated)}
          onRemove={() => removeChild(i)}
        />
      ))}
    </div>
  )
}

export default function SchemaBuilderStep({ onContinue }: SchemaBuilderStepProps) {
  const { fields, setFields, schemaName, setSchemaName, schemaId, setSchemaId, jsonMode, setJsonMode } = useSchemaStore()
  const { activeJob } = useJobStore()
  const [jsonText, setJsonText] = useState('')
  const [jsonError, setJsonError] = useState<string | null>(null)

  const { data: savedSchemas } = useQuery({
    queryKey: ['schemas'],
    queryFn: () => schemasApi.list(),
    staleTime: 10000,
  })

  // Hydrate from the job's saved schema_id (one-time per job)
  const hydratedRef = useRef<string | null>(null)
  const jobSchemaId = activeJob?.extraction_config?.schema_id
  const { data: jobSchema } = useQuery({
    queryKey: ['schema', jobSchemaId],
    queryFn: () => schemasApi.get(jobSchemaId!),
    enabled: !!jobSchemaId,
    staleTime: 10000,
  })

  useEffect(() => {
    if (!activeJob || !jobSchema) return
    // Hydrate once per (jobId|schemaId) pair
    const key = `${activeJob.id}:${jobSchema.id}`
    if (hydratedRef.current === key) return
    hydratedRef.current = key
    setFields(jobSchema.fields)
    setSchemaName(jobSchema.name)
    setSchemaId(jobSchema.id)
  }, [activeJob, jobSchema, setFields, setSchemaName, setSchemaId])

  const addField = useCallback(() => {
    setFields([...fields, { name: '', field_type: 'string', is_array: false, children: null }])
  }, [fields, setFields])

  const updateField = useCallback(
    (index: number, updated: SchemaField) => {
      const next = [...fields]
      next[index] = updated
      setFields(next)
    },
    [fields, setFields],
  )

  const removeField = useCallback(
    (index: number) => {
      const next = [...fields]
      next.splice(index, 1)
      setFields(next)
    },
    [fields, setFields],
  )

  const loadSchema = useCallback(
    (id: string) => {
      const schema = savedSchemas?.schemas.find((s) => s.id === id)
      if (schema) {
        setFields(schema.fields)
        setSchemaName(schema.name)
        setSchemaId(schema.id)
      }
    },
    [savedSchemas, setFields, setSchemaName, setSchemaId],
  )

  const switchToJson = useCallback(() => {
    setJsonText(JSON.stringify(fields, null, 2))
    setJsonMode(true)
  }, [fields, setJsonMode])

  const switchToVisual = useCallback(() => {
    try {
      const parsed = JSON.parse(jsonText)
      setFields(parsed)
      setJsonError(null)
      setJsonMode(false)
    } catch (e) {
      setJsonError(String(e))
    }
  }, [jsonText, setFields, setJsonMode])

  const queryClient = useQueryClient()
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle')

  const saveMutation = useMutation({
    mutationFn: async () => {
      const name = schemaName.trim() || 'Untitled Schema'
      if (schemaId) {
        // Update existing schema
        await schemasApi.update(schemaId, { name, fields })
      } else {
        // Create new schema
        const created = await schemasApi.create({ name, fields })
        setSchemaId(created.id)
      }
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['schemas'] }),
  })

  const canContinue = fields.length > 0 && fields.every((f) => f.name.trim())

  return (
    <div className="space-y-6">
      {/* Schema name + load */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="form-control">
          <label className="label"><span className="label-text text-xs">Schema Name</span></label>
          <input
            type="text"
            className="input input-bordered input-sm"
            placeholder="My extraction schema"
            value={schemaName}
            onChange={(e) => setSchemaName(e.target.value)}
          />
        </div>
        {(savedSchemas?.schemas?.length ?? 0) > 0 && (
          <div className="form-control">
            <label className="label"><span className="label-text text-xs">Load Saved</span></label>
            <select
              className="select select-bordered select-sm"
              defaultValue=""
              onChange={(e) => e.target.value && loadSchema(e.target.value)}
            >
              <option value="" disabled>Select schema...</option>
              {savedSchemas!.schemas.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.is_template ? ' (template)' : ''}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Mode toggle */}
      <div className="flex items-center gap-1 bg-base-200/50 rounded-lg p-1 w-fit">
        <button
          type="button"
          className={`btn btn-sm gap-1.5 ${!jsonMode ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => jsonMode && switchToVisual()}
        >
          <span className="icon-[tabler--forms] size-4" />
          Visual
        </button>
        <button
          type="button"
          className={`btn btn-sm gap-1.5 ${jsonMode ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => !jsonMode && switchToJson()}
        >
          <span className="icon-[tabler--braces] size-4" />
          JSON
        </button>
      </div>

      {/* Builder / Editor */}
      <div className="bg-base-200/50 rounded-xl p-4">
        {jsonMode ? (
          <div className="space-y-2">
            <textarea
              className="textarea textarea-bordered w-full h-64 font-mono text-sm"
              value={jsonText}
              onChange={(e) => {
                setJsonText(e.target.value)
                setJsonError(null)
              }}
            />
            {jsonError && (
              <p className="text-error text-xs">{jsonError}</p>
            )}
          </div>
        ) : (
          <div className="space-y-1">
            {fields.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 gap-3 text-base-content/40">
                <span className="icon-[tabler--schema] size-12" />
                <p className="text-sm">No fields defined yet</p>
              </div>
            ) : (
              fields.map((field, i) => (
                <FieldEditor
                  key={i}
                  field={field}
                  depth={0}
                  onUpdate={(updated) => updateField(i, updated)}
                  onRemove={() => removeField(i)}
                />
              ))
            )}
            <button
              type="button"
              className="btn btn-ghost btn-sm gap-1.5 mt-3"
              onClick={addField}
            >
              <span className="icon-[tabler--plus] size-4" />
              Add Field
            </button>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between pt-2">
        <button
          className="btn btn-ghost btn-sm gap-1.5"
          disabled={!canContinue || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? (
            <span className="icon-[tabler--loader-2] size-4 animate-spin" />
          ) : saveStatus === 'saved' ? (
            <span className="icon-[tabler--check] size-4 text-success" />
          ) : (
            <span className="icon-[tabler--device-floppy] size-4" />
          )}
          {saveStatus === 'saved' ? 'Saved' : 'Save Schema'}
        </button>
        <button
          className="btn btn-primary gap-2"
          disabled={!canContinue}
          onClick={onContinue}
        >
          Continue to Mapping
          <span className="icon-[tabler--arrow-right] size-5" />
        </button>
      </div>
    </div>
  )
}
