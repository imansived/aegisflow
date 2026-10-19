import { useCallback, useEffect, useMemo, useState } from 'react'
import { Api, ApiError } from './api'
import { AlertFeed } from './components/AlertFeed'
import { DemoPanel } from './components/DemoPanel'
import { DeviceDetail } from './components/DeviceDetail'
import { DeviceList } from './components/DeviceList'
import { Header, type Tab } from './components/Header'
import { Login } from './components/Login'
import { SiteMap } from './components/SiteMap'
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
  const [tab, setTab] = useState<Tab>('live')
  const [selected, setSelected] = useState<string | null>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [status, setStatus] = useState<(Status & { demo: DemoStatus }) | null>(null)

  const api = useMemo(() => (token ? new Api(token) : null), [token])
  const [live, dispatch] = useLive(token)

  const logout = useCallback(() => { write('aegis.token', null); write('aegis.role', null); setToken(null); setSelected(null); setSite(null); setStatus(null) }, [])
  const guard = useCallback((e: unknown) => { if (e instanceof ApiError && e.status === 401) logout() }, [logout])

  // Initial load: the zone geometry and recent alerts. Everything after that arrives over the WebSocket.
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

  // When a device is selected, backfill its score history from the server.
  useEffect(() => {
    if (!api || !selected) return
    api.history(selected, 240).then((points) => dispatch({ type: 'history_loaded', device_id: selected, points })).catch(guard)
  }, [api, selected, dispatch, guard])

  const toggleTheme = () => {
    const root = document.documentElement
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches
    root.dataset.theme = dark ? 'light' : 'dark'
  }

  if (!token || !api) {
    return <Login onLogin={(t, r) => { write('aegis.token', t); write('aegis.role', r); setRole(r); setToken(t) }} />
  }

  const snap = selected ? live.devices[selected] ?? null : null
  const deviceAlerts = selected ? live.alerts.filter((a) => a.device_id === selected) : []

  return (
    <div className="app">
      <Header tab={tab} onTab={setTab} live={live} status={status} role={role} onLogout={logout} onTheme={toggleTheme} />
      <main className="main">
        {tab === 'live' && (
          <div className="live-grid" style={{ display: 'grid', gap: 16 }}>
            <div className="col col-feed">
              <AlertFeed alerts={live.alerts} feed={live.feed} selected={selected} onSelect={setSelected} />
            </div>
            <div className="col">
              <section className="card">
                <header><h2>Site</h2><span className="sub">click a device to inspect it</span></header>
                <SiteMap site={site} devices={live.devices} selected={selected} onSelect={setSelected} trail={selected ? live.history[selected] ?? [] : []} />
              </section>
              <DeviceDetail snap={snap} points={selected ? live.history[selected] ?? [] : []} alerts={deviceAlerts} status={status} />
            </div>
            <div className="col">
              <DemoPanel api={api} isAdmin={role === 'admin'} demo={status?.demo ?? null} selected={selected} onChanged={refreshStatus} />
              <DeviceList devices={live.devices} selected={selected} onSelect={setSelected} />
            </div>
          </div>
        )}
        {tab === 'analytics' && <Analytics api={api} live={live} status={status} onUnauthorised={logout} />}
        {tab === 'evidence' && <Evidence />}
      </main>
    </div>
  )
}
