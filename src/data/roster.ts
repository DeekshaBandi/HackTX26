import roster from '../../data/students.json' with { type: 'json' }

export type RosterStudent = {
  name: string
  major: string
  situation: string
  stage: string
  interests: string
  student_id: string
  school: string
  year: string
  theme: string
}

/** Name and school stay on the record and off the judge screen. */
export const ROSTER = roster as RosterStudent[]

export function recordText(row: RosterStudent): string {
  return `${row.major} ${row.situation} ${row.theme} ${row.interests} ${row.stage} ${row.year}`.toLowerCase()
}
