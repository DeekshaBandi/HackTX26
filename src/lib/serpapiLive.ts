import { CALL_QUERY } from './expand.ts'

export type SearchReceipt = {
  id: string
  status: string
  createdAt: string
  seconds: number | null
}

export type LiveEngine = {
  id: string
  label: string
  engine: string
  query: string
  phrases: string[]
  error: string | null
  receipt: SearchReceipt | null
}

/** Shared by every engine, and kept in this browser so a reload cannot start an 11th call. */
const CALL_BUDGET = 10
const BUDGET_KEY = 'aiedh-serp-budget'

const JOBS: { id: string; label: string; engine: string; query: string }[] = [
  { id: 'autocomplete', label: 'Google Autocomplete', engine: 'google_autocomplete', query: 'data struc' },
  { id: 'google', label: 'Google Search', engine: 'google', query: CALL_QUERY },
  { id: 'scholar', label: 'Google Scholar', engine: 'google_scholar', query: 'failing data structures computer science' },
  { id: 'news', label: 'Google News', engine: 'google_news', query: 'computer science students failing classes' },
  { id: 'youtube', label: 'YouTube', engine: 'youtube', query: 'failing data structures class computer science' },
  { id: 'bing', label: 'Bing', engine: 'bing', query: 'data structures' },
  { id: 'duckduckgo', label: 'DuckDuckGo', engine: 'duckduckgo', query: 'failing data structures' },
  { id: 'reddit', label: 'Reddit', engine: 'reddit', query: 'failing data structures' },
]

function callsSpent(): number {
  try {
    const n = Number(localStorage.getItem(BUDGET_KEY) || '0')
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
  } catch {
    return 0
  }
}

/** Reserves one counted SerpAPI search. Cached searches do not move the plan counter, so these are uncached. */
function reserveCall(): boolean {
  const used = callsSpent()
  if (used >= CALL_BUDGET) return false
  try {
    localStorage.setItem(BUDGET_KEY, String(used + 1))
  } catch {
    /* still stop this page at the same number */
  }
  return true
}

export function serpCallBudget(): { used: number; limit: number } {
  return { used: callsSpent(), limit: CALL_BUDGET }
}

function fieldText(item: unknown, field: string): string | null {
  if (typeof item === 'string') {
    const trimmed = item.trim()
    return trimmed.length > 0 ? trimmed : null
  }
  if (!item || typeof item !== 'object') return null
  const value = (item as Record<string, unknown>)[field]
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

/** Short queries the engines suggested. Article titles are not expansion phrases. */
function phrasesFrom(body: Record<string, unknown>, query: string): string[] {
  const buckets: [unknown, string][] = [
    [body.suggestions, 'value'],
    [body.related_searches, 'query'],
    [body.related_questions, 'question'],
  ]
  const phrases: string[] = []
  const skipped = new Set([query.toLowerCase(), CALL_QUERY.toLowerCase(), 'data struc'])
  for (const [bucket, field] of buckets) {
    if (!Array.isArray(bucket)) continue
    for (const item of bucket) {
      const text = fieldText(item, field)
      if (!text || text.length < 4 || text.length > 70) continue
      if (skipped.has(text.toLowerCase())) continue
      if (phrases.some((phrase) => phrase.toLowerCase() === text.toLowerCase())) continue
      phrases.push(text)
      if (phrases.length >= 6) return phrases
    }
  }
  return phrases
}

function publicError(message: string): string {
  return message.replace(/api_key=[^&\s"]+/gi, 'api_key=hidden')
}

function receiptFrom(body: Record<string, unknown>): SearchReceipt | null {
  const meta = body.search_metadata
  if (!meta || typeof meta !== 'object') return null
  const row = meta as Record<string, unknown>
  const id = typeof row.id === 'string' ? row.id : ''
  const status = typeof row.status === 'string' ? row.status : ''
  const createdAt = typeof row.created_at === 'string' ? row.created_at : ''
  const seconds = typeof row.total_time_taken === 'number' ? row.total_time_taken : null
  if (!id && !status) return null
  return { id, status, createdAt, seconds }
}

async function ask(engine: string, query: string): Promise<Record<string, unknown>> {
  if (!reserveCall()) {
    throw new Error(`Stopped at ${CALL_BUDGET} SerpAPI calls`)
  }
  const params = new URLSearchParams({ engine, q: query, no_cache: 'true' })
  const response = await fetch(`/api/serpapi?${params}`, { signal: AbortSignal.timeout(20000) })
  return (await response.json()) as Record<string, unknown>
}

async function oneEngine(job: (typeof JOBS)[number]): Promise<LiveEngine> {
  try {
    let engine = job.engine
    let query = job.query
    let body = await ask(engine, query)
    if (job.id === 'reddit' && typeof body.error === 'string' && body.error.includes('Unsupported') && callsSpent() < CALL_BUDGET) {
      engine = 'google'
      query = `site:reddit.com ${job.query}`
      body = await ask(engine, query)
    }
    const receipt = receiptFrom(body)
    if (typeof body.error === 'string') {
      return { ...job, engine, query, phrases: [], error: publicError(body.error), receipt }
    }
    const phrases = phrasesFrom(body, query)
    return {
      ...job,
      engine,
      query,
      phrases,
      error: phrases.length === 0 ? 'No expansion phrases in the response.' : null,
      receipt,
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Request failed'
    return { ...job, phrases: [], error: publicError(message), receipt: null }
  }
}

export function phrasesFromEngines(rows: LiveEngine[]): { text: string; source: string }[] {
  const seen = new Set<string>()
  const phrases: { text: string; source: string }[] = []
  for (const row of rows) {
    for (const text of row.phrases) {
      const key = text.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      phrases.push({ text, source: row.label })
    }
  }
  return phrases
}

export const SEARCH_STEPS = JOBS.map((job) => ({ id: job.id, label: job.label }))

let inflight: Promise<LiveEngine[]> | null = null

/** Live SerpAPI only. One batch per page, and that batch cannot exceed 10 calls. */
export function runSerpApiExpansion(onStep?: (row: LiveEngine) => void): Promise<LiveEngine[]> {
  if (!inflight) {
    inflight = Promise.all(JOBS.map((job) => oneEngine(job)))
  }
  return inflight.then((rows) => {
    rows.forEach((row) => onStep?.(row))
    return rows
  })
}
