import { useState } from 'react'
import { ApiError, type Api } from '../api'
import type { DemoStatus } from '../types'

const MODULES: { id: string; label: string; hint: string }[] = [
  { id: 'spatial', label: 'Breach: enter restricted zone', hint: 'A device walks into the server room.' },
  { id: 'network', label: 'Exfiltrate: unknown connections', hint: 'Traffic to endpoints it never uses, with an upload spike.' },
  { id: 'hardware', label: 'Drain: battery and CPU', hint: 'Abnormal battery drain and CPU load.' },
  { id: 'proximity', label: 'Intruder: unknown device nearby', hint: 'An unenrolled device stays close.' },
  { id: 'combined', label: 'Combined strike', hint: 'All four signals at once.' },
  { id: 'weak_correlated', label: 'Whisper: weak, simultaneous', hint: 'Several mild deviations together.' },
  { id: 'staged', label: 'Slow burn: weak, staggered', hint: 'Mild deviations one after another.' },
]

/** The Simulation Deck: start a simulated fleet and inject threats into it. Admin only (the server enforces it). */
export function SimDeck({ api, isAdmin, demo, selected, onChanged, open, onToggle }: {
  api: Api; isAdmin: boolean; demo: DemoStatus | null; selected: string | null; onChanged: () => void; open: boolean; onToggle: () => void
}) {
  const [devices, setDevices] = useState(10)
  const [speed, setSpeed] = useState(20)
  const [strength, setStrength] = useState(0.8)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); onChanged() } catch (e) { setError(e instanceof ApiError ? e.message : 'Request failed') } finally { setBusy(false) }
  }
  const state = demo?.state ?? 'idle'
  const live = state === 'live'
  const starting = state === 'registering' || state === 'warming'

  return (
    <aside className={`deck ${open ? 'open' : ''}`} aria-label="Simulation deck">
      <button className="deck-toggle" aria-expanded={open} onClick={onToggle}>
        <span className="pip" aria-hidden="true" />SIMULATION DECK<span className={`chip ${live ? 'live' : ''}`}>{live ? 'LIVE' : starting ? 'CALIBRATING' : state === 'error' ? 'ERROR' : 'OFFLINE'}</span>
      </button>

      {open && (
        <div className="deck-body">
          {state === 'error' && <p className="error">{demo?.error}</p>}
          {!isAdmin && <p className="note">Viewers can watch but not control the simulation. Sign in with the admin token to start the fleet and inject threats.</p>}

          {isAdmin && !live && !starting && (
            <div className="deck-start">
              <label className="field">Devices<input type="number" min={2} max={50} value={devices} onChange={(e) => setDevices(Number(e.target.value))} /></label>
              <label className="field">Speed<select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((s) => <option key={s} value={s}>{s}× real time</option>)}</select></label>
              <button className="btn primary" disabled={busy} onClick={() => run(() => api.demoStart({ devices, warmup_hours: 34, speed }))}>Start fleet</button>
            </div>
          )}
          {isAdmin && (live || starting) && <div className="btns"><button className="btn" disabled={busy} onClick={() => run(() => api.demoStop())}>Stop</button></div>}

          {isAdmin && live && (
            <>
              <h3>Inject on <b>{selected ?? '…'}</b></h3>
              {!selected && <p className="note">Select a device first.</p>}
              <label className="field range">Strength {strength.toFixed(2)}<input type="range" min={0.05} max={1} step={0.05} value={strength} onChange={(e) => setStrength(Number(e.target.value))} /></label>
              <div className="modules">
                {MODULES.map((m) => (
                  <button key={m.id} className="btn module" title={m.hint} disabled={busy || !selected}
                    onClick={() => run(() => api.demoInject({ device_id: selected!, scenario: m.id, magnitude: strength, duration_s: 240 }))}>{m.label}</button>
                ))}
                <button className="btn danger" disabled={busy || !selected} onClick={() => run(() => api.demoClear(selected!))}>Restore normal</button>
              </div>
              {demo && demo.active_episodes.length > 0 && <p className="note">Active: {demo.active_episodes.map((e) => `${e.device_id} ${e.scenario}`).join(', ')}</p>}
            </>
          )}
          {error && <p className="error" role="alert">{error}</p>}
        </div>
      )}
    </aside>
  )
}
