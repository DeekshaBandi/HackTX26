import { useEffect, useState } from 'react'
import { LINES } from './data/call.ts'
import { EmbeddingPanel } from './components/EmbeddingPanel.tsx'
import { EmbeddingView } from './components/EmbeddingView.tsx'
import { PeopleGraph } from './components/PeopleGraph.tsx'
import { PeoplePanel } from './components/PeoplePanel.tsx'

type View = 'embed' | 'people'

export default function App() {
  const [view, setView] = useState<View>('embed')
  const [revealedLines, setRevealedLines] = useState(0)
  const [phraseId, setPhraseId] = useState<string | null>(null)
  const [personId, setPersonId] = useState<string | null>(null)

  useEffect(() => {
    if (view !== 'embed') return
    if (revealedLines >= LINES.length) return
    const delay = revealedLines === 0 ? 700 : 1900
    const id = window.setTimeout(() => setRevealedLines((count) => count + 1), delay)
    return () => window.clearTimeout(id)
  }, [view, revealedLines])

  return (
    <div className="shell">
      <header className="header">
        <div className="brand">
          <span className="brand-pin" aria-hidden />
          <span className="brand-name">AIEDH</span>
          <span className="brand-sub">Judge</span>
        </div>
        <div className="switch" role="tablist" aria-label="Visualizations">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'embed'}
            onClick={() => setView('embed')}
          >
            Embedding
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'people'}
            onClick={() => setView('people')}
          >
            People map
          </button>
        </div>
        <p className="brand-meta">Sample data · Sat, Oct 24</p>
      </header>

      <div className="stage">
        <div className="stage-main">
          {view === 'embed' ? (
            <EmbeddingView
              revealedLines={revealedLines}
              selectedId={phraseId}
              onSelect={setPhraseId}
            />
          ) : (
            <PeopleGraph
              selectedId={personId}
              onSelect={(id) => setPersonId(id || null)}
            />
          )}
        </div>

        {view === 'embed' ? (
          <EmbeddingPanel
            revealedLines={revealedLines}
            selectedId={phraseId}
            onSelect={setPhraseId}
            onReplay={() => {
              setPhraseId(null)
              setRevealedLines(0)
            }}
            onOpenPeople={() => setView('people')}
          />
        ) : (
          <PeoplePanel selectedId={personId} onSelect={setPersonId} />
        )}
      </div>
    </div>
  )
}
