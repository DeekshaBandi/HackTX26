import { LINES } from '../data/call.ts'
import { SEARCH_STEPS } from '../lib/serpapiLive.ts'

type Props = {
  doneIds: string[]
  embedStep: number
  searchBack: boolean
}

export function MapLoading({ doneIds, embedStep, searchBack }: Props) {
  const done = new Set(doneIds)
  const embedFiled = embedStep >= LINES.length
  return (
    <div className="map-loading" role="status" aria-live="polite">
      <div className="load-pin" aria-hidden />
      <p className="load-word">Loading</p>
      <h2>Distances stay hidden</h2>
      <p className="lede">
        Pins are drawn from the center after the embedding is filed and the live search returns. Nothing on this side is a saved distance.
      </p>
      <ul className="load-wait">
        <li className={embedFiled ? 'in' : ''}>
          <i />
          Embedding {Math.min(embedStep, LINES.length)} of {LINES.length}
        </li>
        <li className={searchBack ? 'in' : ''}>
          <i />
          Search {done.size} of {SEARCH_STEPS.length}
        </li>
      </ul>
      <ul className="load-steps">
        {SEARCH_STEPS.map((step) => (
          <li key={step.id} className={done.has(step.id) ? 'in' : ''}>
            <i />
            {step.label}
          </li>
        ))}
      </ul>
    </div>
  )
}
