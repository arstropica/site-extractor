import { defineConfig } from 'vitest/config'
import path from 'path'

// Test-only config so vite.config.ts stays free of vitest's surface.
// Mirrors the path alias from vite.config.ts so imports like '@/lib/...'
// resolve identically in tests.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
