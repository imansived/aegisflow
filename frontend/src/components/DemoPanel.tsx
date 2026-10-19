import { useState } from 'react'
import { ApiError, type Api } from '../api'
import type { DemoStatus } from '../types'

const SCENARIOS: { id: string; label: string; hint: string }[] = [
  { id: 'spatial', label: 'Enter restricted zone', hint: 'A device walks into the server room.' },
  { id: 'network', label: 'Unknown connections', hint: 'Traffic to endpoints it never uses, with an upload spike.' },
  { id: 'hardware', label: 'Battery drain / CPU', hint: 'Abnormal drain and CPU load.' },
  { id: 'proximity', label: 'Unknown device nearby', hint: 'An unenrolled device stays close.' },
  { id: 'combined', label: 'Combined', hint: 'All four signals at once.' },
  { id: 'weak_correlated', label: 'Weak, simultaneous', hint: 'Several mild deviations together.' },
  { id: 'staged', label: 'Weak, staggered', hint: 'Mild deviations one after another.' },
]

export function DemoPanel({ api, isAdmin, demo, selected, onChanged }: {
  api: Api; isAdmin: boolean; demo: DemoStatus | null; selected: string | null; onChanged: () => void
}) {
  const [devices, setDevices] = useState(10)
  const [speed, setSpeed] = useState(20)
  const [magnitude, setMagnitude] = useState(0.8)
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
    <section className="card" aria-label="Demo controls">
      <header><h2>Demo</h2><span className="chip">{state === 'warming' ? 'Warming up' : state === 'live' ? 'Live' : state === 'error' ? 'Error' : state === 'idle' ? 'Stopped' : state}</span></header>

      {starting && (
        <>
          <p className="notice">Building each device’s baseline: fast-forwarding 34 simulated hours so alerts mean something.</p>
          <div className="progress" role="progressbar" aria-valuenow={Math.round((demo?.warmup_progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100} style={{ marginTop: 8 }}>
            <i style={{ width: `${Math.round((demo?.warmup_progress ?? 0) * 100)}%` }} />
          </div>
        </>
      )}
      {state === 'error' && <p className="error">{demo?.error}</p>}

      {isAdmin ? (
        <>
          {!live && !starting && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}>
              <label className="field">Devices<input type="number" min={2} max={50} value={devices} onChange={(e) => setDevices(Number(e.target.value))} style={{ width: 80 }} /></label>
              <label className="field">Speed<select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((s) => <option key={s} value={s}>{s}× real time</option>)}</select></label>
              <button className="btn" disabled={busy} onClick={() => run(() => api.demoStart({ devices, warmup_hours: 34, speed }))}>Start fleet</button>
            </div>
          )}
          {(live || starting) && <div className="btns"><button className="btn secondary" disabled={busy} onClick={() => run(() => api.demoStop())}>Stop</button></div>}

          {live && (
            <>
              <h3>Inject on {selected ?? '…'}</h3>
              {!selected && <p className="notice">Select a device first.</p>}
              <label className="field" style={{ marginBottom: 8 }}>Strength {magnitude.toFixed(2)}<input type="range" min={0.05} max={1} step={0.05} value={magnitude} onChange={(e) => setMagnitude(Number(e.target.value))} /></label>
              <div className="btns">
                {SCENARIOS.map((s) => (
                  <button key={s.id} className="btn secondary" title={s.hint} disabled={busy || !selected}
                    onClick={() => run(() => api.demoInject({ device_id: selected!, scenario: s.id, magnitude, duration_s: 240 }))}>{s.label}</button>
                ))}
                <button className="btn danger" disabled={busy || !selected} onClick={() => run(() => api.demoClear(selected!))}>Restore normal</button>
              </div>
              {demo && demo.active_episodes.length > 0 && (
                <p className="notice" style={{ marginTop: 8 }}>Active: {demo.active_episodes.map((e) => `${e.device_id} ${e.scenario}`).join(', ')}</p>
              )}
            </>
          )}
        </>
      ) : (
        <p className="notice">Viewers can watch but not control the demo. Sign in with the admin token to start the fleet and inject scenarios.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
