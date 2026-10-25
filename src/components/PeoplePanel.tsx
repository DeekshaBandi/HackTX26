import { LINES } from '../data/call.ts'
import { CALLER, scoreMath, type Student } from '../data/students.ts'
import type { ExpansionReport } from '../lib/expand.ts'
import { SEARCH_STEPS, serpCallBudget, type LiveEngine } from '../lib/serpapiLive.ts'
import { ExpandCompare } from './ExpandCompare.tsx'

type Props = {
  ready: boolean
  calls: LiveEngine[]
  students: Student[]
  report: ExpansionReport
  expanded: boolean
  onExpanded: (expanded: boolean) => void
  selectedId: string | null
  onSelect: (id: string) => void
}

export function PeoplePanel({ ready, calls, students, report, expanded, onExpanded, selectedId, onSelect }: Props) {
  const student = students.find((item) => item.id === selectedId) ?? null
  const isYou = selectedId === 'you'
  const bestId = students[0]?.id

  return (
    <aside className="panel">
      <p className="kicker">People map</p>
      <h2>Who this call sits near</h2>
      {!ready && (
        <>
          <p className="load-word">Loading</p>
          <p className="lede">
            Scores and distances stay hidden until the embedding is filed and SerpAPI returns the expansion phrases.
          </p>
        </>
      )}

      <CallReceipt calls={calls} />

      {ready && (
        <>
          <p className="lede">
            Top 10 of {report.total.toLocaleString()} students in the file. Names and schools stay off this screen.
            {` Closer to the center means a higher score from ${expanded ? 'the call plus words inside the SerpAPI phrases' : 'only the words they finished'}.`}
          </p>
          <ExpandCompare report={report} expanded={expanded} onChange={onExpanded} />
          <h4>Example phrases</h4>
          <p className="note">The whole suggestion is rarely in the file, so that count stays 0. Scores use the words inside the suggestion that do appear.</p>
          {report.wordHits.length > 0 && (
            <ul className="auto-list">
              {report.wordHits.slice(0, 8).map((word) => (
                <li key={word.text}>
                  <span>{word.text}</span>
                  <em>{word.hits}</em>
                  <small>word in the file</small>
                </li>
              ))}
            </ul>
          )}
          {report.examples.length === 0 ? (
            <p className="note">The search returned no expansion phrases.</p>
          ) : (
            <ul className="auto-list">
              {report.examples.slice(0, 10).map((phrase) => (
                <li key={`${phrase.source}-${phrase.text}`}>
                  <span>{phrase.text}</span>
                  <em>{phrase.hits}</em>
                  <small>{phrase.source}</small>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <details className="call-fold">
        <summary>The call</summary>
        <ol className="call-log">
          {LINES.map((item) => (
            <li key={item.clock}>
              <span>{item.clock}</span>
              <p>{item.text}</p>
            </li>
          ))}
        </ol>
      </details>

      {ready && (
        <div className="chips" role="listbox" aria-label="Students">
        <button
          type="button"
          className="chip"
          aria-pressed={isYou}
          onClick={() => onSelect('you')}
        >
          <span>YOU</span>
          <strong>live</strong>
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

      {isYou && <YouCard />}
      {student && <StudentCard student={student} best={student.id === bestId} />}
    </aside>
  )
}

function CallReceipt({ calls }: { calls: LiveEngine[] }) {
  const ordered = SEARCH_STEPS.map((step) => calls.find((call) => call.id === step.id)).filter((call) => call != null)
  const budget = serpCallBudget()
  return (
    <div className="receipt">
      <h4>SerpAPI</h4>
      <p className="note">
        {budget.used} of {budget.limit} uncached calls used. Cached replies do not move the plan counter, so these requests skip the cache. Nothing is loaded from a saved response.
      </p>
      <ul>
        {ordered.map((call) => (
          <li key={call.id}>
            <div className="part-top">
              <span>{call.label}</span>
              <span>{call.receipt?.status ?? 'No response'}</span>
            </div>
            <p>
              {call.receipt
                ? `${call.receipt.createdAt} · ${call.receipt.seconds == null ? '' : `${call.receipt.seconds.toFixed(2)}s · `}${call.receipt.id}`
                : call.error}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}

function YouCard() {
  return (
    <article className="detail">
      <header className="detail-head">
        <span className="pin-dot" style={{ background: '#1a73e8' }} />
        <div>
          <h3>{CALLER.label}</h3>
          <p className="redacted">Live caller · University withheld</p>
        </div>
      </header>
      <p className="date">{CALLER.calledOn}</p>
      <h4>Call summary</h4>
      <blockquote>{CALLER.summary}</blockquote>
      <p className="note">
        Compatibility is only computed against the other pins. This card is the call those scores are compared to.
      </p>
    </article>
  )
}

function StudentCard({ student, best }: { student: Student; best: boolean }) {
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
          <p className="muted">compatibility</p>
        </div>
        {best && <span className="best">Closest match</span>}
      </div>
      <div className="track" aria-hidden>
        <span style={{ width: `${student.score}%` }} />
      </div>

      <p className="date">{student.calledOn}</p>

      <h4>Call summary</h4>
      <blockquote>{student.summary}</blockquote>

      <h4>Why this score</h4>
      <p className="formula">Score = 0.5 words they said + 0.3 expanded phrases + 0.2 same major</p>
      <ul className="why-list">
        <li>
          <div className="part-top">
            <span>Words they said</span>
            <span>{student.parts.topic.toFixed(2)}</span>
          </div>
          <div className="track"><span style={{ width: `${student.parts.topic * 100}%` }} /></div>
          <p>{student.why[0].detail}</p>
        </li>
        <li>
          <div className="part-top">
            <span>Expanded phrases</span>
            <span>{student.parts.emotion.toFixed(2)}</span>
          </div>
          <div className="track"><span style={{ width: `${student.parts.emotion * 100}%` }} /></div>
          <p>{student.why[1].detail}</p>
        </li>
        <li>
          <div className="part-top">
            <span>Same major</span>
            <span>{student.parts.ask.toFixed(2)}</span>
          </div>
          <div className="track"><span style={{ width: `${student.parts.ask * 100}%` }} /></div>
          <p>{student.why[2].detail}</p>
        </li>
      </ul>
      <p className="math">{scoreMath(student.parts)}</p>
    </article>
  )
}
