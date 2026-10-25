export type AxisId = 'academic' | 'major' | 'isolation' | 'masking' | 'peer'

export type Axis = {
  id: AxisId
  label: string
  detail: string
  position: [number, number, number]
  disc: string
  ink: string
}

export type CallToken = {
  id: string
  line: number
  quote: string
  axis: AxisId
  because: string
}

export type EmotionSnap = {
  id: string
  label: string
  value: number
}

export type CallLine = {
  clock: string
  text: string
  summary: string
  emotions: EmotionSnap[]
  vector: Record<AxisId, number>
}

function polar(deg: number, radius: number): [number, number, number] {
  const a = (deg * Math.PI) / 180
  return [Math.cos(a) * radius, 0, Math.sin(a) * radius]
}

const specs: Omit<Axis, 'position'>[] = [
  {
    id: 'academic',
    label: 'Academic strain',
    detail: 'A course is going badly, and they used a failure word for it.',
    disc: '#f0d2c2',
    ink: '#9a3412',
  },
  {
    id: 'major',
    label: 'CS major',
    detail: 'They named computer science, so the point sits with other CS students.',
    disc: '#ead7b8',
    ink: '#8a5a12',
  },
  {
    id: 'isolation',
    label: 'Alone in the major',
    detail: 'The lonely part is specific. Nobody in the major, not nobody at all.',
    disc: '#d5e0d4',
    ink: '#245e4a',
  },
  {
    id: 'masking',
    label: 'Tired of pretending',
    detail: 'They are keeping a front in office hours. Tired, not looking for a fix.',
    disc: '#e7dcc4',
    ink: '#6b5130',
  },
  {
    id: 'peer',
    label: 'Wants a peer',
    detail: 'They said who they want and who they do not. A classmate, not a tutor.',
    disc: '#f3d7c0',
    ink: '#9a4e24',
  },
]

export const AXES: Axis[] = specs.map((axis, i) => ({
  ...axis,
  position: polar(-90 + i * 72, 30),
}))

const axisMap = new Map(AXES.map((axis) => [axis.id, axis]))

export function axisById(id: AxisId): Axis {
  const axis = axisMap.get(id)
  if (!axis) throw new Error(`Missing axis ${id}`)
  return axis
}

export const TOKENS: CallToken[] = [
  {
    id: 't-major',
    line: 0,
    quote: 'in computer science',
    axis: 'major',
    because: 'They named the major. That pulls the point toward other CS students, not toward anyone who is stressed.',
  },
  {
    id: 't-academic',
    line: 0,
    quote: 'bombing the data structures quizzes',
    axis: 'academic',
    because: '“Bombing” is a failure word tied to a course. It weighs more than a vague “school is hard.”',
  },
  {
    id: 't-isolation',
    line: 1,
    quote: 'anyone in my major',
    axis: 'isolation',
    because: 'The lonely part is specific: nobody in the major. It does not land in a general “I have no friends” bucket.',
  },
  {
    id: 't-masking',
    line: 2,
    quote: 'pretending I’m fine',
    axis: 'masking',
    because: 'They are keeping a front in office hours. The feeling filed here is tired, not angry, and they are not asking to be fixed.',
  },
  {
    id: 't-peer',
    line: 3,
    quote: 'not a tutor',
    axis: 'peer',
    because: 'They said who they want and who they don’t. The point moves toward peers and away from people who asked for advice.',
  },
]

const emptyVector: Record<AxisId, number> = {
  academic: 0.05,
  major: 0.05,
  isolation: 0.05,
  masking: 0.05,
  peer: 0.05,
}

export const LINES: CallLine[] = [
  {
    clock: '0:14',
    text: 'I’ve been in computer science and I keep bombing the data structures quizzes.',
    summary: 'Computer science student, bombing data structures quizzes.',
    emotions: [
      { id: 'anxious', label: 'Anxious', value: 76 },
      { id: 'lonely', label: 'Lonely', value: 12 },
      { id: 'tired', label: 'Tired', value: 20 },
      { id: 'hopeful', label: 'Hopeful', value: 18 },
      { id: 'angry', label: 'Angry', value: 8 },
    ],
    vector: { ...emptyVector, academic: 0.86, major: 0.8 },
  },
  {
    clock: '0:28',
    text: 'I don’t really have anyone in my major I can talk to about it.',
    summary: 'CS student, behind in data structures, with nobody in the major to tell.',
    emotions: [
      { id: 'anxious', label: 'Anxious', value: 74 },
      { id: 'lonely', label: 'Lonely', value: 71 },
      { id: 'tired', label: 'Tired', value: 28 },
      { id: 'hopeful', label: 'Hopeful', value: 16 },
      { id: 'angry', label: 'Angry', value: 10 },
    ],
    vector: { academic: 0.84, major: 0.82, isolation: 0.86, masking: 0.1, peer: 0.2 },
  },
  {
    clock: '0:41',
    text: 'I’m not failing exactly. I’m just tired of pretending I’m fine in office hours.',
    summary: 'Still in the class, not failing, and tired of acting fine in office hours. No one in the major knows.',
    emotions: [
      { id: 'anxious', label: 'Anxious', value: 70 },
      { id: 'lonely', label: 'Lonely', value: 68 },
      { id: 'tired', label: 'Tired', value: 72 },
      { id: 'hopeful', label: 'Hopeful', value: 22 },
      { id: 'angry', label: 'Angry', value: 14 },
    ],
    vector: { academic: 0.82, major: 0.8, isolation: 0.84, masking: 0.78, peer: 0.25 },
  },
  {
    clock: '0:55',
    text: 'I think I want someone who’s also a CS student and has felt behind. Not a tutor.',
    summary:
      'A computer science student, still in data structures, bombing the quizzes. They don’t have anyone in the major to say that to. They’re tired of acting fine in office hours. They want a classmate who has felt behind. Not a tutor.',
    emotions: [
      { id: 'anxious', label: 'Anxious', value: 68 },
      { id: 'lonely', label: 'Lonely', value: 70 },
      { id: 'tired', label: 'Tired', value: 66 },
      { id: 'hopeful', label: 'Hopeful', value: 34 },
      { id: 'angry', label: 'Angry', value: 12 },
    ],
    vector: { academic: 0.8, major: 0.84, isolation: 0.86, masking: 0.74, peer: 0.9 },
  },
]

export const EMBED_WHY =
  'Each sentence leaves you and lands in one bucket. The embedding is where those buckets sit, and how hard each sentence pulled. Academic strain, the CS major, and having nobody in that major are the heavy pulls. “Not a tutor” keeps the point away from people who asked for advice. Hope stays low because they asked for company, not a rescue. Anger stays low because they never blamed anyone.'
