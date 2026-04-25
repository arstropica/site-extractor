import { create } from 'zustand'
import type { SchemaField } from '@/api/client'

interface SchemaStore {
  // Schema being edited in the builder
  schemaId: string | null
  setSchemaId: (id: string | null) => void
  fields: SchemaField[]
  setFields: (fields: SchemaField[]) => void
  schemaName: string
  setSchemaName: (name: string) => void
  schemaDescription: string
  setSchemaDescription: (desc: string) => void

  // JSON editor mode toggle
  jsonMode: boolean
  setJsonMode: (mode: boolean) => void

  // Reset
  reset: () => void
}

export const useSchemaStore = create<SchemaStore>((set) => ({
  schemaId: null,
  setSchemaId: (id) => set({ schemaId: id }),
  fields: [],
  setFields: (fields) => set({ fields }),
  schemaName: '',
  setSchemaName: (name) => set({ schemaName: name }),
  schemaDescription: '',
  setSchemaDescription: (desc) => set({ schemaDescription: desc }),

  jsonMode: false,
  setJsonMode: (mode) => set({ jsonMode: mode }),

  reset: () =>
    set({
      schemaId: null,
      fields: [],
      schemaName: '',
      schemaDescription: '',
      jsonMode: false,
    }),
}))
