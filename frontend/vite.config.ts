import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In development the dashboard talks to the API through this proxy, so the browser only ever sees its own
// origin: no CORS, and the same relative paths work behind a reverse proxy in production.
const API = process.env.AEGIS_API ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: true, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
  test: { environment: 'jsdom', globals: true },
})
