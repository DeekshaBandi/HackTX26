import type { ApiStudent, Nearby, NearbyStudent } from '../lib/api.ts'

/** One pin on the people map: a real nearby student from the matching API. */
export type Student = {
  id: string
  label: string
  short: string
  color: string
  summary: string
  theme: string | null
  stage: string | null
  major: string | null
  year: string | null
  online: boolean
  synthetic: boolean
  /** Cosine similarity of the two embeddings, 0 to 1 */
  similarity: number
  /** similarity × 100, rounded, for labels and bars */
  score: number
  /** At or above the score matching needs before it connects two people */
  matchable: boolean
  x: number
  z: number
}

const COLORS = [
  '#d9652b', '#3c7a4a', '#b3403a', '#a67c2d', '#3d7a78',
  '#7a5b8a', '#6e6256', '#b5734a', '#4f6f8f', '#8a6248',
]

/** Similarity range the ring spans: the edge is 0.60, the center is 1.00. */
const RING_LOW = 0.6
const RING_HIGH = 1

function pinRadius(similarity: number): number {
  const t = Math.min(1, Math.max(0, (similarity - RING_LOW) / (RING_HIGH - RING_LOW)))
  return 46 - t * 24
}

export function shortId(id: string): string {
  return id.replace(/^(syn|ph|live)_/, '')
}

export function summaryOf(row: Pick<ApiStudent, 'situation' | 'theme'>): string {
  const parts = [row.situation, row.theme].filter((part): part is string => Boolean(part && part.trim()))
  return parts.length > 0 ? `${parts.join('. ')}.` : 'Nothing on the record yet.'
}

function toPin(row: NearbyStudent, threshold: number | null, color: string): Student {
  const digits = shortId(row.student_id)
  return {
    id: row.student_id,
    label: `Student · ${digits}`,
    short: digits,
    color,
    summary: summaryOf(row),
    theme: row.theme,
    stage: row.stage,
    major: row.major,
    year: row.year,
    online: row.online,
    synthetic: row.is_synthetic,
    similarity: row.score,
    score: Math.round(row.score * 100),
    matchable: threshold != null && row.score >= threshold,
    x: 0,
    z: 0,
  }
}

/** Highest score first, spread around the caller, closer to the center for a higher score. */
function place(rows: Student[]): Student[] {
  return rows.map((row, i) => {
    const angle = -Math.PI / 2 + (i / rows.length) * Math.PI * 2
    const radius = pinRadius(row.similarity)
    return { ...row, x: Math.cos(angle) * radius, z: Math.sin(angle) * radius }
  })
}

/** Top pins straight from GET /students/{id}/nearby, already sorted by the API. */
export function topPins(nearby: Nearby | null): Student[] {
  if (!nearby) return []
  const ranked = nearby.matches.map((row, i) => toPin(row, nearby.threshold, COLORS[i % COLORS.length]))
  return place(ranked)
}
