import { useRef, useState } from 'react'
import { siteTime } from '../format'
import { useWidth } from './Charts'

/** SVG path for a series, drawn as separate strokes wherever a value is missing (NaN), never bridged across a gap. */
export function linePath(xs: number[], ys: number[]): string {
  let d = '', pen = false
  xs.forEach((x, i) => {
    if (!Number.isFinite(ys[i])) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)} ${ys[i].toFixed(1)}`
    pen = true
  })
  return d
}

const clock = (ms: number) => siteTime(new Date(ms).toISOString())
const nearest = (times: number[], x: number, xs: (t: number) => number): number => {
  let best = 0, d = Infinity
  times.forEach((t, i) => { const dd = Math.abs(xs(t) - x); if (dd < d) { d = dd; best = i } })
  return best
}

export interface Series { key: string; label: string; color: string; values: number[] }

/** Several series on ONE shared axis (never dual-axis). Legend above, values at the line ends, crosshair tooltip. */
export function MultiLine({ times, series, height = 120, ariaLabel, unit = '', min = 0, max }: {
  times: number[]; series: Series[]; height?: number; ariaLabel: string; unit?: string; min?: number; max?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const W = useWidth(ref, 360, 200, 900)
  const [hover, setHover] = useState<number | null>(null)
  const PAD = { l: 6, r: 58, t: 8, b: 18 }
  const known = series.flatMap((s) => s.values).filter(Number.isFinite)
  if (times.length < 2 || known.length === 0) {
    return <div className="mini-empty" ref={ref}>Waiting for live data…</div>
  }
  const hi = max ?? Math.max(1, ...known) * 1.1
  const t0 = times[0], t1 = times[times.length - 1] || t0 + 1
  const x = (t: number) => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - min) / Math.max(1e-9, hi - min)) * (height - PAD.t - PAD.b)
  const xsAll = times.map(x)
  const h = hover === null ? null : hover
  return (
    <div className="mini" ref={ref}>
      <ul className="mini-legend">{series.map((s) => <li key={s.key}><i style={{ background: s.color }} />{s.label}</li>)}</ul>
      <div className="chart">
        <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel}
          onMouseMove={(e) => setHover(nearest(times, e.nativeEvent.offsetX, x))} onMouseLeave={() => setHover(null)}>
          {[0, 0.5, 1].map((g) => <line key={g} className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(min + g * (hi - min))} y2={y(min + g * (hi - min))} />)}
          {series.map((s) => (
            <g key={s.key}>
              <path d={linePath(xsAll, s.values.map(y))} fill="none" stroke={s.color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
              {(() => {
                const last = [...s.values].reverse().findIndex(Number.isFinite)
                if (last < 0) return null
                const i = s.values.length - 1 - last
                return <text x={W - PAD.r + 6} y={y(s.values[i]) + 4} className="end-label" fill={s.color}>{s.values[i] >= 100 ? Math.round(s.values[i]) : s.values[i].toFixed(1)}{unit}</text>
              })()}
            </g>
          ))}
          <text className="axis-text" x={PAD.l} y={height - 4}>{clock(t0)}</text>
          <text className="axis-text" x={W - PAD.r} y={height - 4} textAnchor="end">{clock(t1)}</text>
          {h !== null && <line className="cross" x1={xsAll[h]} x2={xsAll[h]} y1={PAD.t} y2={height - PAD.b} />}
        </svg>
        {h !== null && (
          <div className="tooltip" style={{ left: Math.min(W - 70, Math.max(70, xsAll[h])), top: 0 }}>
            {clock(times[h])} · {series.map((s) => `${s.label} ${Number.isFinite(s.values[h]) ? s.values[h].toFixed(1) + unit : '—'}`).join(' · ')}
          </div>
        )}
      </div>
    </div>
  )
}

export interface Lane { key: string; label: string; color: string; values: number[]; now: number; extra?: string }

/** The correlation timeline: one thin lane per signal (each on its own 0..1 scale), a faint line at the level where a
 *  signal counts as active, and the fused score underneath with the alert level. When several lanes lift together and
 *  the fused lane jumps above every one of them, that is the engine doing its job, visible at a glance. */
export function CorrelationLanes({ times, lanes, fused, fusedNow, activeLevel, alertLevel, height = 190 }: {
  times: number[]; lanes: Lane[]; fused: number[]; fusedNow: number; activeLevel: number; alertLevel?: number; height?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const W = useWidth(ref, 520, 280, 1200)
  const [hover, setHover] = useState<number | null>(null)
  if (times.length < 2) return <div className="mini-empty" ref={ref}>Waiting for live data…</div>
  const L = 140, R = 8, laneH = (height - 40 - 22) / lanes.length, fusedH = 40
  const t0 = times[0], t1 = times[times.length - 1]
  const x = (t: number) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R)
  const xsAll = times.map(x)
  const laneTop = (i: number) => 4 + i * laneH
  const lane = (_i: number, v: number, h: number, top: number) => top + h - 2 - Math.min(1, Math.max(0, v)) * (h - 6)
  const fusedTop = 4 + lanes.length * laneH + 6
  return (
    <div className="chart corr" ref={ref}>
      <svg width={W} height={height} viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Correlation timeline: each signal and the fused score over time"
        onMouseMove={(e) => setHover(nearest(times, e.nativeEvent.offsetX, x))} onMouseLeave={() => setHover(null)}>
        {lanes.map((l, i) => {
          const top = laneTop(i)
          const ys = l.values.map((v) => lane(i, v, laneH, top))
          const d = linePath(xsAll, ys)
          return (
            <g key={l.key}>
              <rect className="lane-bg" x={L} y={top} width={W - L - R} height={laneH - 3} rx="4" />
              <line className="active-line" x1={L} x2={W - R} y1={lane(i, activeLevel, laneH, top)} y2={lane(i, activeLevel, laneH, top)} />
              {d && <path d={`${d}L${xsAll[xsAll.length - 1].toFixed(1)} ${top + laneH - 2}L${xsAll[0].toFixed(1)} ${top + laneH - 2}Z`} fill={l.color} opacity="0.13" stroke="none" />}
              <path d={d} fill="none" stroke={l.color} strokeWidth="1.6" strokeLinejoin="round" />
              <rect x={0} y={top + laneH / 2 - 8} width="9" height="9" rx="2.5" fill={l.color} />
              <text className="lane-label" x={16} y={top + laneH / 2 + 1}>{l.label}</text>
              <text className="lane-now" x={L - 8} y={top + laneH / 2 + 1} textAnchor="end" fill={l.now >= activeLevel ? l.color : undefined}>{l.now.toFixed(2)}</text>
            </g>
          )
        })}
        <rect className="lane-bg fused" x={L} y={fusedTop} width={W - L - R} height={fusedH} rx="4" />
        {alertLevel !== undefined && <line className="alert-line" x1={L} x2={W - R} y1={lane(0, alertLevel, fusedH, fusedTop)} y2={lane(0, alertLevel, fusedH, fusedTop)} />}
        <path d={linePath(xsAll, fused.map((v) => lane(0, v, fusedH, fusedTop)))} fill="none" stroke="var(--ink)" strokeWidth="2" strokeLinejoin="round" />
        <text className="lane-label strong" x={0} y={fusedTop + fusedH / 2 + 1}>Fused risk</text>
        <text className="lane-now strong" x={L - 8} y={fusedTop + fusedH / 2 + 1} textAnchor="end">{fusedNow.toFixed(2)}</text>
        <text className="axis-text" x={L} y={height - 4}>{clock(t0)}</text>
        <text className="axis-text" x={W - R} y={height - 4} textAnchor="end">{clock(t1)}</text>
        {alertLevel !== undefined && <text className="ref-text" x={W - R} y={lane(0, alertLevel, fusedH, fusedTop) - 3} textAnchor="end">alert level {alertLevel.toFixed(2)}</text>}
        {hover !== null && <line className="cross" x1={xsAll[hover]} x2={xsAll[hover]} y1={4} y2={height - 20} />}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: Math.min(W - 120, Math.max(130, xsAll[hover])), top: 0 }}>
          {clock(times[hover])} · {lanes.map((l) => `${l.label} ${l.values[hover]?.toFixed(2) ?? '—'}`).join(' · ')} · fused {fused[hover]?.toFixed(2)}
        </div>
      )}
    </div>
  )
}
