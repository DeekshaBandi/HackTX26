import type { ApiStudent, Nearby, StudentMatch } from '../lib/api.ts'
import type { LiveCall } from '../lib/useJudgeData.ts'
import { shortId, summaryOf, type Student } from '../data/students.ts'

type Props = {
  ready: boolean
  students: Student[]
  total: number | null
  caller: ApiStudent | null
  call: LiveCall | null
  nearby: Nearby | null
  delivered: StudentMatch[]
  selectedId: string | null
  onSelect: (id: string) => void
}

function clock(iso: string): string {
  return new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function PeoplePanel({ ready, students, total, caller, call, nearby, delivered, selectedId, onSelect }: Props) {
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
