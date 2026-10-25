import { type ExpansionReport } from '../lib/expand.ts'

type Props = {
  report: ExpansionReport
  expanded: boolean
  onChange: (expanded: boolean) => void
}

export function ExpandCompare({ report, expanded, onChange }: Props) {
  return (
    <div className="compare" role="group" aria-label="Expansion comparison">
      <button type="button" aria-pressed={!expanded} onClick={() => onChange(false)}>
        <span>No expansion</span>
        <strong>{report.literal}</strong>
        <em>as typed</em>
      </button>
      <button type="button" aria-pressed={expanded} onClick={() => onChange(true)}>
        <span>With expansion</span>
        <strong>{report.expanded}</strong>
        <em>+{report.added} in the file</em>
      </button>
    </div>
  )
}
