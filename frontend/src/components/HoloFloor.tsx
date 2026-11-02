import { useMemo, useState } from 'react'
import { MODALITY_LABEL, SEVERITY_LABEL, isDegraded, lostSignals, lostText, sevRank } from '../format'
import { makeIso, path, type Pt } from '../iso'
import type { Point } from '../live'
import { MODALITIES, type Severity, type Site, type Snapshot } from '../types'
import { ACTIVE_LEVEL, SIGNAL_COLOR, activeSignals } from './SignalRing'

const SEV_COLOR: Record<Severity, string> = {
  normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)',
}
const ZONE_HEIGHT = { allowed: 7, restricted: 12 } as const
const SPIRE_RADIUS = 4.2 // metres around the selected device
const SPIRE_MAX = 26 // metres of height for a signal at 1.0

/** The facility as a holographic blueprint. Zones are translucent volumes, devices are beacons that float above
 *  the floor (higher = more anomalous), and the selected device raises four signal spires; when several rise
 *  together their tips are joined by a beam: correlation, made visible. */
export function HoloFloor({ site, devices, selected, onSelect, trail }: {
  site: Site | null; devices: Record<string, Snapshot>; selected: string | null; onSelect: (id: string) => void; trail: Point[]
}) {
  const iso = useMemo(() => (site ? makeIso(site, 6, 30) : null), [site])
  const [hover, setHover] = useState<string | null>(null)
  if (!site || !iso) return <div className="empty stage-empty">Loading the facility…</div>

  const { x, y, w, h } = iso.viewBox
  const solids = site.zones.map((z) => ({ z, s: iso.solid(z, ZONE_HEIGHT[z.kind]) })).sort((a, b) => a.s.depth - b.s.depth)
  const list = Object.values(devices).sort((a, b) => iso.at(a.latitude, a.longitude).y - iso.at(b.latitude, b.longitude).y || sevRank(a.severity) - sevRank(b.severity))
  const sel = selected ? devices[selected] : undefined
  const trailPts = trail.filter((p) => Number.isFinite(p.lat)).slice(-48).map((p) => iso.at(p.lat, p.lon))

  return (
    <svg className="floor" viewBox={`${x} ${y} ${w} ${h}`} role="group" aria-label="Facility map, isometric view" preserveAspectRatio="xMidYMid meet">
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="0.9" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <filter id="glow-lg" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="2.4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--line-strong)" stopOpacity="0.5" /><stop offset="1" stopColor="var(--line-strong)" stopOpacity="0.04" /></linearGradient>
        <radialGradient id="hot-halo"><stop offset="0" stopColor="var(--sev-high)" stopOpacity="0.3" /><stop offset="0.6" stopColor="var(--sev-high)" stopOpacity="0.08" /><stop offset="1" stopColor="var(--sev-high)" stopOpacity="0" /></radialGradient>
        <linearGradient id="wall-hot" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--sev-high)" stopOpacity="0.55" /><stop offset="1" stopColor="var(--sev-high)" stopOpacity="0.05" /></linearGradient>
        <pattern id="scan" width="3" height="3" patternUnits="userSpaceOnUse" patternTransform="rotate(30)"><line x1="0" y1="0" x2="0" y2="3" stroke="var(--sev-high)" strokeWidth="0.5" opacity="0.55" /></pattern>
      </defs>

      <g className="floor-grid">{iso.grid(10).map(([a, b], i) => <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />)}</g>

      {solids.map(({ z, s }) => {
        const hot = z.kind === 'restricted'
        return (
          <g key={z.name} className={`vol ${hot ? 'hot' : ''}`}>
            {hot && <ellipse className="hot-halo" cx={s.center.x} cy={s.floor.reduce((m, q) => m + q.y, 0) / s.floor.length} rx={Math.max(...s.floor.map((q) => q.x)) - Math.min(...s.floor.map((q) => q.x))} ry={(Math.max(...s.floor.map((q) => q.y)) - Math.min(...s.floor.map((q) => q.y))) * 1.3} fill="url(#hot-halo)" />}
            <path className="vol-floor" d={path(s.floor)} />
            {s.sides.map((q, i) => <path key={i} className="vol-side" d={path(q)} fill={hot ? 'url(#wall-hot)' : 'url(#wall)'} />)}
            <path className="vol-top" d={path(s.top)} />
            {hot && <path d={path(s.top)} fill="url(#scan)" opacity="0.8" />}
            <text className="vol-label" x={s.top[3]?.x ?? s.center.x} y={(s.top[3]?.y ?? s.center.y) - 2.4}>{z.name.replace(/([a-z])([A-Z])/g, '$1 $2').toUpperCase()}{hot ? ' · RESTRICTED' : ''}</text>
          </g>
        )
      })}

      {trailPts.length > 1 && <polyline className="trail" points={trailPts.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')} />}

      {list.map((d) => {
        const lift = 3 + d.score * 13
        const foot = iso.at(d.latitude, d.longitude)
        const head = iso.at(d.latitude, d.longitude, lift)
        const isSel = selected === d.device_id
        const color = SEV_COLOR[d.severity]
        const degraded = isDegraded(d)
        const status = d.severity === 'normal' && degraded ? `Degraded, ${lostText(d)} unavailable` : degraded ? `${SEVERITY_LABEL[d.severity]}, partly observed: ${lostText(d)} unavailable` : SEVERITY_LABEL[d.severity]
        const name = `${d.device_id}, ${status}, score ${d.score.toFixed(2)}${d.zone ? `, in ${d.zone}` : ''}${d.position_stale ? ', last known position' : ''}`
        const showLabel = isSel || hover === d.device_id || d.severity !== 'normal' || degraded
        return (
          <g key={d.device_id} className={`beacon sev-${d.severity} ${isSel ? 'sel' : ''} ${degraded ? 'degraded' : ''} ${d.position_stale ? 'stale' : ''}`} style={{ ['--c' as string]: color }} tabIndex={0} role="button"
            aria-label={name} aria-pressed={isSel} onClick={() => onSelect(d.device_id)} onPointerEnter={() => setHover(d.device_id)} onPointerLeave={() => setHover(null)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(d.device_id) } }}>
            <title>{name}</title>
            <path className="foot" d={path(iso.ring(d.latitude, d.longitude, d.severity === 'normal' ? 1.4 : 2.2))} />
            {d.severity === 'high' && <path className="ping" d={path(iso.ring(d.latitude, d.longitude, 2.6))} />}
            <line className="stem" x1={foot.x} y1={foot.y} x2={head.x} y2={head.y} />
            <circle className="head" cx={head.x} cy={head.y} r={d.severity === 'normal' ? 1.15 : 1.7} filter={d.severity === 'normal' ? undefined : 'url(#glow)'} />
            {isSel && <path className="halo" d={path(iso.ring(d.latitude, d.longitude, 3.6))} />}
            {showLabel && <text className="tag" x={head.x} y={head.y - 3}>{d.device_id}</text>}
          </g>
        )
      })}

      {sel && <Spires iso={iso} d={sel} />}
    </svg>
  )
}

function Spires({ iso, d }: { iso: ReturnType<typeof makeIso>; d: Snapshot }) {
  const tips: { m: (typeof MODALITIES)[number]; foot: Pt; tip: Pt; v: number; lost: boolean }[] = MODALITIES.map((m, i) => {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4
    const du = SPIRE_RADIUS * Math.cos(a)
    const dv = SPIRE_RADIUS * Math.sin(a)
    const lost = lostSignals(d).includes(m)
    const v = lost ? 0 : Math.min(1, Math.max(0, d.effective[m]))
    return { m, v, lost, foot: iso.near(d.latitude, d.longitude, du, dv, 0), tip: iso.near(d.latitude, d.longitude, du, dv, 1.5 + v * SPIRE_MAX) }
  })
  const active = tips.filter((t) => t.v >= ACTIVE_LEVEL)
  const locked = activeSignals(d.effective) >= 2
  return (
    <g className={`spires ${locked ? 'locked' : ''}`} aria-hidden="true">
      {tips.map((t) => (
        <g key={t.m} style={{ ['--c' as string]: SIGNAL_COLOR[t.m] }} className={t.lost ? 'spire lost' : t.v >= ACTIVE_LEVEL ? 'spire on' : 'spire'}>
          {t.lost && <title>{MODALITY_LABEL[t.m]} unavailable</title>}
          <line className="spire-base" x1={t.foot.x} y1={t.foot.y} x2={t.tip.x} y2={t.tip.y} />
          <circle className="spire-cap" cx={t.tip.x} cy={t.tip.y} r={1.25} filter="url(#glow)" />
        </g>
      ))}
      {locked && <path className="weave" d={path(active.map((t) => t.tip))} />}
      {locked && active.map((t) => <line key={t.m} className="beam" x1={t.tip.x} y1={t.tip.y} x2={iso.at(d.latitude, d.longitude, 3 + d.score * 13).x} y2={iso.at(d.latitude, d.longitude, 3 + d.score * 13).y} style={{ ['--c' as string]: SIGNAL_COLOR[t.m] }} />)}
    </g>
  )
}
