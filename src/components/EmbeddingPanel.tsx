import { API_BASE, type ApiStudent, type CallEvent } from '../lib/api.ts'
import type { LiveCall } from '../lib/useJudgeData.ts'
import { shortId, summaryOf } from '../data/students.ts'

type Props = {
  error: string | null
  calls: LiveCall[]
  call: LiveCall | null
  caller: ApiStudent | null
  selected: ApiStudent | null
  themeColor: (theme: string | null) => string
  onPickCall: (key: string | null) => void
  nearest: { label: string; score: number } | null
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })
}

const METHOD: Record<string, string> = {
  in_app: 'in the app',
  group_text: 'by group text',
  call_merge: 'by merging the calls',
  sms: 'by text',
}

/** One line of the live feed, in plain words. */
function describe(event: CallEvent): string {
  const d = event.detail ?? {}
  switch (event.event) {
    case 'call_started':
      return 'Call connected.'
    case 'profile_updated': {
      const fields = Array.isArray(d.fields) ? (d.fields as string[]).join(', ') : 'profile'
      if (!d.reembedded) return `Heard: ${fields}.`
      const ms = event.value != null ? ` in ${Math.round(event.value)} ms` : ''
      return `Heard: ${fields}. Re-embedded with ${d.embedder === 'local' ? 'the local backup' : 'Snowflake'}${ms}.`
    }
    case 'match_found': {
      const score = typeof d.score === 'number' ? Math.round(d.score * 100) : '?'
      const who = typeof d.match === 'string' ? `Student · ${shortId(d.match)}` : 'someone'
      const ms = event.value != null ? ` Search took ${event.value} ms.` : ''
      return `Closest match: ${who}, score ${score}${d.live ? ', on a call right now' : ''}. Plan: ${String(d.decision ?? '').replace('_', ' ')}.${ms}`
    }
    case 'crisis':
      return `Crisis flag (${String(d.level ?? 'concern')}). No peer matching for this caller.`
    case 'call_ended':
      return 'Call ended.'
    default:
      if (event.event in METHOD) {
        const who = typeof d.with === 'string' ? `Student · ${shortId(d.with)}` : 'their match'
        return `Connected with ${who} ${METHOD[event.event]}.`
      }
      return event.event.replace(/_/g, ' ')
  }
}

export function EmbeddingPanel({ error, calls, call, caller, selected, themeColor, onPickCall, nearest }: Props) {
  return (
    <aside className="panel">
      <p className="kicker">Embedding</p>
      <h2>Where the call lands</h2>
      <p className="lede">
        Every dot is a student, placed by their Snowflake embedding and colored by theme. The blue pin is the caller. Lines run to
        their nearest students, and faint lines join pairs matching has already connected.
      </p>

      {error && (
        <div className="empty-card">
          <p>Can't reach the matching API at {API_BASE}.</p>
          <p>{error}</p>
        </div>
      )}

      {calls.length > 1 && (
        <>
          <h4>Recent calls</h4>
          <div className="chips" role="listbox" aria-label="Calls">
            {calls.slice(0, 8).map((item, i) => (
              <button
                key={item.key}
                type="button"
                className="chip"
                aria-pressed={item.key === call?.key}
                onClick={() => onPickCall(i === 0 ? null : item.key)}
              >
                <span>{shortId(item.studentId)}</span>
                <strong>{item.ended ? time(item.lastAt) : 'live'}</strong>
              </button>
            ))}
          </div>
        </>
      )}

      {!call && !caller && !error && (
        <div className="empty-card">
          <p>Waiting for a call.</p>
          <p>Calls from the last two hours show up here as they happen. To send a sample one, from matching/ run:</p>
          <p className="code-block">curl -X POST {API_BASE}/call/record -H "Content-Type: application/json" -d @samples/call_maya_lonely.json</p>
        </div>
      )}

      {caller && (
        <>
          <h4>Summary</h4>
          <blockquote>{summaryOf(caller)}</blockquote>
          <h4>Cluster</h4>
          <p className="why-copy">
            <span className="pin-dot" style={{ background: themeColor(caller.theme), display: 'inline-block', marginRight: 8 }} />
            {caller.theme
              ? `Lands among students whose theme is “${caller.theme}”, the most common theme among their closest demo students.`
              : 'No theme yet. It is set once the caller has a situation and a first search runs.'}
          </p>
        </>
      )}

      {selected && (
        <>
          <h4>Selected dot · Student · {shortId(selected.student_id)}</h4>
          <blockquote>{summaryOf(selected)}</blockquote>
        </>
      )}

      {call && (
        <>
          <h4>Live call</h4>
          <ol className="call-log" aria-live="polite">
            {call.events.map((event, index) => (
              <li key={`${event.time}-${index}`} className={index === call.events.length - 1 ? 'on' : ''}>
                <span>{time(event.time)}</span>
                <p>{describe(event)}</p>
              </li>
            ))}
          </ol>
          <p className="note">Transcripts are never stored. This feed is what matching logged during the call.</p>
        </>
      )}

      {nearest && (
        <div className="ready">
          <p>{`The people map on the right sits this caller nearest ${nearest.label}, similarity ${nearest.score}.`}</p>
        </div>
      )}
    </aside>
  )
}
