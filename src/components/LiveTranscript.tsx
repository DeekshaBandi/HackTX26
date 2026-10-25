import { useEffect, useRef, useState } from 'react'

// Live call lines from the phone server (server.py) over its /dashboard WebSocket.
// Vite proxies /dashboard to the Python server and adds the DASHBOARD_TOKEN, so the
// page never holds it. Caller lines are keyed "c:<turn_id>", operator lines
// "o:<turn_id>". Each line keeps the ts it first appeared with, so the
// conversation reads in order even when a final lands after the next partial.

type Line = {
  key: string
  who: 'Operator' | 'Caller'
  text: string
  final: boolean
  ts: number
}

type Call = {
  id: string
  from: string
  startedTs: number
  active: boolean
  // Started while this page was open, so it drops in like a new call box.
  fresh: boolean
  lines: Line[]
}

type Status = 'connecting' | 'connected' | 'reconnecting'

type ServerTurn = { turn_id: unknown; text: string; final: boolean; ts: number }
type ServerCall = { call_id: string; from_number: string; started_ts: number; active: boolean; turns: ServerTurn[] }

type Message =
  | { type: 'snapshot'; calls: ServerCall[] }
  | { type: 'call_started'; call_id: string; from_number: string; ts: number }
  | { type: 'muse_transcript' | 'operator_text'; call_id: string; turn_id: unknown; text: string; final: boolean; ts: number }
  | { type: 'call_ended'; call_id: string }
  | { type: 'call_record' }

function blankCall(id: string, from = '', startedTs = Date.now() / 1000): Call {
  return { id, from, startedTs, active: true, fresh: false, lines: [] }
}

// Only call_started opens a call. Lines for a call we are not showing (one that
// ended before this page loaded, replayed by the server) are dropped.
function withCall(calls: Map<string, Call>, id: string, change: (call: Call) => Call): Map<string, Call> {
  if (!calls.has(id)) return calls
  const next = new Map(calls)
  next.set(id, change(calls.get(id) ?? blankCall(id)))
  return next
}

function putLine(call: Call, key: string, who: Line['who'], text: string, final: boolean, ts: number): Call {
  const existing = call.lines.find((line) => line.key === key)
  if (existing?.final && !final) return call // late partial for a finished line
  if (who === 'Operator' && final && !text) {
    // cut off before any of it was heard
    return { ...call, lines: call.lines.filter((line) => line.key !== key) }
  }
  const line: Line = { key, who, text, final, ts: existing?.ts ?? ts ?? Date.now() / 1000 }
  const lines = existing
    ? call.lines.map((item) => (item.key === key ? line : item))
    : [...call.lines, line].sort((a, b) => a.ts - b.ts)
  return { ...call, lines }
}

function reduce(calls: Map<string, Call>, msg: Message): Map<string, Call> {
  switch (msg.type) {
    case 'snapshot': {
      // A refresh starts clear: only a call that is still live carries over.
      const next = new Map<string, Call>()
      for (const call of msg.calls.filter((item) => item.active)) {
        let item = { ...blankCall(call.call_id, call.from_number, call.started_ts), active: call.active }
        for (const turn of call.turns) {
          item = putLine(item, `c:${String(turn.turn_id)}`, 'Caller', turn.text, turn.final, turn.ts)
        }
        next.set(call.call_id, item)
      }
      return next
    }
    case 'call_started':
      return withCall(new Map(calls).set(msg.call_id, calls.get(msg.call_id) ?? blankCall(msg.call_id)), msg.call_id, (call) => ({
        ...call,
        from: msg.from_number || call.from,
        startedTs: msg.ts,
        active: true,
        fresh: true,
      }))
    case 'muse_transcript':
      return withCall(calls, msg.call_id, (call) =>
        putLine(call, `c:${String(msg.turn_id)}`, 'Caller', msg.text, msg.final, msg.ts),
      )
    case 'operator_text':
      return withCall(calls, msg.call_id, (call) =>
        putLine(call, `o:${String(msg.turn_id)}`, 'Operator', msg.text, msg.final, msg.ts),
      )
    case 'call_ended':
      return withCall(calls, msg.call_id, (call) => ({ ...call, active: false }))
    default:
      return calls
  }
}

function useLiveCalls() {
  const [calls, setCalls] = useState<Map<string, Call>>(new Map())
  const [status, setStatus] = useState<Status>('connecting')

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer = 0
    let retryMs = 500
    let stopped = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/dashboard`)
      ws.onopen = () => {
        retryMs = 500
        setStatus('connected')
      }
      ws.onmessage = (event) => setCalls((prev) => reduce(prev, JSON.parse(event.data) as Message))
      ws.onclose = () => {
        if (stopped) return
        setStatus('reconnecting')
        timer = window.setTimeout(connect, retryMs)
        retryMs = Math.min(retryMs * 2, 5000)
      }
      ws.onerror = () => ws?.close()
    }
    connect()

    return () => {
      stopped = true
      window.clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return { calls, status }
}

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function LiveTranscript() {
  const { calls, status } = useLiveCalls()
  const logRef = useRef<HTMLOListElement>(null)
  const nearBottom = useRef(true)

  // The live call if there is one, otherwise the latest call.
  const all = [...calls.values()].sort((a, b) => b.startedTs - a.startedTs)
  const call = all.find((item) => item.active) ?? all[0] ?? null
  const lastLine = call?.lines.at(-1)
  const onCall = status === 'connected' && !!call?.active
  const statusText =
    status === 'connecting'
      ? 'Connecting…'
      : status === 'reconnecting'
        ? 'Phone server offline'
        : onCall
          ? 'Caller on the line'
          : 'No active call'

  useEffect(() => {
    const el = logRef.current
    if (el && nearBottom.current) el.scrollTop = el.scrollHeight
  }, [call?.id, call?.lines.length, lastLine?.text])

  return (
    <aside className="panel live-panel">
      <div className="panel-top">
        <div>
          <p className="kicker">Live call</p>
          <h2>Transcript</h2>
        </div>
        <span className={onCall ? 'live-status on' : 'live-status'} title={`Phone server: ${status}`}>
          <i aria-hidden />
          {statusText}
        </span>
      </div>

      {call ? (
        <div key={call.id} className={call.fresh ? 'live-call drop' : 'live-call'}>
          <p className="lede live-meta">
            {call.from || 'Unknown caller'} · {fmtTime(call.startedTs)}
            <span className={call.active ? 'live-badge on' : 'live-badge'}>{call.active ? '● LIVE' : 'ENDED'}</span>
          </p>
          <ol
            className="call-log live-log"
            aria-live="polite"
            ref={logRef}
            onScroll={(event) => {
              const el = event.currentTarget
              nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
            }}
          >
            {call.lines.length === 0 ? (
              <li className="live-waiting">{call.active ? 'Listening…' : 'No speech transcribed.'}</li>
            ) : (
              call.lines.map((line) => (
                <li
                  key={line.key}
                  className={[line.who === 'Operator' ? 'op' : 'caller', line.final ? '' : 'partial'].join(' ')}
                >
                  <span>{line.who}:</span>
                  <p>{line.text}</p>
                </li>
              ))
            )}
          </ol>
        </div>
      ) : (
        <p className="lede">No calls yet. Call the Twilio number and the conversation shows up here as it happens.</p>
      )}
    </aside>
  )
}
