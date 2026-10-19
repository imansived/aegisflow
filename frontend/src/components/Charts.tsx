import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { siteTime } from '../format'

/** The element's rendered width, kept up to date. Charts draw at this width so their text stays one size
 *  whether the card is narrow or wide, instead of scaling a fixed drawing. Falls back where there is no layout (tests). */
function useWidth(ref: RefObject<HTMLElement | null>, fallback = 560, min = 240, max = 900): number {
  const [w, setW] = useState(fallback)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => {
      const cw = el.getBoundingClientRect().width
      if (cw > 50) setW(Math.round(Math.min(max, Math.max(min, cw))))
    }
    update()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, min, max])
  return w
}

// ------------------------------------------------------------------------------------- line chart

export interface LinePoint { t: number; v: number }

const H = 150, PAD = { l: 28, r: 62, t: 10, b: 20 }

/** One series: a 2 px line over a faint wash, an end dot with a surface ring, a labelled reference line,
 *  hairline grid, and a crosshair + tooltip on hover. A single series needs no legend; the title names it. */
export function LineChart({ points, reference, referenceLabel, ariaLabel, valueLabel = 'Score' }: {
  points: LinePoint[]; reference?: number; referenceLabel?: string; ariaLabel: string; valueLabel?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const W = useWidth(ref)

  const geom = useMemo(() => {
    if (points.length < 2) return null
    const t0 = points[0].t, t1 = points[points.length - 1].t
    const x = (t: number) => PAD.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r)
    const y = (v: number) => PAD.t + (1 - Math.min(1, Math.max(0, v))) * (H - PAD.t - PAD.b)
    const xs = points.map((p) => x(p.t))
    const d = points.map((p, i) => `${i ? 'L' : 'M'}${xs[i].toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ')
    return { x, y, xs, d, t0, t1 }
  }, [points, W])

  if (!geom) return <div className="empty" ref={ref} role="img" aria-label={ariaLabel}>Waiting for data…</div>
  const last = points[points.length - 1]
  const base = H - PAD.b

  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const px = e.clientX - r.left
    let best = 0, bd = Infinity
    geom.xs.forEach((x, i) => { const dd = Math.abs(x - px); if (dd < bd) { bd = dd; best = i } })
    setHover(best)
  }
  const hp = hover !== null ? points[hover] : null

  return (
    <div className="chart" ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {[0, 0.5, 1].map((g) => (
          <g key={g}>
            <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={geom.y(g)} y2={geom.y(g)} />
            <text className="axis-text" x={PAD.l - 6} y={geom.y(g) + 3} textAnchor="end">{g}</text>
          </g>
        ))}
        {reference !== undefined && (
          <g>
            <line className="ref" x1={PAD.l} x2={W - PAD.r} y1={geom.y(reference)} y2={geom.y(reference)} />
            <text className="ref-text" x={W - PAD.r + 6} y={geom.y(reference) + 3}>{referenceLabel ?? reference.toFixed(2)}</text>
          </g>
        )}
        <path className="area" d={`${geom.d} L${geom.xs[geom.xs.length - 1].toFixed(1)} ${base} L${geom.xs[0].toFixed(1)} ${base} Z`} />
        <path className="line" d={geom.d} />
        <text className="axis-text" x={PAD.l} y={H - 4}>{siteTime(new Date(geom.t0).toISOString())}</text>
        <text className="axis-text" x={W - PAD.r} y={H - 4} textAnchor="end">{siteTime(new Date(geom.t1).toISOString())}</text>
        {hp && hover !== null ? (
          <g>
            <line className="cross" x1={geom.xs[hover]} x2={geom.xs[hover]} y1={PAD.t} y2={base} />
            <circle className="end-dot" cx={geom.xs[hover]} cy={geom.y(hp.v)} r={4} />
          </g>
        ) : (
          <circle className="end-dot" cx={geom.xs[geom.xs.length - 1]} cy={geom.y(last.v)} r={4} />
        )}
      </svg>
      {hp && hover !== null && (
        <div className="tooltip" style={{ left: geom.xs[hover], top: geom.y(hp.v) - 8 }}>
          <b>{valueLabel} {hp.v.toFixed(2)}</b> · {siteTime(new Date(hp.t).toISOString())}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------- horizontal bars

export interface BarRow { label: string; value: number; display?: string; title?: string; color?: string; markClass?: string; glyph?: React.ReactNode }

/** Bars at most 14 px thick, a 4 px rounded data end, square at the baseline, the value at the tip, and the
 *  label in ink (never in the series colour). A zero value draws no bar, only its number. */
export function HBars({ rows, max, ariaLabel }: { rows: BarRow[]; max?: number; ariaLabel: string }) {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value))
  if (!rows.length) return <div className="empty">Nothing to show yet.</div>
  return (
    <div className="hbars" role="img" aria-label={ariaLabel}>
      {rows.map((r) => (
        <div className="hbar" key={r.label} title={r.title} style={r.color ? ({ '--c': r.color } as React.CSSProperties) : undefined}>
          <div className="lbl">{r.glyph ?? (r.markClass ? <span className={`mark ${r.markClass}`} aria-hidden="true" /> : null)}<span>{r.label}</span></div>
          <div className="track">
            {r.value > 0 && <span className="bar" style={{ width: `${Math.max(1, Math.min(100, (r.value / m) * 100)) * 0.7}%` }} />}
            <span className="val">{r.display ?? r.value.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ----------------------------------------------------------------------------------- interval plot

export interface Interval { label: string; sub?: string; diff: number; lo: number; hi: number }

/** A difference with its 95% interval around a zero line: where the interval crosses zero, nothing is shown. */
export function IntervalPlot({ rows, ariaLabel }: { rows: Interval[]; ariaLabel: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const w = useWidth(ref, 640, 300, 1100)
  const lo = Math.min(-0.2, ...rows.map((r) => r.lo)), hi = Math.max(0.2, ...rows.map((r) => r.hi))
  const left = 8, right = 64, rowH = 46, top = 6, h = top + rows.length * rowH + 22
  const x = (v: number) => left + ((v - lo) / (hi - lo)) * (w - left - right)
  return (
    <div className="chart" ref={ref}>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={ariaLabel}>
        {[-0.2, 0, 0.2, 0.4].filter((g) => g >= lo - 1e-9 && g <= hi + 1e-9).map((g) => (
          <g key={g}>
            <line className={g === 0 ? 'ref' : 'grid'} x1={x(g)} x2={x(g)} y1={top} y2={h - 18} />
            <text className="axis-text" x={x(g)} y={h - 4} textAnchor="middle">{g > 0 ? `+${g.toFixed(1)}` : g.toFixed(1)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cy = top + i * rowH + 31
          return (
            <g key={r.label}>
              <text className="row-label" x={left} y={cy - 15}>{r.label}</text>
              <line stroke="var(--accent)" strokeWidth={2} strokeLinecap="round" x1={x(r.lo)} x2={x(r.hi)} y1={cy} y2={cy} />
              <circle className="end-dot" cx={x(r.diff)} cy={cy} r={5} />
              <text className="ref-text" x={w - right + 10} y={cy + 4}>{`${r.diff > 0 ? '+' : ''}${r.diff.toFixed(3)}`}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
