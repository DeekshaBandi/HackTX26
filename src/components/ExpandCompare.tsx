import { CALL_QUERY, UNFINISHED_TAIL, type ExpansionReport } from '../lib/expand.ts'
import { CURSOR_VOLUME_PROMPT, VOLUME_PROBE_SOURCE, predictSimilarRequests } from '../lib/serpapiVolume.ts'

type Props = {
  report: ExpansionReport
  expanded: boolean
  onChange: (expanded: boolean) => void
}

export function ExpandCompare({ report, expanded, onChange }: Props) {
  const autocomplete = report.engines.find((engine) => engine.id === 'autocomplete')
  const rest = report.engines.filter((engine) => engine.id !== 'autocomplete')

  return (
    <section className="expand">
      <p className="kicker">SerpAPI expansion</p>
      <p className="query">“{CALL_QUERY}”</p>
      <p className="note">
        They did not finish “{UNFINISHED_TAIL}”. Counts are from {report.total.toLocaleString()} rows in data/students.json. The live SerpAPI call is not run.
      </p>

      <div className="compare" role="group" aria-label="Expansion comparison">
        <button type="button" aria-pressed={!expanded} onClick={() => onChange(false)}>
          <span>No expansion</span>
          <strong>{report.literal}</strong>
          <em>as typed</em>
        </button>
        <button type="button" aria-pressed={expanded} onClick={() => onChange(true)}>
          <span>With expansion</span>
          <strong>{report.expanded}</strong>
          <em>+{report.added} from the engines</em>
        </button>
      </div>

      {autocomplete && (
        <div className="auto">
          <h4>Google Autocomplete</h4>
          <p className="note">engine=google_autocomplete. The word was cut off, so these are the completions. They are searched too.</p>
          <ul>
            {autocomplete.phrases.map((phrase) => (
              <li key={phrase.text}>
                <span>{phrase.text}</span>
                <em>{phrase.hits}</em>
              </li>
            ))}
          </ul>
        </div>
      )}

      <h4>Other engines</h4>
      <ul className="engine-list">
        {rest.map((engine) => (
          <li key={engine.id}>
            <div className="part-top">
              <span>{engine.label}</span>
              <span>{engine.hits}</span>
            </div>
            <p className="note">{engine.kind} · engine={engine.engine}</p>
            <p>{engine.phrases.map((phrase) => `${phrase.text} (${phrase.hits})`).join(' · ')}</p>
          </li>
        ))}
      </ul>

      <details className="call-fold">
        <summary>Cursor prompt for the volume check</summary>
        <pre className="code-block">{CURSOR_VOLUME_PROMPT}</pre>
        <p className="note">
          {predictSimilarRequests.name} is in the repo and returns null while the key is blank. It is not called.
        </p>
        <pre className="code-block">{VOLUME_PROBE_SOURCE}</pre>
      </details>
    </section>
  )
}
