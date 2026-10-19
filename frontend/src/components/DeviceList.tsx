import { sevRank } from '../format'
import type { Snapshot } from '../types'
import { SeverityBadge } from './Glyph'

export function DeviceList({ devices, selected, onSelect }: { devices: Record<string, Snapshot>; selected: string | null; onSelect: (id: string) => void }) {
  const rows = Object.values(devices).sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || b.score - a.score || a.device_id.localeCompare(b.device_id))
  return (
    <section className="card" aria-label="Devices">
      <header><h2>Devices</h2><span className="sub">{rows.length} reporting · highest risk first</span></header>
      {rows.length === 0 ? (
        <div className="empty">No devices yet. Start the demo to simulate a fleet.</div>
      ) : (
        <ul className="list scroll" style={{ maxHeight: 360 }}>
          {rows.map((d) => (
            <li key={d.device_id}>
              <button className={`row sev-${d.severity}`} aria-pressed={selected === d.device_id} onClick={() => onSelect(d.device_id)}>
                <span className="top">
                  <span><span className="id">{d.device_id}</span> <span className="meta">{d.zone ?? 'in transit'}{d.learning ? ' · learning' : ''}</span></span>
                  <SeverityBadge severity={d.severity} />
                </span>
                <span className="scorebar" aria-label={`risk score ${d.score.toFixed(2)}`}><i style={{ width: `${Math.round(d.score * 100)}%` }} /></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
