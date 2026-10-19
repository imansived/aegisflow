import { useMemo, useRef } from 'react'
import { siteTime } from '../format'
import type { Point } from '../live'
import type { Alert, Severity } from '../types'
import { useWidth } from './Charts'

const H = 92
const PAD = { l: 10, r: 10, t: 14, b: 18 }
const SEV_COLOR: Record<Severity, string> = { normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)' }

/** The fleet's pulse: the highest score across all devices per bucket of (simulated) time, over the last window. */
export function ribbonSeries(history: Record<string, Point[]>, bucketMs = 20_000, windowMs = 30 * 60_000): { t: number; v: number }[] {
  const pts = Object.values(history).flat().filter((p) => Number.isFinite(p.t))
  if (!pts.length) return []
  const newest = Math.max(...pts.map((p) => p.t))
  const byBucket = new Map<number, number>()
  for (const p of pts) {
    if (p.t < newest - windowMs) continue
    const b = Math.floor(p.t / bucketMs) * bucketMs
    byBucket.set(b, Math.max(byBucket.get(b) ?? 0, p.score))
  }
  return [...byBucket.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v }))
}

/** A timeline along the bottom of the stage. Alerts appear as flares on it; click one to focus its device. */
export function TimeRibbon({ history, alerts, onSelect }: { history: Record<string, Point[]>; alerts: Alert[]; onSelect: (device: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const W = useWidth(ref, 900, 300, 2400)
  const series = useMemo(() => ribbonSeries(history), [history])

  const geom = useMemo(() => {
    if (series.length < 2) return null
    const t0 = series[0].t, t1 = series[series.length - 1].t
    const x = (t: number) => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r)
    const y = (v: number) => PAD.t + (1 - Math.min(1, Math.max(0, v))) * (H - PAD.t - PAD.b)
    const line = series.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ')
    return { t0, t1, x, y, line, area: `${line} L${x(t1).toFixed(1)} ${H - PAD.b} L${x(t0).toFixed(1)} ${H - PAD.b} Z` }
  }, [series, W])

  const flares = geom ? alerts.filter((a) => { const t = Date.parse(a.started); return t >= geom.t0 && t <= geom.t1 }) : []

  return (
    <div className="ribbon" ref={ref} aria-label="Fleet pulse timeline">
      <span className="ribbon-title">FLEET PULSE <i>last 30 min · simulated clock</i></span>
      {!geom ? (
        <div className="ribbon-empty">awaiting signal…</div>
      ) : (
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Highest anomaly score in the fleet over time">
          <defs>
            <linearGradient id="ribbon-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--accent)" stopOpacity="0.45" /><stop offset="1" stopColor="var(--accent)" stopOpacity="0" /></linearGradient>
            <filter id="ribbon-glow" x="-10%" y="-40%" width="120%" height="180%"><feGaussianBlur stdDeviation="2.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {[0.25, 0.5, 0.75].map((g) => <line key={g} className="rg" x1={PAD.l} x2={W - PAD.r} y1={geom.y(g)} y2={geom.y(g)} />)}
          <path d={geom.area} fill="url(#ribbon-fill)" />
          <path className="ribbon-line" d={geom.line} filter="url(#ribbon-glow)" />
          {flares.map((a) => {
            const fx = geom.x(Date.parse(a.started))
            return (
              <g key={a.alert_id} className={`flare sev-${a.severity}`} style={{ ['--c' as string]: SEV_COLOR[a.severity] }} tabIndex={0} role="button"
                aria-label={`${a.device_id} alert, ${a.severity}, at ${siteTime(a.started)}`} onClick={() => onSelect(a.device_id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(a.device_id) } }}>
                <title>{a.device_id} · {a.severity} · {siteTime(a.started)}</title>
                <line x1={fx} x2={fx} y1={PAD.t} y2={H - PAD.b} />
                <circle cx={fx} cy={PAD.t} r={4.5} />
                <text x={fx} y={PAD.t - 5} textAnchor="middle">{a.device_id}</text>
              </g>
            )
          })}
          <text className="rt" x={PAD.l} y={H - 4}>{siteTime(new Date(geom.t0).toISOString())}</text>
          <text className="rt" x={W - PAD.r} y={H - 4} textAnchor="end">{siteTime(new Date(geom.t1).toISOString())}</text>
        </svg>
      )}
    </div>
  )
}
