import { siteDayTime } from '../format'
import type { LiveState } from '../live'
import type { DemoStatus, Status } from '../types'

export function TopBar({ live, status, role, onLogout, onPalette }: {
  live: LiveState; status: (Status & { demo: DemoStatus }) | null; role: string; onLogout: () => void; onPalette: () => void
}) {
  const devices = Object.values(live.devices)
  const open = live.alerts.filter((a) => a.status !== 'closed').length
  const top = devices.reduce((m, d) => Math.max(m, d.score), 0)
  const latest = devices.reduce<string | null>((m, d) => (m === null || d.ts > m ? d.ts : m), null)
  const degraded = devices.filter((d) => d.observability && d.observability !== 'full').length
  const conn = live.connection
  const demo = status?.demo.state
  // A live fleet is the normal case and idle is just "no demo": the pill only appears when the fleet needs attention.
  const fleet = demo === 'registering' || demo === 'warming' ? ['warn', 'Calibrating'] : demo === 'error' ? ['bad', 'Fleet error'] : demo === 'stopping' ? ['warn', 'Stopping'] : null
  // The simulated fleet runs on its own clock, faster than real time; say so instead of calling it the site's time.
  const simulated = demo === 'live' || demo === 'warming' || demo === 'stopping'
  const speed = status?.demo.speed

  return (
    <header className="top">
      <div className="brand"><strong>AEGISFLOW</strong><span>Cross-modal anomaly detection</span></div>
      <button className="palette-btn" onClick={onPalette} aria-label="Open command palette"><span>Search devices, jump anywhere</span><kbd>/</kbd></button>
      <div className="spacer" />
      <dl className="kpis" aria-label="Summary">
        <div><dt>Connected devices</dt><dd>{devices.length}</dd></div>
        <div className={open ? 'alert' : ''}><dt>Open alerts</dt><dd>{open}</dd></div>
        {degraded > 0 && <div className="degraded"><dt>Degraded</dt><dd>{degraded}</dd></div>}
        <div><dt>Peak risk</dt><dd>{devices.length ? top.toFixed(2) : '—'}</dd></div>
        <div title={simulated ? `Simulated clock, running ${speed ?? ''}× real time. Telemetry timestamps, not your computer's clock.` : 'Time of the latest telemetry'}>
          <dt>{simulated ? 'Simulated time' : 'Site time'}</dt><dd>{latest ? siteDayTime(latest) : '—'}{simulated && speed ? <small> ×{speed}</small> : null}</dd>
        </div>
      </dl>
      {fleet && <span className={`pill ${fleet[0]}`}><i />{fleet[1]}</span>}
      <span className={`pill ${conn === 'open' ? 'ok' : conn === 'connecting' ? 'warn' : 'bad'}`} role="status"><i />{conn === 'open' ? 'LINK' : conn === 'connecting' ? 'CONNECTING' : 'RECONNECTING'}</span>
      <span className="chip role">{role}</span>
      <button className="ghost" onClick={onLogout}>Sign out</button>
    </header>
  )
}
