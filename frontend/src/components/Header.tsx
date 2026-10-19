import { siteDayTime } from '../format'
import type { LiveState } from '../live'
import type { Status } from '../types'

export type Tab = 'live' | 'analytics' | 'evidence'

export function Header({ tab, onTab, live, status, role, onLogout, onTheme }: {
  tab: Tab; onTab: (t: Tab) => void; live: LiveState; status: Status | null; role: string; onLogout: () => void; onTheme: () => void
}) {
  const devices = Object.values(live.devices)
  const open = live.alerts.filter((a) => a.status !== 'closed').length
  const top = devices.reduce((m, d) => Math.max(m, d.score), 0)
  const latest = devices.reduce<string | null>((m, d) => (m === null || d.ts > m ? d.ts : m), null)
  const conn = live.connection

  return (
    <header className="topbar">
      <div className="brand"><strong>AegisFlow</strong><span>Connected-device behaviour monitor</span></div>
      <nav className="tabs" aria-label="Sections">
        {(['live', 'analytics', 'evidence'] as Tab[]).map((t) => (
          <button key={t} className="tab" aria-current={tab === t ? 'page' : undefined} onClick={() => onTab(t)}>
            {t === 'live' ? 'Live' : t === 'analytics' ? 'Analytics' : 'Evidence'}
          </button>
        ))}
      </nav>
      <div className="spacer" />
      <div className="kpis" aria-label="Summary">
        <div className="kpi"><span className="label">Site time</span><span className="value">{latest ? siteDayTime(latest) : '—'}</span></div>
        <div className="kpi"><span className="label">Devices</span><span className="value">{devices.length}</span></div>
        <div className="kpi"><span className="label">Open alerts</span><span className="value">{open}</span></div>
        <div className="kpi"><span className="label">Top score</span><span className="value">{devices.length ? top.toFixed(2) : '—'}</span></div>
      </div>
      <span className={`pill ${conn === 'open' ? 'ok' : conn === 'connecting' ? 'warn' : 'bad'}`} role="status">
        <span className="dot" />{conn === 'open' ? 'Live' : conn === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
      </span>
      {status && <span className="chip" title="fusion method and correlation window">{status.fusion} · {status.correlation.half_life_s}s</span>}
      <span className="chip">{role}</span>
      <button className="iconbtn" onClick={onTheme} aria-label="Toggle light or dark theme">Theme</button>
      <button className="iconbtn" onClick={onLogout}>Sign out</button>
    </header>
  )
}
