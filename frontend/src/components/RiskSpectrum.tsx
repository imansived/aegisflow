import { useRef } from 'react'
import { SEVERITY_LABEL } from '../format'
import type { Severity, Snapshot } from '../types'
import { useWidth } from './Charts'

const SEV_COLOR: Record<Severity, string> = { normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)' }
const H = 150, PAD = { l: 16, r: 16, t: 22, b: 30 }

/** Every device laid along one axis from 0 (ordinary) to 1 (extreme). A crowd of quiet devices on the left and a few
 *  outliers on the right is exactly what a healthy fleet looks like; a cluster drifting right is the early warning. */
export function RiskSpectrum({ devices, threshold }: { devices: Snapshot[]; threshold?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const W = useWidth(ref, 520, 280, 1200)
  const x = (v: number) => PAD.l + Math.min(1, Math.max(0, v)) * (W - PAD.l - PAD.r)
  // Stack devices whose scores are close so ticks never hide each other.
  const sorted = [...devices].sort((a, b) => a.score - b.score)
  const rows: number[] = []
  const placed = sorted.map((d) => {
    let row = 0
    while ((rows[row] ?? -Infinity) > x(d.score) - 9) row++
    rows[row] = x(d.score)
    return { d, row }
  })
  const base = H - PAD.b
  const label = new Set([...sorted].slice(-4).map((d) => d.device_id))

  return (
    <div className="chart" ref={ref}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Risk spectrum: ${devices.length} devices from 0 to 1`}>
        <line className="axis" x1={PAD.l} x2={W - PAD.r} y1={base} y2={base} />
        {[0, 0.25, 0.5, 0.75, 1].map((g) => <g key={g}><line className="tick" x1={x(g)} x2={x(g)} y1={base} y2={base + 4} /><text className="axis-text" x={x(g)} y={H - 10} textAnchor="middle">{g}</text></g>)}
        {threshold !== undefined && <g><line className="ref" x1={x(threshold)} x2={x(threshold)} y1={PAD.t - 6} y2={base} /><text className="ref-text" x={x(threshold)} y={PAD.t - 9} textAnchor="end">alert level</text></g>}
        {placed.map(({ d, row }) => (
          <g key={d.device_id} style={{ ['--c' as string]: SEV_COLOR[d.severity] }} className={`spec-dot sev-${d.severity}`}>
            <title>{`${d.device_id}: ${SEVERITY_LABEL[d.severity]}, ${d.score.toFixed(2)}`}</title>
            <line x1={x(d.score)} x2={x(d.score)} y1={base} y2={base - 14 - row * 11} />
            <circle cx={x(d.score)} cy={base - 14 - row * 11} r={4.2} />
            {label.has(d.device_id) && <text x={x(d.score)} y={base - 24 - row * 11} textAnchor="middle">{d.device_id}</text>}
          </g>
        ))}
      </svg>
    </div>
  )
}
