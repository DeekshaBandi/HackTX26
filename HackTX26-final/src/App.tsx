import { useCallback, useEffect, useMemo, useState } from 'react'
import { topPins } from './data/students.ts'
import { API_BASE, type Expansion } from './lib/api.ts'
import { expansionReport } from './lib/expand.ts'
import { phrasesFromEngines, runSerpApiExpansion } from './lib/serpapiLive.ts'
import { useJudgeData } from './lib/useJudgeData.ts'
import { EmbeddingView } from './components/EmbeddingView.tsx'
import { LiveTranscript } from './components/LiveTranscript.tsx'
import { MapLoading } from './components/MapLoading.tsx'
import { PeopleGraph } from './components/PeopleGraph.tsx'
import { PeoplePanel } from './components/PeoplePanel.tsx'

const THEME_COLORS = [
  '#3c7a4a', '#a67c2d', '#3d7a78', '#7a5b8a', '#b5734a', '#4f6f8f',
  '#8a6248', '#b3403a', '#6b5130', '#245e4a', '#8d3b32', '#5f7f3a',
]

export default function App() {
  const data = useJudgeData()
  const [dotId, setDotId] = useState<string | null>(null)
  const [personId, setPersonId] = useState<string | null>(null)
  const pins = useMemo(() => topPins(data.nearby), [data.nearby])
  const nearIds = useMemo(() => pins.map((pin) => pin.id), [pins])
  const mapReady = data.students !== null && data.caller !== null && pins.length > 0
  const [expanded, setExpanded] = useState(true)
  const [expansion, setExpansion] = useState<Expansion | null>(null)
  const [expansionError, setExpansionError] = useState<string | null>(null)

  // SerpApi expansion for the current caller, once per caller and situation
  const callerId = data.caller?.student_id ?? null
  const situation = data.caller?.situation ?? null
  useEffect(() => {
    setExpansion(null)
    setExpansionError(null)
    if (!callerId) return
    let cancelled = false
    runSerpApiExpansion(callerId, situation)
      .then((result) => !cancelled && setExpansion(result))
      .catch((err: Error) => !cancelled && setExpansionError(err.message))
    return () => {
      cancelled = true
    }
  }, [callerId, situation])

  const report = useMemo(() => {
    if (!expansion || !data.students) return null
    const others = data.students.filter((row) => row.student_id !== expansion.student_id)
    return expansionReport(others, expansion.query, phrasesFromEngines(expansion.engines))
  }, [expansion, data.students])
  const reachIds = report && report.examples.length > 0 ? (expanded ? report.expandedIds : report.literalIds) : null

  // Same color for a theme everywhere on the page
  const themes = useMemo(
    () => [...new Set((data.students ?? []).map((row) => row.theme).filter((theme): theme is string => Boolean(theme)))].sort(),
    [data.students],
  )
  const themeColor = useCallback(
    (theme: string | null) => {
      const i = theme ? themes.indexOf(theme) : -1
      return i < 0 ? '#9c8f80' : THEME_COLORS[i % THEME_COLORS.length]
    },
    [themes],
  )

  const live = data.call && !data.call.ended

  return (
    <div className="shell">
      <header className="header">
        <div className="brand">
          <span className="brand-pin" aria-hidden />
          <span className="brand-name">AIEDH</span>
          <span className="brand-sub">Judge</span>
        </div>
        <p className="brand-meta">
          {data.error ? 'API offline' : live ? 'Live call' : data.call ? 'Last call' : 'Waiting for a call'} · {API_BASE.replace(/^https?:\/\//, '')}
        </p>
      </header>

      <div className="pair">
        <section className="column">
          <div className="stage-main">
            <EmbeddingView
              students={data.students ?? []}
              caller={data.caller}
              nearIds={nearIds}
              pairs={data.pairs}
              reachIds={reachIds}
              themeColor={themeColor}
              selectedId={dotId}
              onSelect={setDotId}
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
                studentsLoaded={data.students !== null}
                hasCaller={data.callerId !== null}
                embedded={pins.length > 0}
              />
            )}
          </div>
          <PeoplePanel
            ready={mapReady}
            students={pins}
            total={data.students?.length ?? null}
            caller={data.caller}
            call={data.call}
            nearby={data.nearby}
            delivered={data.delivered}
            expansion={expansion}
            expansionError={expansionError}
            report={report}
            expanded={expanded}
            onExpanded={setExpanded}
            selectedId={personId}
            onSelect={setPersonId}
          />
        </section>
      </div>
    </div>
  )
}
