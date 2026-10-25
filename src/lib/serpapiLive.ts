import { serp, type Expansion, type SerpEngine } from './api.ts'

export type LiveEngine = SerpEngine

/** Every phrase the engines returned, once each, tagged with the engine it came from. */
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

const inflight = new Map<string, Promise<Expansion>>()

/**
 * Live SerpApi expansion for one student, through GET /students/{id}/expansion.
 * The API server holds the key, caches repeat searches, and caps the calls per run.
 * One request per student and situation per page load.
 */
export function runSerpApiExpansion(studentId: string, situation: string | null): Promise<Expansion> {
  const key = `${studentId}|${situation ?? ''}`
  let request = inflight.get(key)
  if (!request) {
    request = serp.expansion(studentId)
    request.catch(() => inflight.delete(key))
    inflight.set(key, request)
  }
  return request
}
