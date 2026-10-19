import type { DemoStatus } from '../types'

const LINES = [
  'registering the fleet',
  'replaying 34 simulated hours of ordinary behaviour',
  'learning each device’s normal places, traffic and battery habits',
  'building baselines per device and per time of day',
  'arming the correlation engine',
]

/** Shown while the simulated fleet calibrates. The ring fills with the REAL warm-up progress from the server:
 *  this minute is the system learning what "normal" means, which is the whole premise. */
export function BootOverlay({ demo }: { demo: DemoStatus | null }) {
  if (!demo || (demo.state !== 'warming' && demo.state !== 'registering')) return null

  const pct = demo.state === 'registering' ? 0 : Math.round(demo.warmup_progress * 100)
  const C = 2 * Math.PI * 92
  const shown = Math.min(LINES.length, 1 + Math.floor(pct / 20)) // steps follow the server's real progress, not a timer
  return (
    <div className="boot" role="status" aria-live="polite" aria-label={`Calibrating, ${pct} percent`}>
      <div className="boot-core">
        <svg viewBox="0 0 220 220" aria-hidden="true">
          <circle className="boot-track" cx="110" cy="110" r="92" />
          <circle className="boot-fill" cx="110" cy="110" r="92" strokeDasharray={`${(C * pct) / 100} ${C}`} transform="rotate(-90 110 110)" />
          <circle className="boot-orbit" cx="110" cy="110" r="104" />
          <circle className="boot-orbit two" cx="110" cy="110" r="76" />
        </svg>
        <div className="boot-pct"><b>{pct}</b><small>%</small></div>
      </div>
      <h2>CALIBRATING BASELINES</h2>
      <ul>{LINES.slice(0, shown).map((l, i) => <li key={l} className={i === shown - 1 ? 'now' : ''}>{l}</li>)}</ul>
      <p className="sub">No alert is trusted until a device has learned what normal looks like. This takes about a minute.</p>
    </div>
  )
}
