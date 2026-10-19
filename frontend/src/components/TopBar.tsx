import { siteDayTime } from '../format'
import type { LiveState } from '../live'
import type { Status } from '../types'

export type Mode = 'observe' | 'archive' | 'findings'

export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 40 40" aria-hidden="true">
      <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="var(--sig-spatial)" /><stop offset="1" stopColor="var(--sig-device)" /></linearGradient></defs>
      <path d="M20 3 35 9v10c0 9-6 15-15 18C11 34 5 28 5 19V9Z" fill="none" stroke="url(#lg)" strokeWidth="2" />
      <circle cx="20" cy="20" r="6.5" fill="none" stroke="url(#lg)" strokeWidth="1.5" />
      <ellipse cx="20" cy="20" rx="11" ry="4" fill="none" stroke="var(--sig-network)" strokeWidth="1" transform="rotate(-28 20 20)" />
      <circle cx="20" cy="20" r="2" fill="var(--sig-proximity)" />
    </svg>
  )
}

export function TopBar({ mode, onMode, live, status, role, onLogout, onPalette }: {
  mode: Mode; onMode: (m: Mode) => void; live: LiveState; status: Status | null; role: string; onLogout: () => void; onPalette: () => void
}) {
  const devices = Object.values(live.devices)
  const open = live.alerts.filter((a) => a.status !== 'closed').length
  const top = devices.reduce((m, d) => Math.max(m, d.score), 0)
  const latest = devices.reduce<string | null>((m, d) => (m === null || d.ts > m ? d.ts : m), null)
  const conn = live.connection
  const MODES: [Mode, string][] = [['observe', 'Observe'], ['archive', 'Archive'], ['findings', 'Findings']]

  return (
    <header className="top">
      <div className="brand"><Logo /><div><strong>AEGISFLOW</strong><span>OBSERVATORY</span></div></div>
      <nav className="modes" aria-label="Sections">
        {MODES.map(([m, label]) => <button key={m} aria-current={mode === m ? 'page' : undefined} onClick={() => onMode(m)}>{label}</button>)}
      </nav>
      <button className="palette-btn" onClick={onPalette} aria-label="Open command palette"><span>Search devices, jump anywhere</span><kbd>/</kbd></button>
      <div className="spacer" />
      <dl className="kpis" aria-label="Summary">
        <div><dt>SITE TIME</dt><dd>{latest ? siteDayTime(latest) : '——'}</dd></div>
        <div><dt>DEVICES</dt><dd>{devices.length}</dd></div>
        <div className={open ? 'alert' : ''}><dt>OPEN ALERTS</dt><dd>{open}</dd></div>
        <div><dt>PEAK</dt><dd>{devices.length ? top.toFixed(2) : '——'}</dd></div>
      </dl>
      <span className={`pill ${conn === 'open' ? 'ok' : conn === 'connecting' ? 'warn' : 'bad'}`} role="status"><i />{conn === 'open' ? 'LINK' : conn === 'connecting' ? 'CONNECTING' : 'RECONNECTING'}</span>
      {status && <span className="chip" title="fusion method and correlation window">{status.fusion} · {status.correlation.half_life_s}s</span>}
      <span className="chip role">{role}</span>
      <button className="ghost" onClick={onLogout}>Sign out</button>
    </header>
  )
}
