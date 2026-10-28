import { SEVERITY_LABEL } from '../format'
import type { Modality, Severity } from '../types'
import { MODALITY_LABEL } from '../format'

/** Severity is never carried by colour alone: each level has its own shape AND a word. */
export function SeverityBadge({ severity, label = true }: { severity: Severity; label?: boolean }) {
  return (
    <span className={`sev sev-${severity}`} title={SEVERITY_LABEL[severity]}>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        {severity === 'normal' && <circle cx="6" cy="6" r="3.5" />}
        {severity === 'low' && <path d="M6 1 11 6 6 11 1 6Z" />}
        {severity === 'medium' && <path d="M6 1 11.5 11H.5Z" />}
        {severity === 'high' && <path d="M4 .8h4L11.2 4v4L8 11.2H4L.8 8V4Z" />}
      </svg>
      {label ? SEVERITY_LABEL[severity] : <span className="sr">{SEVERITY_LABEL[severity]}</span>}
    </span>
  )
}

/** Degraded observability: part of this device cannot be seen. A hollow, dashed shape and the word, never colour alone,
 *  and deliberately unlike every severity glyph: a quiet dashboard must not read as a healthy device. */
export function DegradedBadge({ lost, label = true }: { lost?: string; label?: boolean }) {
  const text = lost ? `Degraded: ${lost} unavailable` : 'Degraded'
  return (
    <span className="sev sev-degraded" title={text}>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <circle cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2 1.6" />
        <path d="M3.4 8.6 8.6 3.4" stroke="currentColor" strokeWidth="1.4" />
      </svg>
      {label ? 'Degraded' : <span className="sr">{text}</span>}
    </span>
  )
}

/** The one badge for a device: Anomalous (a severity glyph) beats Degraded beats Normal. */
export function StateBadge({ snap, label = true }: { snap: { severity: Severity; observability?: string; unavailable?: Modality[] }; label?: boolean }) {
  if (snap.severity === 'normal' && snap.observability && snap.observability !== 'full') {
    return <DegradedBadge lost={(snap.unavailable ?? []).join(' and ')} label={label} />
  }
  return <SeverityBadge severity={snap.severity} label={label} />
}

export function ModalityMark({ modality, withLabel = true }: { modality: Modality; withLabel?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span className={`mark m-${modality}`} aria-hidden="true" />
      {withLabel && <span>{MODALITY_LABEL[modality]}</span>}
    </span>
  )
}
