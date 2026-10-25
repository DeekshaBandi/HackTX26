import { recordText, ROSTER, type RosterStudent } from '../data/roster.ts'

/** The caller stopped mid-word. Autocomplete is for that tail, not a finished sentence. */
export const CALL_QUERY = "I've been in computer science and I keep bombing the data struc"
export const UNFINISHED_TAIL = 'struc'

export type SearchPhrase = {
  text: string
  source: string
}

function hasWord(text: string, word: string): boolean {
  return new RegExp(`\\b${word}\\b`, 'i').test(text)
}

/** What they actually got out. "struc" is unfinished, so it does not match "structures". */
export function literalMatch(text: string): boolean {
  return text.includes('computer science') || hasWord(text, 'bombing') || hasWord(text, 'struc') || hasWord(text, 'data')
}

export function phraseHits(text: string, phrases: string[]): string[] {
  return phrases.filter((phrase) => text.includes(phrase))
}

export type ExamplePhrase = {
  text: string
  source: string
  hits: number
}

export type ExpansionReport = {
  total: number
  literal: number
  expanded: number
  added: number
  examples: ExamplePhrase[]
  wordHits: { text: string; hits: number }[]
}

const STOP_WORDS = new Set([
  'what', 'when', 'where', 'which', 'have', 'high', 'this', 'that', 'with', 'from',
  'your', 'about', 'into', 'over', 'just', 'like', 'does', 'will', 'been', 'they',
  'them', 'than', 'then', 'also', 'only', 'some', 'more', 'most', 'very',
])

/** Words inside the live phrases. The whole suggestion often is not in the student file. */
export function contentWords(phrases: string[]): string[] {
  const seen = new Set<string>()
  const words: string[] = []
  for (const phrase of phrases) {
    for (const raw of phraseKey(phrase).split(/[^a-z0-9]+/)) {
      if (raw.length < 4 || STOP_WORDS.has(raw) || seen.has(raw) || /^\d+$/.test(raw)) continue
      seen.add(raw)
      words.push(raw)
    }
  }
  return words
}

export function phraseKey(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').trim()
}

function countWhere(test: (text: string) => boolean): number {
  let n = 0
  for (const row of ROSTER) {
    if (test(recordText(row))) n += 1
  }
  return n
}

export function expansionReport(phrases: SearchPhrase[]): ExpansionReport {
  const unique: SearchPhrase[] = []
  const seen = new Set<string>()
  for (const phrase of phrases) {
    const key = phraseKey(phrase.text)
    if (key.length < 4 || seen.has(key)) continue
    seen.add(key)
    unique.push({ text: phrase.text.trim(), source: phrase.source })
  }
  const keys = unique.map((phrase) => phraseKey(phrase.text))
  const words = contentWords(unique.map((phrase) => phrase.text))
  const literal = countWhere(literalMatch)
  const expanded = countWhere(
    (text) => literalMatch(text) || keys.some((key) => text.includes(key)) || words.some((word) => hasWord(text, word)),
  )
  return {
    total: ROSTER.length,
    literal,
    expanded,
    added: expanded - literal,
    examples: unique.map((phrase) => ({
      ...phrase,
      hits: countWhere((text) => text.includes(phraseKey(phrase.text))),
    })),
    wordHits: words
      .map((text) => ({ text, hits: countWhere((doc) => hasWord(doc, text)) }))
      .filter((word) => word.hits > 0),
  }
}

export type MatchParts = {
  topic: number
  emotion: number
  ask: number
  said: string[]
  phrases: string[]
}

const TOPIC_TERMS: { text: string; units: number }[] = [
  { text: 'computer science', units: 2 },
  { text: 'bombing', units: 1 },
  { text: 'data', units: 1 },
  { text: 'struc', units: 1 },
  { text: 'quiz', units: 1 },
  { text: 'tired', units: 1 },
  { text: 'pretending', units: 1 },
  { text: 'behind', units: 1 },
  { text: 'tutor', units: 1 },
  { text: 'office', units: 1 },
  { text: 'class', units: 1 },
  { text: 'coding', units: 1 },
  { text: 'failing', units: 1 },
  { text: 'lecture', units: 1 },
  { text: 'project', units: 1 },
]

function topicHits(text: string): { said: string[]; units: number } {
  const said: string[] = []
  let units = 0
  for (const term of TOPIC_TERMS) {
    const found = term.text.includes(' ') || term.text === 'quiz' || term.text === 'class'
      ? text.includes(term.text)
      : hasWord(text, term.text)
    if (!found) continue
    said.push(term.text)
    units += term.units
  }
  return { said, units }
}

/** A shorter phrase inside a longer hit is the same match, counted once. */
function uniquePhrases(text: string, phrases: string[]): string[] {
  const hit = phraseHits(text, phrases.map(phraseKey)).sort((a, b) => b.length - a.length)
  const kept: string[] = []
  for (const phrase of hit) {
    if (kept.some((longer) => longer.includes(phrase))) continue
    kept.push(phrase)
  }
  return kept
}

export function matchParts(row: RosterStudent, useExpansion: boolean, phrases: string[]): MatchParts {
  const text = recordText(row)
  const { said, units } = topicHits(text)
  const full = useExpansion ? uniquePhrases(text, phrases) : []
  const words = useExpansion ? contentWords(phrases).filter((word) => hasWord(text, word)) : []
  const found = [...full, ...words.filter((word) => !full.some((phrase) => phrase.includes(word)))]
  return {
    topic: Math.min(1, units / 6),
    emotion: Math.min(1, found.length / 2),
    ask: row.major === 'Computer Science' ? 1 : 0,
    said,
    phrases: found,
  }
}
