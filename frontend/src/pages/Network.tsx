import { MODALITY_LABEL, SEVERITY_LABEL, deviceState, isDegraded, lostSignals, lostText, sevRank } from '../format'
import { MODALITIES, type Snapshot } from '../types'
import { StateBadge } from '../components/Glyph'
import { ACTIVE_LEVEL, SIGNAL_COLOR } from '../components/SignalRing'

/** Every device at a glance, riskiest first. The four small bars are the four signals; a lit bar is one that is
 *  contributing. Selecting a row takes you to that device on the map. */
export default function Network({ devices, selected, onOpen }: { devices: Record<string, Snapshot>; selected: string | null; onOpen: (id: string) => void }) {
  const rows = Object.values(devices).sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || Number(isDegraded(b)) - Number(isDegraded(a)) || b.score - a.score || a.device_id.localeCompare(b.device_id))
  return (
    <div className="page">
      <section className="card" aria-label="Device network">
        <header><h2>Device network</h2><span className="sub">{rows.length} connected · riskiest first</span></header>
        {rows.length === 0 ? <div className="empty">No devices yet. Start the fleet in Scenarios.</div> : (
          <div className="table-wrap">
            <table className="data devices">
              <thead><tr><th>Device</th><th>State</th><th>Risk</th><th>Signals</th><th>Zone</th><th>Battery</th><th>CPU</th><th>Connections</th></tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.device_id} className={d.device_id === selected ? 'sel' : ''}>
                    <td><button className="link strong" onClick={() => onOpen(d.device_id)}>{d.device_id}</button>{d.learning && <span className="sub"> · learning</span>}</td>
                    <td><StateBadge snap={d} />{isDegraded(d) && d.severity !== 'normal' && <span className="sub"> · partly observed</span>}</td>
                    <td><span className={`riskbar sev-${d.severity}`} role="img" aria-label={`${deviceState(d) === 'degraded' ? 'Degraded' : SEVERITY_LABEL[d.severity]}, score ${d.score.toFixed(2)}`}><i style={{ width: `${Math.round(d.score * 100)}%` }} /></span> <b>{d.score.toFixed(2)}</b></td>
                    <td>
                      <span className="sigbars" role="img" aria-label={MODALITIES.map((m) => (lostSignals(d).includes(m) ? `${MODALITY_LABEL[m]} unavailable` : `${MODALITY_LABEL[m]} ${d.effective[m].toFixed(2)}`)).join(', ')}>
                        {MODALITIES.map((m) => <i key={m} className={lostSignals(d).includes(m) ? 'lost' : d.effective[m] >= ACTIVE_LEVEL ? 'on' : ''} style={{ ['--c' as string]: SIGNAL_COLOR[m], height: `${6 + d.effective[m] * 16}px` }} />)}
                      </span>
                    </td>
                    <td>{d.zone ?? 'in transit'}{d.restricted ? ' · restricted' : ''}{d.position_stale ? ' · last known' : ''}{isDegraded(d) && <div className="sub">{lostText(d)} unavailable</div>}</td>
                    <td>{d.battery === null ? <span className="na" title="unavailable">—</span> : `${d.battery.toFixed(0)}%`}</td>
                    <td>{d.cpu_usage === null ? <span className="na" title="unavailable">—</span> : `${d.cpu_usage.toFixed(0)}%`}</td>
                    <td>{d.network_connections === null ? <span className="na" title="unavailable">—</span> : d.network_connections}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
