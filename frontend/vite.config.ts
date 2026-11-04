import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Served by FastAPI: / returns web/index.html, assets live under /web/.
  base: '/web/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: path.resolve(import.meta.dirname, '../web'),
    emptyOutDir: true,
  },
  server: {
    // Dev-only: the production build is served same-origin by FastAPI.
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/tasks': 'http://127.0.0.1:8000',
      '/chat': 'http://127.0.0.1:8000',
      '/reminders': 'http://127.0.0.1:8000',
      '/briefing': 'http://127.0.0.1:8000',
      '/memories': 'http://127.0.0.1:8000',
      '/consents': 'http://127.0.0.1:8000',
      '/approvals': 'http://127.0.0.1:8000',
      '/failures': 'http://127.0.0.1:8000',
      '/worker': 'http://127.0.0.1:8000',
      '/notifications': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
