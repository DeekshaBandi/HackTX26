import { matchParts, type MatchParts } from '../lib/expand.ts'
import { ROSTER, type RosterStudent } from './roster.ts'

export type Parts = {
  topic: number
  emotion: number
  ask: number
}

export type Reason = {
  label: string
  detail: string
}

export type Student = {
  id: string
  label: string
  short: string
  color: string
  calledOn: string
  summary: string
  parts: Parts
  why: Reason[]
  score: number
  x: number
  z: number
}

const COLORS = [
  '#d9652b', '#3c7a4a', '#b3403a', '#a67c2d', '#3d7a78',
  '#7a5b8a', '#6e6256', '#b5734a', '#4f6f8f', '#8a6248',
]

const DAYS = [
  'Sun, Oct 18, 2026',
  'Mon, Oct 19, 2026',
  'Tue, Oct 20, 2026',
  'Wed, Oct 21, 2026',
  'Thu, Oct 22, 2026',
  'Fri, Oct 23, 2026',
  'Sat, Oct 24, 2026',
]

export function compatibility(parts: Parts): number {
  const raw = 0.5 * parts.topic + 0.3 * parts.emotion + 0.2 * parts.ask
  return Math.round(raw * 100)
}

export function scoreMath(parts: Parts): string {
  const raw = 0.5 * parts.topic + 0.3 * parts.emotion + 0.2 * parts.ask
  const n = (v: number) => v.toFixed(2)
  return `0.5 × ${n(parts.topic)}  +  0.3 × ${n(parts.emotion)}  +  0.2 × ${n(parts.ask)}  =  ${raw.toFixed(2)}  →  ${Math.round(raw * 100)}`
}

function pinRadius(score: number): number {
  const t = Math.min(1, Math.max(0, (score - 25) / 69))
  return 46 - t * 24
}

function calledOn(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i += 1) hash = (hash * 33 + id.charCodeAt(i)) >>> 0
  return DAYS[hash % DAYS.length]
}

function whyFor(row: RosterStudent, parts: MatchParts, useExpansion: boolean): Reason[] {
  const said = parts.said.length > 0
    ? `The record contains ${parts.said.join(', ')}.`
    : 'None of the finished words appear on this record.'
  let feeling: string
  if (!useExpansion) feeling = 'Expansion is off, so the related phrases are not counted.'
  else if (parts.phrases.length > 0) feeling = `Related phrases on the record: ${parts.phrases.join('; ')}.`
  else feeling = 'None of the expanded phrases landed on this record.'
  const ask = row.major === 'Computer Science'
    ? 'Same major the caller asked for. Name and school stay off this screen.'
    : `${row.major}. Not the computer science peer they asked for. Name and school stay off this screen.`
  return [
    { label: 'Words they said', detail: said },
    { label: 'Expanded phrases', detail: feeling },
    { label: 'Same major', detail: ask },
  ]
}

function toScored(row: RosterStudent, useExpansion: boolean, color: string): Student {
  const parts = matchParts(row, useExpansion)
  const digits = row.student_id.replace('syn_', '')
  return {
    id: row.student_id,
    label: `Student · ${digits}`,
    short: digits,
    color,
    calledOn: calledOn(row.student_id),
    summary: `${row.situation} ${row.theme}.`,
    parts,
    why: whyFor(row, parts, useExpansion),
    score: compatibility(parts),
    x: 0,
    z: 0,
  }
}

function place(rows: Student[]): Student[] {
  const ranked = [...rows].sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
  const pos = new Map<string, { x: number; z: number }>()
  ranked.forEach((row, i) => {
    const angle = -Math.PI / 2 + (i / ranked.length) * Math.PI * 2
    const radius = pinRadius(row.score)
    pos.set(row.id, {
      x: Math.cos(angle) * radius,
      z: Math.sin(angle) * radius,
    })
  })
  return ranked.map((row) => {
    const at = pos.get(row.id)!
    return { ...row, x: at.x, z: at.z }
  })
}

/** Top 10 pins for the map. Scores come from the 1,000-row file. */
export function topPins(useExpansion: boolean): Student[] {
  const ranked = ROSTER
    .map((row) => toScored(row, useExpansion, '#6e6256'))
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
    .slice(0, 10)
    .map((row, i) => ({ ...row, color: COLORS[i % COLORS.length] }))
  return place(ranked)
}

export const BEST: Student = topPins(true)[0]

export const CALLER = {
  id: 'you',
  label: 'You',
  short: 'YOU',
  calledOn: 'Sat, Oct 24, 2026 · on the call',
  summary:
    'A computer science student, still in data structures, bombing the quizzes. They stopped at “data struc.” They do not have anyone in the major to say that to. They want a classmate who has felt behind. Not a tutor.',
}
