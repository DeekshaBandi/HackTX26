import { useEffect, useMemo, useState } from 'react'
import { LINES } from './data/call.ts'
import { topPins } from './data/students.ts'
import { expansionReport, type SearchPhrase } from './lib/expand.ts'
import { phrasesFromEngines, runSerpApiExpansion, type LiveEngine } from './lib/serpapiLive.ts'
import { EmbeddingView } from './components/EmbeddingView.tsx'
import { LiveTranscript } from './components/LiveTranscript.tsx'
import { MapLoading } from './components/MapLoading.tsx'
import { PeopleGraph } from './components/PeopleGraph.tsx'
import { PeoplePanel } from './components/PeoplePanel.tsx'

export default function App() {
  const [revealedLines, setRevealedLines] = useState(0)
  const [phraseId, setPhraseId] = useState<string | null>(null)
  const [personId, setPersonId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)
  const [searchDone, setSearchDone] = useState<LiveEngine[]>([])
  const [searchPhrases, setSearchPhrases] = useState<SearchPhrase[] | null>(null)
  const [embedDone, setEmbedDone] = useState(false)
  const phraseTexts = useMemo(() => (searchPhrases ?? []).map((phrase) => phrase.text), [searchPhrases])
  const report = useMemo(() => expansionReport(searchPhrases ?? []), [searchPhrases])
  const pins = useMemo(() => topPins(expanded, phraseTexts), [expanded, phraseTexts])
  const mapReady = embedDone && searchPhrases !== null

  useEffect(() => {
    if (revealedLines >= LINES.length) {
      setEmbedDone(true)
      return
    }
    const delay = revealedLines === 0 ? 700 : 1900
    const id = window.setTimeout(() => setRevealedLines((count) => count + 1), delay)
    return () => window.clearTimeout(id)
  }, [revealedLines])

  useEffect(() => {
    let cancelled = false
    runSerpApiExpansion((row) => {
      if (!cancelled) setSearchDone((rows) => [...rows, row])
    }).then((rows) => {
      if (!cancelled) setSearchPhrases(phrasesFromEngines(rows))
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="shell">
      <header className="header">
        <div className="brand">
          <span className="brand-pin" aria-hidden />
          <span className="brand-name">AIEDH</span>
          <span className="brand-sub">Judge</span>
        </div>
        <p className="brand-meta">Sample data · Sat, Oct 24</p>
      </header>

      <div className="pair">
        <section className="column">
          <div className="stage-main">
            <EmbeddingView
              revealedLines={revealedLines}
              selectedId={phraseId}
              onSelect={setPhraseId}
            />
          </div>
          <LiveTranscript />
        </section>

        <section className="column">
          <div className="stage-main">
            {mapReady ? (
              <PeopleGraph
                students={pins}
                selectedId={personId}
                onSelect={(id) => setPersonId(id || null)}
              />
            ) : (
              <MapLoading
                doneIds={searchDone.map((row) => row.id)}
                embedStep={revealedLines}
                searchBack={searchPhrases !== null}
              />
            )}
          </div>
          <PeoplePanel
            ready={mapReady}
            calls={searchDone}
            students={pins}
            report={report}
            expanded={expanded}
            onExpanded={(next) => {
              setExpanded(next)
              setPersonId(null)
            }}
            selectedId={personId}
            onSelect={setPersonId}
          />
        </section>
      </div>
    </div>
  )
}
