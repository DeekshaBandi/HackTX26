import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// SerpApi calls go through the matching API (GET /students/{id}/expansion), which holds the key.
export default defineConfig(({ mode }) => {
  // The live transcript panel talks to the phone server (server.py) through this proxy.
  // DASHBOARD_TOKEN stays here on the dev server, never in the browser bundle.
  const env = loadEnv(mode, '.', '')
  const phoneServer = env.PHONE_SERVER_URL || 'http://127.0.0.1:8000'
  const token = (env.DASHBOARD_TOKEN || '').replace(/^"|"$/g, '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/dashboard': {
          target: phoneServer,
          ws: true,
          rewrite: (path) => (token ? `${path.split('?')[0]}?token=${encodeURIComponent(token)}` : path),
        },
      },
    },
  }
})
