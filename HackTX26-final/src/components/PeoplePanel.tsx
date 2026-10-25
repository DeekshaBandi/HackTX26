import type { ApiStudent, Expansion, Nearby, StudentMatch } from '../lib/api.ts'
import type { ExpansionReport } from '../lib/expand.ts'
import type { LiveCall } from '../lib/useJudgeData.ts'
import { shortId, summaryOf, type Student } from '../data/students.ts'
import { ExpandCompare } from './ExpandCompare.tsx'

type Props = {
  ready: boolean
  students: Student[]
  total: number | null
  caller: ApiStudent | null
  call: LiveCall | null
  nearby: Nearby | null
  delivered: StudentMatch[]
  expansion: Expansion | null
  expansionError: string | null
  report: ExpansionReport | null
  expanded: boolean
  onExpanded: (expanded: boolean) => void
  selectedId: string | null
  onSelect: (id: string) => void
}

function clock(iso: string): string {
  return new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function PeoplePanel({
  ready, students, total, caller, call, nearby, delivered, expansion, expansionError, report, expanded, onExpanded, selectedId, onSelect,
}: Props) {
  const student = students.find((item) => item.id === selectedId) ?? null
  const isYou = selectedId === 'you'
  const bestId = students[0]?.id
  const threshold = nearby?.threshold ?? null

  return (
    <aside className="panel">
      <p className="kicker">People map</p>
      <h2>Who this call sits near</h2>
      {!ready && (
        <>
          <p className="load-word">Loading</p>
          <p className="lede">Pins show up once the caller has an embedding and the matching API returns their nearest students.</p>
        </>
      )}

      {ready && (
        <>
          <p className="lede">
            Top {students.length}
            {total != null ? ` of ${total.toLocaleString()}` : ''} students by {nearby?.embedder === 'local' ? 'local backup' : 'Snowflake'} embedding
            similarity. Closer to the center means more similar. Names and schools stay off this screen.
          </p>
          {threshold != null && (
            <p className="note">
              Matching connects two people at a score of {Math.round(threshold * 100)} or higher. People this caller was already
              matched with are left off the map, the same as in matching.
            </p>
          )}
        </>
      )}

      {caller && <SerpSection expansion={expansion} error={expansionError} report={report} expanded={expanded} onExpanded={onExpanded} />}

      {delivered.length > 0 && (
        <div className="receipt">
          <h4>Matched with</h4>
          <ul>
            {delivered.map((item) => (
              <li key={item.match_id}>
                <div className="part-top">
                  <span>Student · {shortId(item.other_id)}</span>
                  <span>{Math.round(item.score * 100)} · {item.method.replace('_', ' ')}</span>
                </div>
                <p>{item.other_situation ?? 'No situation on the record.'} · {clock(item.created_at)}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ready && (
        <div className="chips" role="listbox" aria-label="Students">
          <button type="button" className="chip" aria-pressed={isYou} onClick={() => onSelect('you')}>
            <span>YOU</span>
            <strong>{call && !call.ended ? 'live' : 'call'}</strong>
          </button>
          {students.map((item) => (
            <button
              key={item.id}
              type="button"
              className="chip"
              aria-pressed={item.id === selectedId}
              onClick={() => onSelect(item.id)}
            >
              <span>{item.short}</span>
              <strong>{item.score}</strong>
            </button>
          ))}
        </div>
      )}

      {ready && !student && !isYou && (
        <div className="empty-card">
          <p>Click a pin, or a chip above.</p>
          <p>The closest pin is the best match. Arrows travel outward from you the whole time.</p>
        </div>
      )}

      {isYou && caller && <YouCard caller={caller} call={call} />}
      {student && <StudentCard student={student} best={student.id === bestId} threshold={threshold} />}
    </aside>
  )
}

function YouCard({ caller, call }: { caller: ApiStudent; call: LiveCall | null }) {
  return (
    <article className="detail">
      <header className="detail-head">
        <span className="pin-dot" style={{ background: '#1a73e8' }} />
        <div>
          <h3>You</h3>
          <p className="redacted">{call && !call.ended ? 'Live caller' : 'Caller'} · University withheld</p>
        </div>
      </header>
      {call && <p className="date">{clock(call.startedAt)}{call.ended ? ' · call ended' : ' · on the call'}</p>}
      <h4>What they said</h4>
      <blockquote>{summaryOf(caller)}</blockquote>
      <ul className="why-list">
        {caller.stage && <li><div className="part-top"><span>Stage</span><span>{caller.stage}</span></div></li>}
        {caller.major && <li><div className="part-top"><span>Major</span><span>{caller.major}</span></div></li>}
      </ul>
      <p className="note">Scores on the other pins compare their embedding to this caller's.</p>
    </article>
  )
}

function StudentCard({ student, best, threshold }: { student: Student; best: boolean; threshold: number | null }) {
  return (
    <article className="detail">
      <header className="detail-head">
        <span className="pin-dot" style={{ background: student.color }} />
        <div>
          <h3>{student.label}</h3>
          <p className="redacted">Name withheld · University withheld</p>
        </div>
      </header>

      <div className="score-row">
        <div>
          <p className="score">{student.score}</p>
          <p className="muted">similarity</p>
        </div>
        {best && <span className="best">Closest match</span>}
      </div>
      <div className="track" aria-hidden>
        <span style={{ width: `${student.score}%` }} />
      </div>

      <p className="date">
        {student.synthetic ? 'Demo student' : 'Real caller'}
        {student.online ? ' · on a call now' : ''}
      </p>

      <h4>What they said</h4>
      <blockquote>{student.summary}</blockquote>

      <h4>Why this score</h4>
      <p className="formula">Score = 1 − cosine distance between the two Snowflake embeddings, × 100</p>
      <ul className="why-list">
        {student.stage && <li><div className="part-top"><span>Stage</span><span>{student.stage}</span></div></li>}
        {student.major && <li><div className="part-top"><span>Major</span><span>{student.major}</span></div></li>}
        {student.year && <li><div className="part-top"><span>Year</span><span>{student.year}</span></div></li>}
      </ul>
      <p className="math">
        {student.similarity.toFixed(3)}
        {threshold != null
          ? student.matchable
            ? `  ≥  ${threshold.toFixed(2)}  →  close enough to match`
            : `  <  ${threshold.toFixed(2)}  →  not close enough to match`
          : ''}
      </p>
      <p className="note">Only what they are going through is embedded. Major, year, and interests are kept off the vector on purpose.</p>
    </article>
  )
}

function SerpSection({ expansion, error, report, expanded, onExpanded }: {
  expansion: Expansion | null
  error: string | null
  report: ExpansionReport | null
  expanded: boolean
  onExpanded: (expanded: boolean) => void
}) {
  if (error) {
    return (
      <div className="receipt">
        <h4>SerpApi</h4>
        <p className="note">Expansion failed: {error}</p>
      </div>
    )
  }
  if (!expansion) {
    return (
      <div className="receipt">
        <h4>SerpApi</h4>
        <p className="note">Searching for phrases related to this caller's situation…</p>
      </div>
    )
  }
  if (!expansion.query) {
    return (
      <div className="receipt">
        <h4>SerpApi</h4>
        <p className="note">No situation on the record yet, so there is nothing to search for.</p>
      </div>
    )
  }
  return (
    <>
      <div className="receipt">
        <h4>SerpApi</h4>
        <p className="note">
          Searched “{expansion.query}”, the caller's situation with names, schools, and numbers taken out. The key stays on the API
          server. {expansion.calls_used} of {expansion.calls_limit} searches used this run, and repeats come from memory.
        </p>
        <ul>
          {expansion.engines.map((engine) => (
            <li key={engine.id}>
              <div className="part-top">
                <span>{engine.label}</span>
                <span>{engine.cached ? 'Cached' : engine.receipt?.status || 'No response'}</span>
              </div>
              <p>
                {engine.receipt && !engine.error
                  ? `${engine.receipt.created_at} · ${engine.receipt.seconds == null ? '' : `${engine.receipt.seconds.toFixed(2)}s · `}${engine.phrases.length} phrases`
                  : engine.error}
              </p>
            </li>
          ))}
        </ul>
      </div>

      {report && report.examples.length > 0 && (
        <>
          <p className="note">
            How many of the {report.total.toLocaleString()} student records the caller's own words reach, and how many more the
            SerpApi phrases add. The map on the left fades everyone outside the selected count.
          </p>
          <ExpandCompare report={report} expanded={expanded} onChange={onExpanded} />
          {report.wordHits.length > 0 && (
            <>
              <h4>Words inside the phrases</h4>
              <ul className="auto-list">
                {report.wordHits.slice(0, 8).map((word) => (
                  <li key={word.text}>
                    <span>{word.text}</span>
                    <em>{word.hits}</em>
                    <small>records</small>
                  </li>
                ))}
              </ul>
            </>
          )}
          <h4>Example phrases</h4>
          <ul className="auto-list">
            {report.examples.slice(0, 10).map((phrase) => (
              <li key={`${phrase.source}-${phrase.text}`}>
                <span>{phrase.text}</span>
                <em>{phrase.hits}</em>
                <small>{phrase.source}</small>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}
