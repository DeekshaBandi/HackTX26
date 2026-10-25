export type Parts = {
  topic: number
  emotion: number
  ask: number
}

export type Reason = {
  label: string
  detail: string
}

export type Student = {
  id: string
  label: string
  short: string
  color: string
  calledOn: string
  summary: string
  parts: Parts
  why: Reason[]
  score: number
  x: number
  z: number
}

/** Hardcoded weights. The inputs below are fixed sample data, not a live model. */
export function compatibility(parts: Parts): number {
  const raw = 0.5 * parts.topic + 0.3 * parts.emotion + 0.2 * parts.ask
  return Math.round(raw * 100)
}

export function scoreMath(parts: Parts): string {
  const raw = 0.5 * parts.topic + 0.3 * parts.emotion + 0.2 * parts.ask
  const n = (v: number) => v.toFixed(2)
  return `0.5 × ${n(parts.topic)}  +  0.3 × ${n(parts.emotion)}  +  0.2 × ${n(parts.ask)}  =  ${raw.toFixed(2)}  →  ${Math.round(raw * 100)}`
}

function pinRadius(score: number): number {
  const t = Math.min(1, Math.max(0, (score - 25) / 69))
  return 46 - t * 24
}

type Draft = Omit<Student, 'score' | 'x' | 'z'>

const drafts: Draft[] = [
  {
    id: 's03',
    label: 'Student · 03',
    short: 'S03',
    color: '#d9652b',
    calledOn: 'Fri, Oct 23, 2026',
    summary:
      'Also computer science. Bombed the last data structures midterm and said they sit in office hours nodding. They asked for another CS student who has felt behind, not a TA.',
    parts: { topic: 0.96, emotion: 0.9, ask: 0.95 },
    why: [
      {
        label: 'Same subject',
        detail: 'Both named computer science and data structures. That overlap is most of the topic score.',
      },
      {
        label: 'Same feeling',
        detail: 'Both sound anxious and tired. Neither one is looking for a pep talk.',
      },
      {
        label: 'Same ask',
        detail: 'Both asked for a classmate who has been behind, and both waved off a tutor.',
      },
    ],
  },
  {
    id: 's11',
    label: 'Student · 11',
    short: 'S11',
    color: '#3c7a4a',
    calledOn: 'Thu, Oct 22, 2026',
    summary:
      'Computer science, second year. Said the major feels like a room where everyone else already gets it. They want someone in the same classes, not advice.',
    parts: { topic: 0.88, emotion: 0.78, ask: 0.84 },
    why: [
      {
        label: 'Same subject',
        detail: 'Same major. They talked about discrete math and data structures, which sits next to this call.',
      },
      {
        label: 'Same feeling',
        detail: 'Lonely inside the major, same shape as this call. Less panic about one quiz.',
      },
      {
        label: 'Same ask',
        detail: 'Asked for a peer. Did not ask to be taught.',
      },
    ],
  },
  {
    id: 's08',
    label: 'Student · 08',
    short: 'S08',
    color: '#b3403a',
    calledOn: 'Tue, Oct 20, 2026',
    summary:
      'Electrical engineering. Failed a circuits quiz and does not want to tell the group chat. Asked for someone who has already failed something this semester.',
    parts: { topic: 0.74, emotion: 0.8, ask: 0.7 },
    why: [
      {
        label: 'Nearby subject',
        detail: 'Engineering, not CS. The quiz panic matches. The course does not.',
      },
      {
        label: 'Same feeling',
        detail: 'Same shame-and-tired mix after a bad quiz.',
      },
      {
        label: 'Close ask',
        detail: 'Wanted a person who has already failed. That is near “felt behind,” without the same major.',
      },
    ],
  },
  {
    id: 's02',
    label: 'Student · 02',
    short: 'S02',
    color: '#a67c2d',
    calledOn: 'Mon, Oct 19, 2026',
    summary:
      'Computer science. A group-project partner stopped answering. They feel like they are faking it in standup and asked if anyone else is lost in the class.',
    parts: { topic: 0.8, emotion: 0.62, ask: 0.68 },
    why: [
      {
        label: 'Same subject',
        detail: 'Same major, different sore spot. Theirs is a team that went quiet, not a quiz score.',
      },
      {
        label: 'Nearby feeling',
        detail: 'More left out than quiz-anxious. Still tired of pretending.',
      },
      {
        label: 'Close ask',
        detail: 'Asked for someone else who is lost. Close to this caller’s ask.',
      },
    ],
  },
  {
    id: 's15',
    label: 'Student · 15',
    short: 'S15',
    color: '#3d7a78',
    calledOn: 'Sat, Oct 24, 2026',
    summary:
      'Math major, behind in proofs, lives in the library. Wants a study friend who will admit they are stuck.',
    parts: { topic: 0.48, emotion: 0.7, ask: 0.66 },
    why: [
      {
        label: 'Different subject',
        detail: 'Proofs, not data structures. “Behind in the hard class” is the only real overlap.',
      },
      {
        label: 'Same feeling',
        detail: 'Lonely and tired. The shape matches better than the major does.',
      },
      {
        label: 'Nearby ask',
        detail: 'A study friend is close to a peer, but they want to work problems, not only talk.',
      },
    ],
  },
  {
    id: 's19',
    label: 'Student · 19',
    short: 'S19',
    color: '#7a5b8a',
    calledOn: 'Wed, Oct 21, 2026',
    summary:
      'Design student. Eats alone. Studio critiques feel personal. Wants one person who will not turn it into advice.',
    parts: { topic: 0.36, emotion: 0.68, ask: 0.42 },
    why: [
      {
        label: 'Different subject',
        detail: 'No shared classes. Studio, not computer science.',
      },
      {
        label: 'Nearby feeling',
        detail: 'Isolation is real on both sides. Theirs is social. This call’s is inside the major.',
      },
      {
        label: 'Weak ask',
        detail: '“Don’t advise me” brushes “not a tutor,” and then the overlap stops.',
      },
    ],
  },
  {
    id: 's06',
    label: 'Student · 06',
    short: 'S06',
    color: '#6e6256',
    calledOn: 'Sun, Oct 18, 2026',
    summary:
      'Biology. Family keeps asking about grades. They want to say out loud that they are scared of orgo, not to form a study pair.',
    parts: { topic: 0.3, emotion: 0.74, ask: 0.4 },
    why: [
      {
        label: 'Different subject',
        detail: 'Almost no course overlap. Orgo is not data structures.',
      },
      {
        label: 'Nearby feeling',
        detail: 'Scared and tired lines up a little. The subject does not.',
      },
      {
        label: 'Different ask',
        detail: 'They wanted to vent. This caller wanted a CS classmate.',
      },
    ],
  },
  {
    id: 's04',
    label: 'Student · 04',
    short: 'S04',
    color: '#b5734a',
    calledOn: 'Thu, Oct 22, 2026',
    summary:
      'Business. Burned out on recruiting, not on a class. Wants to complain to someone outside their friend group.',
    parts: { topic: 0.28, emotion: 0.6, ask: 0.55 },
    why: [
      {
        label: 'Different subject',
        detail: 'No academic overlap with data structures. The stress is recruiting.',
      },
      {
        label: 'Partial feeling',
        detail: 'Tired, but not anxious about a course and not pretending in office hours.',
      },
      {
        label: 'Different ask',
        detail: 'A stranger to vent to. This caller asked for a classmate in the same spot.',
      },
    ],
  },
  {
    id: 's09',
    label: 'Student · 09',
    short: 'S09',
    color: '#4f6f8f',
    calledOn: 'Fri, Oct 23, 2026',
    summary:
      'Music performance. Shaking before juries. Wants another performer who gets stage fear.',
    parts: { topic: 0.2, emotion: 0.58, ask: 0.28 },
    why: [
      {
        label: 'Different subject',
        detail: 'No shared work. Juries are not quizzes.',
      },
      {
        label: 'Partial feeling',
        detail: 'Anxiety is there, but it is performance nerves, not schoolwork.',
      },
      {
        label: 'Different ask',
        detail: 'They want a peer in their own world. It is a different world.',
      },
    ],
  },
  {
    id: 's12',
    label: 'Student · 12',
    short: 'S12',
    color: '#8a6248',
    calledOn: 'Sun, Oct 18, 2026',
    summary:
      'Premed. Wants a competitive study partner to stay ahead in biochem. Says falling behind is not an option.',
    parts: { topic: 0.18, emotion: 0.4, ask: 0.22 },
    why: [
      {
        label: 'Different subject',
        detail: 'Biochem, and they want to stay ahead. This caller is already behind in CS.',
      },
      {
        label: 'Opposite feeling',
        detail: 'Driven, not tired of pretending. The emotion score stays low on purpose.',
      },
      {
        label: 'Opposite ask',
        detail: 'They want an edge. This caller wants someone who has already felt behind.',
      },
    ],
  },
]

function place(rows: Draft[]): Student[] {
  const scored = rows.map((row) => ({ ...row, score: compatibility(row.parts) }))
  const ranked = [...scored].sort((a, b) => b.score - a.score)
  const pos = new Map<string, { x: number; z: number }>()
  ranked.forEach((row, i) => {
    const angle = -Math.PI / 2 + (i / ranked.length) * Math.PI * 2
    const radius = pinRadius(row.score)
    pos.set(row.id, {
      x: Math.cos(angle) * radius,
      z: Math.sin(angle) * radius,
    })
  })
  return scored.map((row) => {
    const at = pos.get(row.id)!
    return { ...row, x: at.x, z: at.z }
  })
}

export const STUDENTS: Student[] = place(drafts)

export const RANKED: Student[] = [...STUDENTS].sort((a, b) => b.score - a.score)

export const BEST: Student = RANKED[0]

export const CALLER = {
  id: 'you',
  label: 'You',
  short: 'YOU',
  calledOn: 'Sat, Oct 24, 2026 · on the call',
  summary:
    'A computer science student, still in data structures, bombing the quizzes. They do not have anyone in the major to say that to. They are tired of acting fine in office hours. They want a classmate who has felt behind. Not a tutor.',
}
