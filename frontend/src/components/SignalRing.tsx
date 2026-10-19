import { MODALITY_LABEL, SEVERITY_LABEL } from '../format'
import { MODALITIES, type Modality, type Severity } from '../types'

export const SIGNAL_COLOR: Record<Modality, string> = {
  spatial: 'var(--sig-spatial)',
  network: 'var(--sig-network)',
  device: 'var(--sig-device)',
  proximity: 'var(--sig-proximity)',
}

export const ACTIVE_LEVEL = 0.3 // matches the alert manager: a signal at or above this counts as contributing

/** How many signals are contributing at once: the project's whole thesis, as a number. */
export const activeSignals = (eff: Record<Modality, number>): number => MODALITIES.filter((m) => eff[m] >= ACTIVE_LEVEL).length

const SEV_COLOR: Record<Severity, string> = {
  normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)',
}

const R = 78 // ring radius in a 200x200 box
const QUARTER = (2 * Math.PI * R) / 4

/** Four arcs, one per signal, each filling clockwise as that signal rises, around a core that glows with the fused
 *  score. When three or more arcs are lit the outer ring locks and spins: a correlation lock. */
export function SignalRing({ effective, score, severity, size = 220 }: {
  effective: Record<Modality, number>; score: number; severity: Severity; size?: number
}) {
  const n = activeSignals(effective)
  const locked = n >= 3
  const c = SEV_COLOR[severity]
  return (
    <figure className={`ring ${locked ? 'locked' : ''}`} style={{ width: size, height: size, ['--core' as string]: c }}
      aria-label={`Signal ring: ${n} of 4 signals active, anomaly score ${score.toFixed(2)}, ${SEVERITY_LABEL[severity]}`}>
      <svg viewBox="0 0 200 200" aria-hidden="true">
        <defs>
          <radialGradient id="core-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--core)" stopOpacity={0.15 + 0.55 * score} />
            <stop offset="100%" stopColor="var(--core)" stopOpacity="0" />
          </radialGradient>
          <filter id="ring-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <circle cx="100" cy="100" r="64" fill="url(#core-glow)" />
        <circle className="ring-lock" cx="100" cy="100" r="92" />
        <circle className="ring-lock inner" cx="100" cy="100" r="86" />
        {MODALITIES.map((m, i) => {
          const v = Math.min(1, Math.max(0, effective[m]))
          const start = i * 90 - 90 + 6
          return (
            <g key={m} transform={`rotate(${start} 100 100)`}>
              <circle className="arc-track" cx="100" cy="100" r={R} strokeDasharray={`${QUARTER - 14} ${2 * Math.PI * R}`} />
              <circle className="arc" cx="100" cy="100" r={R} stroke={SIGNAL_COLOR[m]} filter="url(#ring-glow)"
                strokeDasharray={`${Math.max(0.01, (QUARTER - 14) * v)} ${2 * Math.PI * R}`} />
            </g>
          )
        })}
        <text className="ring-score" x="100" y="104" textAnchor="middle">{score.toFixed(2)}</text>
        <text className="ring-sub" x="100" y="122" textAnchor="middle">{locked ? `LOCK · ${n} SIGNALS` : n ? `${n} SIGNAL${n > 1 ? 'S' : ''}` : 'NOMINAL'}</text>
      </svg>
      <figcaption className="sr">{MODALITIES.map((m) => `${MODALITY_LABEL[m]} ${effective[m].toFixed(2)}`).join(', ')}</figcaption>
    </figure>
  )
}
