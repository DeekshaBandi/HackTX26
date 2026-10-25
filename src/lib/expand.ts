import type { ApiStudent } from './api.ts'

export type SearchPhrase = {
  text: string
  source: string
}

/** The words on a student's record that expansion can find. Name and school are never part of it. */
export function recordText(row: ApiStudent): string {
  return [row.major, row.situation, row.theme, row.interests, row.stage, row.year].filter(Boolean).join(' ').toLowerCase()
}

function hasWord(text: string, word: string): boolean {
  return new RegExp(`\\b${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(text)
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
  /** Students found by the caller's own words, and by their words plus the SerpApi phrases */
  literalIds: Set<string>
  expandedIds: Set<string>
}

const STOP_WORDS = new Set([
  'what', 'when', 'where', 'which', 'have', 'high', 'this', 'that', 'with', 'from',
  'your', 'about', 'into', 'over', 'just', 'like', 'does', 'will', 'been', 'they',
  'them', 'than', 'then', 'also', 'only', 'some', 'more', 'most', 'very', 'feel',
  'feeling', 'college', 'student', 'students', 'really', 'still', 'being',
])

/** Words inside the phrases. The whole suggestion often is not in the student file. */
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

/**
 * How many records the caller's own words reach, and how many more the SerpApi phrases add.
 * "No expansion" counts records with any of the caller's words. "With expansion" adds records with
 * a whole phrase, or a word inside one, from any engine.
 */
export function expansionReport(rows: ApiStudent[], query: string, phrases: SearchPhrase[]): ExpansionReport {
  const unique: SearchPhrase[] = []
  const seen = new Set<string>()
  for (const phrase of phrases) {
    const key = phraseKey(phrase.text)
    if (key.length < 4 || seen.has(key)) continue
    seen.add(key)
    unique.push({ text: phrase.text.trim(), source: phrase.source })
  }
  const keys = unique.map((phrase) => phraseKey(phrase.text))
  const said = contentWords([query])
  const words = contentWords(unique.map((phrase) => phrase.text)).filter((word) => !said.includes(word))
  const texts = rows.map((row) => ({ id: row.student_id, text: recordText(row) }))

  const literalIds = new Set<string>()
  const expandedIds = new Set<string>()
  for (const { id, text } of texts) {
    const literal = said.some((word) => hasWord(text, word))
    if (literal) literalIds.add(id)
    if (literal || keys.some((key) => text.includes(key)) || words.some((word) => hasWord(text, word))) expandedIds.add(id)
  }
  const count = (test: (text: string) => boolean) => texts.reduce((n, row) => n + (test(row.text) ? 1 : 0), 0)

  return {
    total: rows.length,
    literal: literalIds.size,
    expanded: expandedIds.size,
    added: expandedIds.size - literalIds.size,
    examples: unique.map((phrase) => ({ ...phrase, hits: count((text) => text.includes(phraseKey(phrase.text))) })),
    wordHits: words
      .map((text) => ({ text, hits: count((doc) => hasWord(doc, text)) }))
      .filter((word) => word.hits > 0),
    literalIds,
    expandedIds,
  }
}
