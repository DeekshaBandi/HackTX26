import { LINES } from '../data/call.ts'
import { CALLER, scoreMath, type Student } from '../data/students.ts'
import type { ExpansionReport } from '../lib/expand.ts'
import { ExpandCompare } from './ExpandCompare.tsx'

type Props = {
  students: Student[]
  report: ExpansionReport
  expanded: boolean
  onExpanded: (expanded: boolean) => void
  selectedId: string | null
  onSelect: (id: string) => void
}

export function PeoplePanel({ students, report, expanded, onExpanded, selectedId, onSelect }: Props) {
  const student = students.find((item) => item.id === selectedId) ?? null
  const isYou = selectedId === 'you'
  const bestId = students[0]?.id

  return (
    <aside className="panel">
      <p className="kicker">People map</p>
      <h2>Who this call sits near</h2>
      <p className="lede">
        Top 10 of {report.total.toLocaleString()} students in the file. Names and schools stay off this screen. The map is using {expanded ? 'the expanded search' : 'the words they actually finished'}.
      </p>

      <ExpandCompare report={report} expanded={expanded} onChange={onExpanded} />

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

      {!student && !isYou && (
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
