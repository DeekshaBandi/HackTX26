import { loadEnv, defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

function serpapiDevProxy(): Plugin {
  return {
    name: 'serpapi-dev-proxy',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.url ?? ''
        if (!url.startsWith('/api/serpapi')) return next()
        const env = loadEnv(server.config.mode, server.config.root, '')
        const apiKey = (env.VITE_SERPAPI_KEY || '').replace(/^"|"$/g, '')
        if (!apiKey) {
          res.statusCode = 500
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ error: 'VITE_SERPAPI_KEY is empty in .env' }))
          return
        }
        const incoming = new URL(url, 'http://localhost')
        const engine = incoming.searchParams.get('engine') || 'google'
        const query = incoming.searchParams.get('q') || ''
        const target = new URL('https://serpapi.com/search.json')
        target.searchParams.set('engine', engine)
        target.searchParams.set('api_key', apiKey)
        if (engine === 'youtube') target.searchParams.set('search_query', query)
        else target.searchParams.set('q', query)
        const noCache = incoming.searchParams.get('no_cache')
        if (noCache) target.searchParams.set('no_cache', noCache)
        try {
          const response = await fetch(target)
          const text = await response.text()
          res.statusCode = response.status
          res.setHeader('content-type', 'application/json')
          res.end(text)
        } catch (err) {
          res.statusCode = 502
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ error: err instanceof Error ? err.message : 'SerpAPI request failed' }))
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), serpapiDevProxy()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
