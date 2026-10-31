import { useState } from 'react'
import { MODALITY_LABEL, SEVERITY_LABEL, siteDayTime } from '../format'
import type { Alert } from '../types'
import { SeverityBadge } from '../components/Glyph'

/** Every alert, newest first, with the full account of why it fired: which signals, how much each contributed. */
export default function Anomalies({ alerts, onOpen }: { alerts: Alert[]; onOpen: (id: string) => void }) {
  const [openOnly, setOpenOnly] = useState(false)
  const [pick, setPick] = useState<string | null>(null)
  const shown = openOnly ? alerts.filter((a) => a.status !== 'closed') : alerts
  const cur = shown.find((a) => a.alert_id === pick) ?? shown[0]

  return (
    <div className="page two-col">
      <section className="card" aria-label="Alerts">
        <header><h2>Alerts</h2>
          <div className="seg" role="group" aria-label="Alert filter">
            <button aria-pressed={!openOnly} onClick={() => setOpenOnly(false)}>All</button>
            <button aria-pressed={openOnly} onClick={() => setOpenOnly(true)}>Open</button>
          </div>
        </header>
        {shown.length === 0 ? <div className="empty">{openOnly ? 'No open alerts.' : 'No alerts yet. Inject a threat in Scenarios.'}</div> : (
          <ul className="alert-list">
            {shown.map((a) => (
              <li key={a.alert_id}>
                <button className={`acard sev-${a.severity}`} aria-pressed={cur?.alert_id === a.alert_id} onClick={() => setPick(a.alert_id)}>
                  <span className="acard-top"><b>{a.device_id}</b><SeverityBadge severity={a.severity} /><span className={`state ${a.status === 'closed' ? '' : 'open'}`}>{a.status === 'closed' ? 'Closed' : 'Open'}</span></span>
                  <span className="acard-why">{a.factors[0]?.reasons[0] ?? 'Elevated behaviour across several signals'}</span>
                  <span className="meta">{siteDayTime(a.started)} · peak {a.peak_score.toFixed(2)} · {a.factors.length} signal{a.factors.length === 1 ? '' : 's'}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Alert detail">
        {!cur ? <div className="empty">Select an alert to see why it fired.</div> : (
          <>
            <header><h2>{cur.device_id}</h2><SeverityBadge severity={cur.severity} /></header>
            <p className="note">{SEVERITY_LABEL[cur.severity]} · started {siteDayTime(cur.started)}{cur.ended ? ` · ended ${siteDayTime(cur.ended)}` : ' · still open'} · peak {cur.peak_score.toFixed(2)} · {cur.fusion} fusion</p>
            <h3 className="h3">Signals that combined</h3>
            <ol className="why-list">
              {cur.factors.map((f) => (
                <li key={f.modality}>
                  <span className={`sigchip m-${f.modality}`}><i />{MODALITY_LABEL[f.modality]}</span>
                  <span className="meter"><i style={{ width: `${Math.round(f.effective_score * 100)}%`, background: `var(--sig-${f.modality})` }} /></span>
                  <b>{f.effective_score.toFixed(2)}</b>
                  <span className="meta">contributes {f.contribution.toFixed(2)}</span>
                  <span className="reason">{f.reasons.join('; ') || 'elevated'}</span>
                </li>
              ))}
            </ol>
            {cur.observation && <p className="obs"><b>What this alert rests on.</b> {cur.observation}</p>}
            {cur.explanation && cur.explanation !== 'x' && (
              <details className="tech">
                <summary>Full technical explanation</summary>
                <pre className="tech-text">{cur.explanation}</pre>
              </details>
            )}
            <p className="fine">Opened when the fused score reached the alert level (and the device had finished learning); it closes after 60 s below the clear level. Severity labels follow the score, so a device can read Normal again while its alert is still open.</p>
            <p className="fine">The fused score ranks how unusual this is across all signals. It is not a probability that an attack occurred.</p>
            <div className="btns" style={{ marginTop: 12 }}><button className="btn" onClick={() => onOpen(cur.device_id)}>Show {cur.device_id} on the map</button></div>
          </>
        )}
      </section>
    </div>
  )
}
