import { useCallback, useEffect, useMemo, useState } from 'react'
import { Api, ApiError } from './api'
import { BootOverlay } from './components/BootOverlay'
import { CommandPalette, buildItems } from './components/CommandPalette'
import { HoloFloor } from './components/HoloFloor'
import { Inspector } from './components/Inspector'
import { Login } from './components/Login'
import { Sentinel } from './components/Sentinel'
import { SimDeck } from './components/SimDeck'
import { TimeRibbon } from './components/TimeRibbon'
import { TopBar, type Mode } from './components/TopBar'
import { useLive } from './live'
import Analytics from './pages/Analytics'
import Evidence from './pages/Evidence'
import type { DemoStatus, Site, Status } from './types'

// The token lives in sessionStorage: it survives a refresh but not a closed tab, and is never written to disk.
const read = (k: string): string | null => { try { return sessionStorage.getItem(k) } catch { return null } }
const write = (k: string, v: string | null) => { try { v === null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v) } catch { /* private mode */ } }

export default function App() {
  const [token, setToken] = useState<string | null>(() => read('aegis.token'))
  const [role, setRole] = useState<'admin' | 'viewer'>(() => (read('aegis.role') === 'admin' ? 'admin' : 'viewer'))
  const [mode, setMode] = useState<Mode>('observe')
  const [selected, setSelected] = useState<string | null>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [status, setStatus] = useState<(Status & { demo: DemoStatus }) | null>(null)
  const [deckOpen, setDeckOpen] = useState(true)
  const [palette, setPalette] = useState(false)

  const api = useMemo(() => (token ? new Api(token) : null), [token])
  const [live, dispatch] = useLive(token)

  const logout = useCallback(() => { write('aegis.token', null); write('aegis.role', null); setToken(null); setSelected(null); setSite(null); setStatus(null) }, [])
  const guard = useCallback((e: unknown) => { if (e instanceof ApiError && e.status === 401) logout() }, [logout])

  useEffect(() => {
    if (!api) return
    api.site().then(setSite).catch(guard)
    api.alerts({ limit: 100 }).then((alerts) => dispatch({ type: 'alerts_loaded', alerts })).catch(guard)
  }, [api, dispatch, guard])

  const refreshStatus = useCallback(() => { api?.status().then(setStatus).catch(guard) }, [api, guard])
  useEffect(() => {
    if (!api) return
    refreshStatus()
    const id = setInterval(refreshStatus, 2500)
    return () => clearInterval(id)
  }, [api, refreshStatus])

  useEffect(() => {
    if (!api || !selected) return
    api.history(selected, 240).then((points) => dispatch({ type: 'history_loaded', device_id: selected, points })).catch(guard)
  }, [api, selected, dispatch, guard])

  // The deck covers part of the scene, so it opens when there is something to start and tucks away once the fleet is live.
  const demoState = status?.demo.state
  useEffect(() => { if (demoState === 'live') setDeckOpen(false); else if (demoState === 'idle') setDeckOpen(true) }, [demoState])

  // "/" opens the command palette from anywhere that is not a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setPalette(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!token || !api) {
    return <Login onLogin={(t, r) => { write('aegis.token', t); write('aegis.role', r); setRole(r); setToken(t) }} />
  }

  const snap = selected ? live.devices[selected] ?? null : null
  const deviceAlerts = selected ? live.alerts.filter((a) => a.device_id === selected) : []
  const empty = Object.keys(live.devices).length === 0
  const demo = status?.demo ?? null
  const standby = empty && (!demo || demo.state === 'idle' || demo.state === 'error')
  const items = buildItems(live.devices, (id) => { setSelected(id); setMode('observe') }, setMode, () => { setMode('observe'); setDeckOpen(true) })

  return (
    <div className="obs">
      <TopBar mode={mode} onMode={setMode} live={live} status={status} role={role} onLogout={logout} onPalette={() => setPalette(true)} />
      <CommandPalette open={palette} onClose={() => setPalette(false)} items={items} />

      {mode === 'observe' && (
        <main className="scene">
          <Sentinel alerts={live.alerts} feed={live.feed} devices={live.devices} selected={selected} onSelect={setSelected} />
          <div className="stage">
            <i className="hud tl" aria-hidden="true" /><i className="hud tr" aria-hidden="true" /><i className="hud bl" aria-hidden="true" /><i className="hud br" aria-hidden="true" />
            <HoloFloor site={site} devices={live.devices} selected={selected} onSelect={setSelected} trail={selected ? live.history[selected] ?? [] : []} />
            {standby && (
              <div className="standby" role="status">
                <p><b>No fleet running.</b> {role === 'admin' ? 'Use the Simulation Deck to start one.' : 'Waiting for an operator to start the simulated fleet.'}</p>
              </div>
            )}
            <BootOverlay demo={demo} />
            <SimDeck api={api} isAdmin={role === 'admin'} demo={demo} selected={selected} targets={Object.keys(live.devices).sort()} onSelect={setSelected} onChanged={refreshStatus} open={deckOpen} onToggle={() => setDeckOpen((o) => !o)} />
            <TimeRibbon history={live.history} alerts={live.alerts} onSelect={setSelected} />
          </div>
          <Inspector snap={snap} points={selected ? live.history[selected] ?? [] : []} alerts={deviceAlerts} status={status} />
        </main>
      )}
      {mode === 'archive' && <main className="page-wrap"><Analytics api={api} live={live} status={status} onUnauthorised={logout} /></main>}
      {mode === 'findings' && <main className="page-wrap"><Evidence /></main>}
    </div>
  )
}
