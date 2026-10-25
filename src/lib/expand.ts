import { recordText, ROSTER, type RosterStudent } from '../data/roster.ts'

/** The caller stopped mid-word. Autocomplete is for that tail, not a finished sentence. */
export const CALL_QUERY = "I've been in computer science and I keep bombing the data struc"
export const UNFINISHED_TAIL = 'struc'

export type EngineKind = 'autocomplete' | 'web' | 'scholar' | 'news' | 'video' | 'forum'

export type EngineSpec = {
  id: string
  label: string
  engine: string
  kind: EngineKind
  phrases: string[]
}

/**
 * Phrases stand in for what each SerpAPI engine would return for this call.
 * Hit counts are computed against data/students.json. Nothing here calls SerpAPI.
 * Forums are one engine in the list, not the whole expansion.
 */
export const ENGINES: EngineSpec[] = [
  {
    id: 'autocomplete',
    label: 'Google Autocomplete',
    engine: 'google_autocomplete',
    kind: 'autocomplete',
    phrases: [
      'data structures',
      'data structures and algorithms',
      'data structures quiz',
      'data structures class',
      'data structures failing',
    ],
  },
  {
    id: 'google',
    label: 'Google Search',
    engine: 'google',
    kind: 'web',
    phrases: ['failing a class', 'burned out from classes'],
  },
  {
    id: 'scholar',
    label: 'Google Scholar',
    engine: 'google_scholar',
    kind: 'scholar',
    phrases: ['imposter syndrome in a hard major', 'imposter syndrome'],
  },
  {
    id: 'news',
    label: 'Google News',
    engine: 'google_news',
    kind: 'news',
    phrases: ["can't focus in lecture"],
  },
  {
    id: 'youtube',
    label: 'YouTube',
    engine: 'youtube',
    kind: 'video',
    phrases: ['drowning in coding', 'too many coding projects'],
  },
  {
    id: 'bing',
    label: 'Bing',
    engine: 'bing',
    kind: 'web',
    phrases: ['everyone in my intro class', 'gap between me and my peers'],
  },
  {
    id: 'duckduckgo',
    label: 'DuckDuckGo',
    engine: 'duckduckgo',
    kind: 'web',
    phrases: ['coding assignments'],
  },
  {
    id: 'reddit',
    label: 'Reddit',
    engine: 'reddit',
    kind: 'forum',
    phrases: ['imposter syndrome hitting hard'],
  },
]

const PHRASES = ENGINES.flatMap((engine) => engine.phrases)

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

export function expansionMatch(text: string): boolean {
  return literalMatch(text) || phraseHits(text, PHRASES).length > 0
}

export type PhraseHit = { text: string; hits: number }

export type EngineReport = {
  id: string
  label: string
  engine: string
  kind: EngineKind
  hits: number
  phrases: PhraseHit[]
}

export type ExpansionReport = {
  total: number
  literal: number
  expanded: number
  added: number
  engines: EngineReport[]
}

function countWhere(test: (text: string) => boolean): number {
  let n = 0
  for (const row of ROSTER) {
    if (test(recordText(row))) n += 1
  }
  return n
}

export function expansionReport(): ExpansionReport {
  const literal = countWhere(literalMatch)
  const expanded = countWhere(expansionMatch)
  const engines = ENGINES.map((engine) => {
    const phrases = engine.phrases.map((text) => ({
      text,
      hits: countWhere((doc) => doc.includes(text)),
    }))
    return {
      id: engine.id,
      label: engine.label,
      engine: engine.engine,
      kind: engine.kind,
      hits: countWhere((doc) => engine.phrases.some((phrase) => doc.includes(phrase))),
      phrases,
    }
  })
  return {
    total: ROSTER.length,
    literal,
    expanded,
    added: expanded - literal,
    engines,
  }
}

export type MatchParts = {
  topic: number
  emotion: number
  ask: number
  said: string[]
  phrases: string[]
}

export function matchParts(row: RosterStudent, useExpansion: boolean): MatchParts {
  const text = recordText(row)
  const said: string[] = []
  if (text.includes('computer science')) said.push('computer', 'science')
  for (const word of ['bombing', 'data', 'struc']) {
    if (hasWord(text, word)) said.push(word)
  }
  const phrases = useExpansion ? phraseHits(text, PHRASES) : []
  return {
    topic: Math.min(1, said.length / 2),
    emotion: Math.min(1, phrases.length / 2),
    ask: row.major === 'Computer Science' ? 1 : 0,
    said,
    phrases,
  }
}
