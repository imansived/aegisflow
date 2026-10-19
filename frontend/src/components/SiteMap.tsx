import { useMemo } from 'react'
import { SEVERITY_LABEL, sevRank } from '../format'
import { makeProjection } from '../geo'
import type { Point } from '../live'
import type { Severity, Site, Snapshot } from '../types'
import { SeverityBadge } from './Glyph'

const SEV_VAR: Record<Severity, string> = {
  normal: 'var(--sev-normal)', low: 'var(--sev-low)', medium: 'var(--sev-medium)', high: 'var(--sev-high)',
}

/** The campus drawn from the same zone geometry the detectors use. Markers carry severity by colour AND
 *  size/outline; only unusual or selected devices are labelled, so a crowded room stays readable. */
export function SiteMap({ site, devices, selected, onSelect, trail }: {
  site: Site | null; devices: Record<string, Snapshot>; selected: string | null; onSelect: (id: string) => void; trail: Point[]
}) {
  const proj = useMemo(() => (site ? makeProjection(site) : null), [site])
  if (!site || !proj) return <div className="empty">Loading site…</div>

  const { x, y, w, h } = proj.viewBox
  const list = Object.values(devices).sort((a, b) => sevRank(a.severity) - sevRank(b.severity)) // severe on top
  const trailPts = trail.filter((p) => Number.isFinite(p.lat)).slice(-40).map((p) => proj.toSvg(p.lat, p.lon))

  return (
    <>
      <svg className="map" viewBox={`${x} ${y} ${w} ${h}`} role="group" aria-label="Site map">
        <defs>
          <pattern id="hatch" width="2.4" height="2.4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="2.4" height="2.4" fill="var(--wash)" />
            <line x1="0" y1="0" x2="0" y2="2.4" stroke="var(--sev-high)" strokeWidth="0.5" opacity="0.7" />
          </pattern>
        </defs>
        {site.zones.map((z) => {
          const c = proj.zoneCorner(z)
          return (
            <g key={z.name}>
              <path className={`zone ${z.kind}`} d={proj.zonePath(z)} />
              <text className="zone-label" x={c.x + 1.0} y={c.y + 2.9}>{z.kind === 'restricted' ? `${z.name} · restricted` : z.name}</text>
            </g>
          )
        })}
        {trailPts.length > 1 && (
          <polyline points={trailPts.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')} fill="none" stroke="var(--ink2)" strokeWidth="0.35" strokeLinecap="round" opacity="0.6" />
        )}
        {list.map((d) => {
          const p = proj.toSvg(d.latitude, d.longitude)
          const sev = d.severity
          const label = `${d.device_id}, ${SEVERITY_LABEL[sev]}, score ${d.score.toFixed(2)}${d.zone ? `, in ${d.zone}` : ''}`
          const r = sev === 'normal' ? 1.1 : sev === 'low' ? 1.3 : 1.6
          return (
            <g key={d.device_id} className={`marker ${sev === 'high' ? 'pulse' : ''}`} tabIndex={0} role="button" aria-label={label}
               aria-pressed={selected === d.device_id} onClick={() => onSelect(d.device_id)}
               onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(d.device_id) } }}>
              <title>{label}</title>
              {sev === 'high' && <circle className="halo" cx={p.x} cy={p.y} r={r + 0.6} />}
              <circle className="dot" cx={p.x} cy={p.y} r={r} fill={SEV_VAR[sev]} />
              {selected === d.device_id && <circle className="sel" cx={p.x} cy={p.y} r={r + 1.1} />}
              {(sev !== 'normal' || selected === d.device_id) && <text x={p.x} y={p.y - r - 1.4}>{d.device_id}</text>}
            </g>
          )
        })}
      </svg>
      <div className="legend" aria-label="Map legend">
        {(['normal', 'low', 'medium', 'high'] as Severity[]).map((s) => <SeverityBadge key={s} severity={s} />)}
        <span><span className="swatch" style={{ background: 'repeating-linear-gradient(45deg, var(--sev-high) 0 1px, transparent 1px 4px)' }} />Restricted zone</span>
      </div>
    </>
  )
}
