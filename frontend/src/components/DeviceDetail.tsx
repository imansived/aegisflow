import { MODALITY_LABEL, SEVERITY_LABEL, siteDayTime } from '../format'
import type { Point } from '../live'
import { MODALITIES, type Alert, type Snapshot, type Status } from '../types'
import { HBars, LineChart } from './Charts'
import { ModalityMark, SeverityBadge } from './Glyph'

const SEV_VAR = { normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)' } as const

export function DeviceDetail({ snap, points, alerts, status }: { snap: Snapshot | null; points: Point[]; alerts: Alert[]; status: Status | null }) {
  if (!snap) return <section className="card"><div className="empty">Select a device on the map or in the list to see its behaviour and why it was flagged.</div></section>

  const open = alerts.find((a) => a.status !== 'closed')
  const shown = open ?? alerts[0]
  const raise = status?.alert_thresholds.raise_at

  return (
    <section className="card" aria-label={`Device ${snap.device_id}`}>
      <header>
        <h2>{snap.device_id} <span className="sub">{snap.zone ?? 'in transit'}</span></h2>
        <span style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <SeverityBadge severity={snap.severity} />
          <b>{snap.score.toFixed(2)}</b>
        </span>
      </header>

      <h3>Anomaly score over time</h3>
      <LineChart
        points={points.map((p) => ({ t: p.t, v: p.score }))} reference={raise}
        referenceLabel={raise !== undefined ? `alert ${raise.toFixed(2)}` : undefined}
        ariaLabel={`Anomaly score history for ${snap.device_id}`}
      />

      <h3>Signals behind the score <span className="sub">(0 = ordinary, 1 = extreme)</span></h3>
      <HBars
        ariaLabel={`Per-signal scores for ${snap.device_id}`} max={1}
        rows={MODALITIES.map((m) => ({
          label: MODALITY_LABEL[m], value: snap.effective[m], markClass: `m-${m}`,
          display: snap.effective[m].toFixed(2),
          title: snap.contributions[m] ? `${MODALITY_LABEL[m]} contributes ${snap.contributions[m]!.toFixed(2)} to the combined score` : undefined,
        }))}
      />
      {snap.learning && <p className="notice" style={{ marginTop: 8 }}>Still learning this device’s normal behaviour: alerts are held back until baselines are ready.</p>}

      <h3>{open ? 'Why it was flagged' : shown ? 'Most recent alert' : 'Alert explanation'}</h3>
      {shown ? (
        <div className="why-box" style={{ ['--c' as string]: SEV_VAR[shown.severity] }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span><SeverityBadge severity={shown.severity} /> · peak {shown.peak_score.toFixed(2)} · {siteDayTime(shown.started)}</span>
            <span className="chip">{shown.status === 'closed' ? 'Closed' : 'Open'}</span>
          </div>
          <ol>
            {shown.factors.map((f) => (
              <li key={f.modality}>
                <ModalityMark modality={f.modality} /> <span className="sub">contributes {f.contribution.toFixed(2)}</span>
                <div className="notice">{f.reasons.length ? f.reasons.join('; ') : 'Elevated, with no single dominant feature.'}</div>
              </li>
            ))}
          </ol>
          <p className="note">The score ranks how unusual this is. It is not a probability that an attack occurred.</p>
        </div>
      ) : (
        <p className="notice">No alerts for this device. Its current state is {SEVERITY_LABEL[snap.severity].toLowerCase()}.</p>
      )}

      <h3>Latest telemetry</h3>
      <div className="stats">
        <div className="stat"><div className="label">Battery</div><div className="value">{snap.battery.toFixed(0)}%</div></div>
        <div className="stat"><div className="label">CPU</div><div className="value">{snap.cpu_usage.toFixed(0)}%</div></div>
        <div className="stat"><div className="label">Temperature</div><div className="value">{snap.temperature.toFixed(0)} °C</div></div>
        <div className="stat"><div className="label">Connections</div><div className="value">{snap.network_connections}</div></div>
        <div className="stat"><div className="label">Nearby devices</div><div className="value">{snap.nearby_devices}</div></div>
      </div>
    </section>
  )
}
