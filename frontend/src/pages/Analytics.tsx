import { useEffect, useMemo, useState } from 'react'
import { ApiError, type Api } from '../api'
import { HBars, LineChart } from '../components/Charts'
import { SeverityBadge } from '../components/Glyph'
import { MODALITY_LABEL, SEVERITY_LABEL, formatUptime, sevRank } from '../format'
import type { LiveState } from '../live'
import { MODALITIES, type HistoryPoint, type Severity, type Status } from '../types'

const SEV_COLOR: Record<Severity, string> = { normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)' }

/** Highest fused score across the fleet per simulated minute, over the last hour of stored history. Older runs
 *  (each starts a week later in simulated time) are left out so the chart shows the current run only. */
export function fleetPeakSeries(points: HistoryPoint[], windowMs = 60 * 60_000): { t: number; v: number }[] {
  const times = points.map((p) => Date.parse(p.ts))
  const newest = Math.max(-Infinity, ...times)
  const byMinute = new Map<number, number>()
  points.forEach((p, i) => {
    if (times[i] < newest - windowMs) return
    const m = Math.floor(times[i] / 60_000) * 60_000
    byMinute.set(m, Math.max(byMinute.get(m) ?? 0, p.score))
  })
  return [...byMinute.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v }))
}

export default function Analytics({ api, live, status, onUnauthorised }: { api: Api; live: LiveState; status: Status | null; onUnauthorised: () => void }) {
  const [history, setHistory] = useState<HistoryPoint[]>([])
  const [table, setTable] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () => api.history(undefined, 2000).then((h) => alive && setHistory(h)).catch((e) => { if (e instanceof ApiError && e.status === 401) onUnauthorised() })
    load()
    const id = setInterval(load, 15_000)
    return () => { alive = false; clearInterval(id) }
  }, [api, onUnauthorised])

  const devices = useMemo(() => Object.values(live.devices).sort((a, b) => b.score - a.score), [live.devices])
  const bySeverity = useMemo(() => (['high', 'medium', 'low'] as Severity[]).map((s) => ({ s, n: live.alerts.filter((a) => a.severity === s).length })), [live.alerts])
  const byLead = useMemo(() => MODALITIES.map((m) => ({ m, n: live.alerts.filter((a) => a.factors[0]?.modality === m).length })), [live.alerts])
  const series = useMemo(() => fleetPeakSeries(history), [history])
  const open = live.alerts.filter((a) => a.status !== 'closed').length

  return (
    <div className="page">
      <div className="tiles">
        <div className="tile"><div className="label">Open alerts</div><div className="value">{open}</div><div className="hint">{live.alerts.length} total this session</div></div>
        <div className="tile"><div className="label">Events processed</div><div className="value">{status?.processor?.processed.toLocaleString() ?? '—'}</div><div className="hint">{status?.processor?.failed ?? 0} failed · {status?.dead_letters ?? 0} dead-lettered</div></div>
        <div className="tile"><div className="label">Latency p95</div><div className="value">{status?.processor?.p95_ms !== undefined ? `${status.processor.p95_ms.toFixed(0)} ms` : '—'}</div><div className="hint">publish to stored · p99 {status?.processor?.p99_ms?.toFixed(0) ?? '—'} ms</div></div>
        <div className="tile"><div className="label">Stream backlog</div><div className="value">{status?.stream.lag ?? '—'}</div><div className="hint">{status?.stream.pending ?? 0} in flight</div></div>
        <div className="tile"><div className="label">Server uptime</div><div className="value">{status ? formatUptime(status.uptime_s) : '—'}</div><div className="hint">fusion: {status?.fusion ?? '—'}</div></div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button className="iconbtn" aria-pressed={table} onClick={() => setTable(!table)}>{table ? 'Show charts' : 'Show data as tables'}</button>
      </div>

      <div className="grid-2">
        <section className="card">
          <header><h2>Risk score by device</h2><span className="sub">current, 15 highest</span></header>
          {table ? (
            <table className="data"><thead><tr><th>Device</th><th>Severity</th><th>Score</th></tr></thead>
              <tbody>{devices.slice(0, 15).map((d) => <tr key={d.device_id}><td>{d.device_id}</td><td>{SEVERITY_LABEL[d.severity]}</td><td>{d.score.toFixed(3)}</td></tr>)}</tbody></table>
          ) : (
            <HBars ariaLabel="Current risk score by device" max={1} rows={devices.slice(0, 15).map((d) => ({ label: d.device_id, value: d.score, glyph: <SeverityBadge severity={d.severity} label={false} />, color: 'var(--accent)' }))} />
          )}
        </section>

        <section className="card">
          <header><h2>Fleet peak score over time</h2><span className="sub">highest score in the fleet, per minute</span></header>
          {table ? (
            <table className="data"><thead><tr><th>Minute (UTC)</th><th>Peak score</th></tr></thead>
              <tbody>{series.slice(-15).reverse().map((p) => <tr key={p.t}><td>{new Date(p.t).toISOString().slice(11, 16)}</td><td>{p.v.toFixed(3)}</td></tr>)}</tbody></table>
          ) : (
            <LineChart points={series} ariaLabel="Highest anomaly score in the fleet per minute" valueLabel="Peak" reference={status?.alert_thresholds.raise_at} referenceLabel={status ? `alert ${status.alert_thresholds.raise_at.toFixed(2)}` : undefined} />
          )}
        </section>

        <section className="card">
          <header><h2>Alerts by severity</h2><span className="sub">this session</span></header>
          {table ? (
            <table className="data"><thead><tr><th>Severity</th><th>Alerts</th></tr></thead><tbody>{bySeverity.map((r) => <tr key={r.s}><td>{SEVERITY_LABEL[r.s]}</td><td>{r.n}</td></tr>)}</tbody></table>
          ) : (
            <HBars ariaLabel="Alerts by severity" rows={bySeverity.sort((a, b) => sevRank(b.s) - sevRank(a.s)).map((r) => ({ label: SEVERITY_LABEL[r.s], value: r.n, display: String(r.n), color: SEV_COLOR[r.s], glyph: <SeverityBadge severity={r.s} label={false} /> }))} />
          )}
        </section>

        <section className="card">
          <header><h2>Alerts by leading signal</h2><span className="sub">which signal contributed most</span></header>
          {table ? (
            <table className="data"><thead><tr><th>Signal</th><th>Alerts</th></tr></thead><tbody>{byLead.map((r) => <tr key={r.m}><td>{MODALITY_LABEL[r.m]}</td><td>{r.n}</td></tr>)}</tbody></table>
          ) : (
            <HBars ariaLabel="Alerts by leading signal" rows={byLead.map((r) => ({ label: MODALITY_LABEL[r.m], value: r.n, display: String(r.n), markClass: `m-${r.m}`, color: `var(--s-${r.m})` }))} />
          )}
        </section>
      </div>
    </div>
  )
}
