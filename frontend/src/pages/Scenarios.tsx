import { useState } from 'react'
import { ApiError, type Api } from '../api'
import { MODALITY_LABEL } from '../format'
import { MODALITIES, type DemoStatus, type Modality } from '../types'

const ALL: Modality[] = [...MODALITIES]
const MODULES: { id: string; label: string; hint: string; signals: Modality[] }[] = [
  { id: 'spatial', label: 'Breach: enter restricted zone', hint: 'A device walks into the server room.', signals: ['spatial'] },
  { id: 'network', label: 'Exfiltrate: unknown connections', hint: 'Traffic to endpoints it never uses, with an upload spike.', signals: ['network'] },
  { id: 'hardware', label: 'Drain: battery and CPU', hint: 'Abnormal battery drain and CPU load.', signals: ['device'] },
  { id: 'proximity', label: 'Intruder: unknown device nearby', hint: 'An unenrolled device stays close.', signals: ['proximity'] },
  { id: 'combined', label: 'Combined strike', hint: 'All four signals at once.', signals: ALL },
  { id: 'weak_correlated', label: 'Whisper: weak, simultaneous', hint: 'Mild deviations in several signals together. Each alone is below its own alert level.', signals: ALL },
  { id: 'staged', label: 'Slow burn: weak, staggered', hint: 'Mild deviations one after another, so they overlap only partly.', signals: ALL },
]

/** Scenarios: start a simulated fleet and inject threats into it. Admin only (the server enforces it).
 *  Scenario buttons always work: they act on the chosen device, or on the first device if none is chosen yet. */
export default function Scenarios({ api, isAdmin, demo, selected, targets, onSelect, onChanged, onWatch }: {
  api: Api; isAdmin: boolean; demo: DemoStatus | null; selected: string | null; targets: string[]; onSelect: (id: string) => void
  onChanged: () => void; onWatch: (id: string) => void
}) {
  const [devices, setDevices] = useState(10)
  const [speed, setSpeed] = useState(20)
  const [strength, setStrength] = useState(0.8)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<{ text: string; device: string } | null>(null)

  const target = selected ?? targets[0] ?? null
  const run = async (fn: () => Promise<unknown>, ok?: { text: string; device: string }) => {
    setBusy(true); setError(null); setDone(null)
    try { await fn(); setDone(ok ?? null); onChanged() } catch (e) { setError(e instanceof ApiError ? e.message : 'Request failed') } finally { setBusy(false) }
  }
  const inject = (m: (typeof MODULES)[number]) => {
    if (!target) return
    if (!selected) onSelect(target)
    return run(() => api.demoInject({ device_id: target, scenario: m.id, magnitude: strength, duration_s: 240 }), { text: `${m.label.split(':')[0]} started on ${target}.`, device: target })
  }
  const state = demo?.state ?? 'idle'
  const live = state === 'live'
  const starting = state === 'registering' || state === 'warming'

  return (
    <div className="page">
      <section className="card" aria-label="Simulated fleet">
        <header><h2>Simulated fleet</h2><span className={`chip ${live ? 'live' : ''}`}>{live ? 'Live' : starting ? 'Calibrating' : state === 'error' ? 'Error' : 'Offline'}</span></header>
        <p className="note">The fleet is simulated: ordinary devices moving around a 130 m facility. Starting it replays 34 simulated hours so every device learns its own normal before anything is judged.</p>
        {state === 'error' && <p className="error">{demo?.error}</p>}
        {!isAdmin && <p className="note">Viewers can watch but not control the simulation. Sign in with the admin token to start the fleet and inject threats.</p>}
        {isAdmin && !live && !starting && (
          <div className="row-form">
            <label className="field">Devices<input type="number" min={2} max={50} value={devices} onChange={(e) => setDevices(Number(e.target.value))} /></label>
            <label className="field">Speed<select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((s) => <option key={s} value={s}>{s}× real time</option>)}</select></label>
            <button className="btn primary" disabled={busy} onClick={() => run(() => api.demoStart({ devices, warmup_hours: 34, speed }))}>Start fleet</button>
          </div>
        )}
        {isAdmin && (live || starting) && <div className="btns"><button className="btn" disabled={busy} onClick={() => run(() => api.demoStop())}>Stop</button></div>}
        {starting && <p className="note">Calibrating: {Math.round((demo?.warmup_progress ?? 0) * 100)}%. The Overview map shows the progress.</p>}
      </section>

      {isAdmin && live && (
        <section className="card" aria-label="Inject a threat">
          <header><h2>Inject a threat</h2><span className="sub">acts on the target device for 4 simulated minutes</span></header>
          <div className="row-form">
            <label className="field">Target device
              <select value={target ?? ''} onChange={(e) => e.target.value && onSelect(e.target.value)}>
                {targets.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
            <label className="field range">Strength {strength.toFixed(2)}<input type="range" min={0.05} max={1} step={0.05} value={strength} onChange={(e) => setStrength(Number(e.target.value))} /></label>
            <button className="btn danger" disabled={busy || !target} onClick={() => run(() => api.demoClear(target!), { text: `Restored ${target} to normal.`, device: target! })}>Restore normal</button>
          </div>
          <div className="scn-grid">
            {MODULES.map((m) => (
              <button key={m.id} className="scn" disabled={busy || !target} onClick={() => inject(m)}>
                <b>{m.label}</b>
                <span>{m.hint}</span>
                <span className="scn-sig">{m.signals.length === ALL.length ? <em>All four signals</em> : m.signals.map((s) => <span key={s} className={`sigchip m-${s}`}><i />{MODALITY_LABEL[s]}</span>)}</span>
              </button>
            ))}
          </div>
          {done && (
            <p className="note ok" role="status">{done.text} <button className="link" onClick={() => onWatch(done.device)}>Watch it on the map →</button></p>
          )}
          {demo && demo.active_episodes.length > 0 && <p className="note">Active: {demo.active_episodes.map((e) => `${e.device_id} ${e.scenario}`).join(', ')}</p>}
          {error && <p className="error" role="alert">{error}</p>}
        </section>
      )}
      {!live && error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
