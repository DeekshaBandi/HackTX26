import { useEffect, useMemo, useState } from 'react'
import { api, type ApiStudent, type CallEvent, type Nearby, type Pair, type StudentMatch } from './api.ts'

/** How far back to look for live calls, and how often to ask. */
const EVENT_MINUTES = 120
const EVENT_POLL_MS = 2500
const PAIRS_POLL_MS = 15000

export type LiveCall = {
  key: string
  callId: string | null
  studentId: string
  startedAt: string
  lastAt: string
  ended: boolean
  events: CallEvent[]
}

/** Events grouped into calls, newest call first. Test calls sometimes reuse a call_id, so the student is part of the key. */
function groupCalls(events: CallEvent[]): LiveCall[] {
  const calls = new Map<string, LiveCall>()
  for (const event of events) {
    const key = `${event.call_id ?? ''}|${event.student_id}`
    let call = calls.get(key)
    if (!call) {
      call = { key, callId: event.call_id, studentId: event.student_id, startedAt: event.time, lastAt: event.time, ended: false, events: [] }
      calls.set(key, call)
    }
    call.events.push(event)
    call.lastAt = event.time
    if (event.event === 'call_ended') call.ended = true
  }
  return [...calls.values()].sort((a, b) => b.lastAt.localeCompare(a.lastAt))
}

/** ?student=ID in the page URL pins the view to one student instead of the latest call. */
function pinnedStudent(): string | null {
  return new URLSearchParams(window.location.search).get('student')
}

export type JudgeData = {
  error: string | null
  students: ApiStudent[] | null
  pairs: Pair[]
  calls: LiveCall[]
  call: LiveCall | null
  callerId: string | null
  caller: ApiStudent | null
  nearby: Nearby | null
  delivered: StudentMatch[]
  callerLoading: boolean
  selectCall: (key: string | null) => void
}

export function useJudgeData(): JudgeData {
  const [error, setError] = useState<string | null>(null)
  const [students, setStudents] = useState<ApiStudent[] | null>(null)
  const [pairs, setPairs] = useState<Pair[]>([])
  const [events, setEvents] = useState<CallEvent[]>([])
  const [chosenKey, setChosenKey] = useState<string | null>(null)
  const [caller, setCaller] = useState<ApiStudent | null>(null)
  const [nearby, setNearby] = useState<Nearby | null>(null)
  const [delivered, setDelivered] = useState<StudentMatch[]>([])
  const [callerLoading, setCallerLoading] = useState(false)
  const pinned = useMemo(pinnedStudent, [])

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      api.pairs().then((rows) => !cancelled && setPairs(rows)).catch(() => {})
    }
    tick()
    const id = window.setInterval(tick, PAIRS_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer = 0
    const tick = async () => {
      try {
        const rows = await api.events(EVENT_MINUTES)
        if (!cancelled) {
          setEvents(rows)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not reach the API')
      }
      if (!cancelled) timer = window.setTimeout(tick, EVENT_POLL_MS)
    }
    tick()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [])

  const calls = useMemo(() => {
    const all = groupCalls(events)
    return pinned ? all.filter((call) => call.studentId === pinned) : all
  }, [events, pinned])

  // Reload everyone when a new call comes in, so the new caller shows up as a dot
  const callCount = calls.length
  useEffect(() => {
    let cancelled = false
    api.students()
      .then((rows) => !cancelled && setStudents(rows))
      .catch((err: Error) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [callCount])

  // Follow the newest call unless the judge picked one
  const call = calls.find((item) => item.key === chosenKey) ?? calls[0] ?? null
  const callerId = call?.studentId ?? pinned
  // Refetch the caller whenever their call logs something new (profile change, match, hang-up)
  const version = call ? `${call.key}:${call.events.length}` : pinned ?? ''

  useEffect(() => {
    if (!callerId) {
      setCaller(null)
      setNearby(null)
      setDelivered([])
      return
    }
    let cancelled = false
    setCallerLoading(true)
    Promise.all([api.student(callerId), api.nearby(callerId, 10), api.matches(callerId)])
      .then(([me, near, done]) => {
        if (cancelled) return
        setCaller(me)
        setNearby(near)
        setDelivered(done)
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setCallerLoading(false))
    return () => {
      cancelled = true
    }
  }, [callerId, version])

  return {
    error,
    students,
    pairs,
    calls,
    call,
    callerId,
    caller,
    nearby,
    delivered,
    callerLoading,
    selectCall: setChosenKey,
  }
}
