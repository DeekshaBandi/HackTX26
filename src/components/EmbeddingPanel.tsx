import { EMBED_WHY, LINES, TOKENS, axisById } from '../data/call.ts'

const EMOTION_COLOR: Record<string, string> = {
  anxious: '#9a3412',
  lonely: '#245e4a',
  tired: '#6b5130',
  hopeful: '#3c7a4a',
  angry: '#8d3b32',
}

type Props = {
  revealedLines: number
  selectedId: string | null
  onSelect: (id: string | null) => void
  onReplay: () => void
  nearest: { label: string; score: number } | null
}

export function EmbeddingPanel({ revealedLines, selectedId, onSelect, onReplay, nearest }: Props) {
  const line = revealedLines > 0 ? LINES[revealedLines - 1] : null
  const token = TOKENS.find((item) => item.id === selectedId) ?? null
  const done = revealedLines >= LINES.length

  return (
    <aside className="panel">
      <div className="panel-top">
        <div>
          <p className="kicker">Embedding</p>
          <h2>What the call becomes</h2>
        </div>
        <button type="button" className="text-btn" onClick={onReplay}>
          Play again
        </button>
      </div>
      <p className="lede">
        Hardcoded transcript. Each phrase leaves you and lands in the bucket the AI files it under.
      </p>

      <h4>Summary</h4>
      <blockquote>
        {line?.summary ?? 'Waiting on the first sentence.'}
      </blockquote>

      <h4>Emotion</h4>
      {line ? (
        <ul className="emotion-list">
          {line.emotions.map((emotion) => (
            <li key={emotion.id}>
              <div className="part-top">
                <span>{emotion.label}</span>
                <span>{emotion.value}</span>
              </div>
              <div className="track">
                <span
                  style={{
                    width: `${emotion.value}%`,
                    background: EMOTION_COLOR[emotion.id] ?? '#9a4e24',
                  }}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="note">Nothing filed yet. Anxious, lonely, tired, hopeful, and angry will show up as the sentences land.</p>
      )}

      <h4>Why the embedding looks like this</h4>
      <p className="why-copy">{line ? EMBED_WHY : 'The point starts on you. It only moves once a sentence has somewhere to go.'}</p>

      {line && (
        <ul className="axis-list">
          {TOKENS.filter((item) => item.line < revealedLines).map((item) => {
            const axis = axisById(item.axis)
            const on = item.id === token?.id
            return (
              <li key={item.id}>
                <button
                  type="button"
                  className={on ? 'axis-btn on' : 'axis-btn'}
                  onClick={() => onSelect(on ? null : item.id)}
                >
                  <span className="axis-name">
                    <i style={{ background: axis.ink }} />
                    {axis.label}
                    <em>{line.vector[axis.id].toFixed(2)}</em>
                  </span>
                  <span className="axis-quote">“{item.quote}”</span>
                  <span className="axis-because">{item.because}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <h4>Transcript</h4>
      <ol className="call-log" aria-live="polite">
        {LINES.map((item, index) => (
          <li key={item.clock} className={index >= revealedLines ? 'future' : index === revealedLines - 1 ? 'on' : ''}>
            <span>{item.clock}</span>
            <p>{item.text}</p>
          </li>
        ))}
      </ol>

      {done && (
        <div className="ready">
          <p>
            {nearest
              ? `Filed. The people map on the right sits this point nearest ${nearest.label}, compatibility ${nearest.score}.`
              : 'Filed. The people map is still loading the search.'}
          </p>
        </div>
      )}
    </aside>
  )
}
