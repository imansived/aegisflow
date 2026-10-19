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

export function ModalityMark({ modality, withLabel = true }: { modality: Modality; withLabel?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span className={`mark m-${modality}`} aria-hidden="true" />
      {withLabel && <span>{MODALITY_LABEL[modality]}</span>}
    </span>
  )
}
