/**
 * The matching API (matching/api.py). Every request the judge site makes goes through here.
 * Set VITE_API_URL in .env to point at the deployed server.
 */
export const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

export type Xyz = [number, number, number]

export type ApiStudent = {
  student_id: string
  name: string | null
  school: string | null
  major: string | null
  year: string | null
  situation: string | null
  stage: string | null
  interests: string | null
  theme: string | null
  online: boolean
  is_synthetic: boolean
  xyz: Xyz | null
}

export type NearbyStudent = ApiStudent & { score: number }

export type Nearby = {
  student_id: string
  embedder: 'snowflake' | 'local' | null
  threshold: number | null
  matches: NearbyStudent[]
  live_match: NearbyStudent | null
}

export type CallEvent = {
  time: string
  call_id: string | null
  student_id: string
  event: string
  theme: string | null
  detail: Record<string, unknown> | null
  value: number | null
}

export type Pair = {
  a: string
  b: string
  score: number
  method: string
  created_at: string
}

export type StudentMatch = {
  match_id: string
  score: number
  method: string
  intro: string | null
  created_at: string
  other_id: string
  other_situation: string | null
  other_stage: string | null
  chat_id: string | null
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { signal: AbortSignal.timeout(20000) })
  if (!response.ok) throw new Error(`${path} returned ${response.status}`)
  return (await response.json()) as T
}

export const api = {
  students: () => get<ApiStudent[]>('/students'),
  student: (id: string) => get<ApiStudent>(`/students/${encodeURIComponent(id)}`),
  nearby: (id: string, k = 10) => get<Nearby>(`/students/${encodeURIComponent(id)}/nearby?k=${k}`),
  matches: (id: string) => get<StudentMatch[]>(`/students/${encodeURIComponent(id)}/matches`),
  events: (minutes: number) => get<CallEvent[]>(`/events?minutes=${minutes}`),
  pairs: () => get<Pair[]>('/pairs'),
}

export type SerpReceipt = {
  id: string
  status: string
  created_at: string
  seconds: number | null
}

export type SerpEngine = {
  id: string
  label: string
  engine: string
  query: string
  phrases: string[]
  error: string | null
  receipt: SerpReceipt | null
  cached: boolean
}

export type Expansion = {
  student_id: string
  /** The scrubbed situation phrase the server searched for */
  query: string
  configured: boolean
  engines: SerpEngine[]
  calls_used: number
  calls_limit: number
}

/** SerpApi runs on the API server, so the key never reaches this page. */
export const serp = {
  expansion: (id: string) => get<Expansion>(`/students/${encodeURIComponent(id)}/expansion`),
}
