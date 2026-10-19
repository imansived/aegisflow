import { MODALITY_LABEL, SEVERITY_LABEL, siteDayTime } from '../format'
import type { Point } from '../live'
import { MODALITIES, type Alert, type Snapshot, type Status } from '../types'
import { LineChart } from './Charts'
import { ModalityMark, SeverityBadge } from './Glyph'
import { SIGNAL_COLOR, SignalRing } from './SignalRing'

const SEV_VAR = { normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)' } as const

/** The right rail: everything about one device. The Signal Ring is the centrepiece; below it, the evidence. */
export function Inspector({ snap, points, alerts, status }: { snap: Snapshot | null; points: Point[]; alerts: Alert[]; status: Status | null }) {
  if (!snap) {
    return (
      <section className="rail inspector idle" aria-label="Inspector">
        <div className="empty big">
          Select a beacon in the scene, a line in the Sentinel, or press <kbd>/</kbd> to search.
        </div>
      </section>
    )
  }
  const open = alerts.find((a) => a.status !== 'closed')
  const shown = open ?? alerts[0]
  const raise = status?.alert_thresholds.raise_at

  return (
    <section className="rail inspector" aria-label={`Device ${snap.device_id}`} style={{ ['--core' as string]: SEV_VAR[snap.severity] }}>
      <header className="rail-head">
        <h2>{snap.device_id}<span className="sub"> {snap.zone ?? 'in transit'}</span></h2>
        <SeverityBadge severity={snap.severity} />
      </header>

      <SignalRing effective={snap.effective} score={snap.score} severity={snap.severity} />

      <ul className="legend-signals" aria-label="Signals">
        {MODALITIES.map((m) => (
          <li key={m} style={{ ['--c' as string]: SIGNAL_COLOR[m] }} className={snap.effective[m] >= 0.3 ? 'on' : ''} title={snap.contributions[m] ? `${MODALITY_LABEL[m]} contributes ${snap.contributions[m]!.toFixed(2)} of the score` : undefined}>
            <span className="swatch" aria-hidden="true" /><span className="nm">{MODALITY_LABEL[m]}</span>
            <span className="meter"><i style={{ width: `${Math.round(snap.effective[m] * 100)}%` }} /></span>
            <b>{snap.effective[m].toFixed(2)}</b>
          </li>
        ))}
      </ul>
      {snap.learning && <p className="note">Still learning this device’s normal behaviour; alerts are held back until baselines are ready.</p>}

      <h3>Pulse</h3>
      <LineChart points={points.map((p) => ({ t: p.t, v: p.score }))} reference={raise} referenceLabel={raise !== undefined ? `alert ${raise.toFixed(2)}` : undefined} ariaLabel={`Anomaly score history for ${snap.device_id}`} />

      <h3>{open ? 'Why it was flagged' : shown ? 'Most recent alert' : 'Explanation'}</h3>
      {shown ? (
        <div className="why" style={{ ['--c' as string]: SEV_VAR[shown.severity] }}>
          <div className="why-head"><span><SeverityBadge severity={shown.severity} /> · peak {shown.peak_score.toFixed(2)} · {siteDayTime(shown.started)}</span><span className={`state ${shown.status === 'closed' ? '' : 'open'}`}>{shown.status === 'closed' ? 'Closed' : 'Open'}</span></div>
          <ol>
            {shown.factors.map((f) => (
              <li key={f.modality} style={{ ['--c' as string]: SIGNAL_COLOR[f.modality] }}>
                <div className="fh"><ModalityMark modality={f.modality} /> <span className="sub">contributes {f.contribution.toFixed(2)}</span></div>
                <div className="fr">{f.reasons.length ? f.reasons.join('; ') : 'Elevated, with no single dominant feature.'}</div>
              </li>
            ))}
          </ol>
          <p className="fine">The score ranks how unusual this is. It is not a probability that an attack occurred.</p>
        </div>
      ) : (
        <p className="note">No alerts for this device. Current state: {SEVERITY_LABEL[snap.severity].toLowerCase()}.</p>
      )}

      <h3>Telemetry</h3>
      <dl className="readouts">
        <div><dt>Battery</dt><dd>{snap.battery.toFixed(0)}<small>%</small></dd></div>
        <div><dt>CPU</dt><dd>{snap.cpu_usage.toFixed(0)}<small>%</small></dd></div>
        <div><dt>Temp</dt><dd>{snap.temperature.toFixed(0)}<small>°C</small></dd></div>
        <div><dt>Links</dt><dd>{snap.network_connections}</dd></div>
        <div><dt>Nearby</dt><dd>{snap.nearby_devices}</dd></div>
      </dl>
    </section>
  )
}
