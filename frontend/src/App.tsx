import { useCallback, useEffect, useMemo, useState } from 'react'
import { Api, ApiError } from './api'
import { BootOverlay } from './components/BootOverlay'
import { CommandPalette, buildItems } from './components/CommandPalette'
import { SeverityBadge } from './components/Glyph'
import { HoloFloor } from './components/HoloFloor'
import { Login } from './components/Login'
import { AnomalyFeed, CorrelationPanel, HealthPanel, TrafficPanel, focusDevice } from './components/Panels'
import { SideNav, type View } from './components/SideNav'
import { TopBar } from './components/TopBar'
import { useLive } from './live'
import Analytics from './pages/Analytics'
import Anomalies from './pages/Anomalies'
import Evidence from './pages/Evidence'
import Network from './pages/Network'
import Scenarios from './pages/Scenarios'
import type { DemoStatus, Site, Status } from './types'

// The token lives in sessionStorage: it survives a refresh but not a closed tab, and is never written to disk.
const read = (k: string): string | null => { try { return sessionStorage.getItem(k) } catch { return null } }
const write = (k: string, v: string | null) => { try { v === null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v) } catch { /* private mode */ } }

export default function App() {
  const [token, setToken] = useState<string | null>(() => read('aegis.token'))
  const [role, setRole] = useState<'admin' | 'viewer'>(() => (read('aegis.role') === 'admin' ? 'admin' : 'viewer'))
  const [view, setView] = useState<View>('overview')
  const [selected, setSelected] = useState<string | null>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [status, setStatus] = useState<(Status & { demo: DemoStatus }) | null>(null)
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

  // The device the bottom panels describe: the selected one, or else the riskiest.
  const focus = focusDevice(live.devices, selected)
  const focusId = focus?.device_id ?? null
  useEffect(() => {
    if (!api || !focusId) return
    api.history(focusId, 240).then((points) => dispatch({ type: 'history_loaded', device_id: focusId, points })).catch(guard)
  }, [api, focusId, dispatch, guard])

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

  const demo = status?.demo ?? null
  const empty = Object.keys(live.devices).length === 0
  const standby = empty && (!demo || demo.state === 'idle' || demo.state === 'error')
  const openCount = live.alerts.filter((a) => a.status !== 'closed').length
  const watch = (id: string) => { setSelected(id); setView('overview') }
  const items = buildItems(live.devices, watch, setView)

  return (
    <div className="shell">
      <TopBar live={live} status={status} role={role} onLogout={logout} onPalette={() => setPalette(true)} />
      <SideNav view={view} onView={setView} openAlerts={openCount} />
      <CommandPalette open={palette} onClose={() => setPalette(false)} items={items} />

      {view === 'overview' && (
        <main className="overview">
          <section className="stage" aria-label="Facility map">
            <HoloFloor site={site} devices={live.devices} selected={selected} onSelect={setSelected} trail={selected ? live.history[selected] ?? [] : []} />
            {focus && (
              <div className="focus-chip" aria-live="polite">
                <b>{focus.device_id}</b><span>{focus.zone ?? 'in transit'}</span><SeverityBadge severity={focus.severity} /><b className="score">{focus.score.toFixed(2)}</b>
                {!selected && <span className="sub">riskiest device</span>}
              </div>
            )}
            {standby && (
              <div className="standby" role="status">
                <p><b>No fleet running.</b> {role === 'admin' ? <button className="link" onClick={() => setView('scenarios')}>Open Scenarios to start one →</button> : 'Waiting for an operator to start the simulated fleet.'}</p>
              </div>
            )}
            <BootOverlay demo={demo} />
          </section>
          <AnomalyFeed alerts={live.alerts} feed={live.feed} selected={selected} onSelect={setSelected} onOpenAll={() => setView('anomalies')} />
          <div className="bottom">
            <TrafficPanel snap={focus} history={live.history} />
            <HealthPanel snap={focus} history={live.history} />
            <CorrelationPanel snap={focus} history={live.history} alerts={live.alerts} status={status} />
          </div>
        </main>
      )}
      {view === 'network' && <main className="page-wrap"><Network devices={live.devices} selected={selected} onOpen={watch} /></main>}
      {view === 'anomalies' && <main className="page-wrap"><Anomalies alerts={live.alerts} onOpen={watch} /></main>}
      {view === 'scenarios' && (
        <main className="page-wrap">
          <Scenarios api={api} isAdmin={role === 'admin'} demo={demo} selected={selected} targets={Object.keys(live.devices).sort()} onSelect={setSelected} onChanged={refreshStatus} onWatch={watch} />
        </main>
      )}
      {view === 'stats' && <main className="page-wrap"><Analytics api={api} live={live} status={status} onUnauthorised={logout} /></main>}
      {view === 'evidence' && <main className="page-wrap"><Evidence /></main>}
    </div>
  )
}
