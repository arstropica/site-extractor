import { useState, useEffect, useRef } from 'react'

interface EditableTitleProps {
  value: string
  placeholder?: string
  onSave: (newValue: string) => Promise<void> | void
  className?: string
}

export default function EditableTitle({
  value,
  placeholder = 'Untitled',
  onSave,
  className = '',
}: EditableTitleProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [isEditing])

  const commit = async () => {
    const trimmed = draft.trim()
    if (trimmed === value.trim()) {
      setIsEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(trimmed)
      setIsEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const cancel = () => {
    setDraft(value)
    setIsEditing(false)
  }

  if (isEditing) {
    return (
      <input
        ref={inputRef}
        type="text"
        className={`input input-ghost text-2xl font-semibold p-0 h-auto focus:outline-none focus:bg-base-200/50 rounded px-2 ${className}`}
        value={draft}
        placeholder={placeholder}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          else if (e.key === 'Escape') cancel()
        }}
      />
    )
  }

  return (
    <button
      type="button"
      className={`group inline-flex items-center gap-2 text-2xl font-semibold hover:text-primary transition-colors ${className}`}
      onClick={() => setIsEditing(true)}
      title="Click to rename"
    >
      <span className={value ? '' : 'text-base-content/40'}>{value || placeholder}</span>
      <span className="icon-[tabler--pencil] size-4 opacity-0 group-hover:opacity-50 transition-opacity" />
    </button>
  )
}
