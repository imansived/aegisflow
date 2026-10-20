import { useEffect, useMemo, useRef, useState } from 'react'
import { SEVERITY_LABEL, sevRank } from '../format'
import type { Snapshot } from '../types'
import type { View } from './SideNav'

export interface PaletteItem { id: string; label: string; hint: string; run: () => void }

/** Pure: the items that match a query, devices riskiest first. Exported for tests. */
export function filterItems(items: PaletteItem[], query: string): PaletteItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  const terms = q.split(/\s+/)
  return items.filter((i) => terms.every((t) => `${i.label} ${i.hint}`.toLowerCase().includes(t)))
}

export function buildItems(devices: Record<string, Snapshot>, onDevice: (id: string) => void, onView: (v: View) => void): PaletteItem[] {
  const devs = Object.values(devices).sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || b.score - a.score)
    .map((d): PaletteItem => ({ id: d.device_id, label: d.device_id, hint: `${SEVERITY_LABEL[d.severity]} · ${d.zone ?? 'in transit'} · score ${d.score.toFixed(2)}`, run: () => onDevice(d.device_id) }))
  return [
    ...devs,
    { id: 'go-overview', label: 'Go to Overview', hint: 'map, live anomalies, correlation', run: () => onView('overview') },
    { id: 'go-network', label: 'Go to Device Network', hint: 'every device at a glance', run: () => onView('network') },
    { id: 'go-anomalies', label: 'Go to Anomalies', hint: 'all alerts and why they fired', run: () => onView('anomalies') },
    { id: 'go-scenarios', label: 'Go to Scenarios', hint: 'start the fleet, inject threats', run: () => onView('scenarios') },
    { id: 'go-stats', label: 'Go to Statistics', hint: 'fleet-wide numbers', run: () => onView('stats') },
    { id: 'go-evidence', label: 'Go to Evidence', hint: 'what the final test showed', run: () => onView('evidence') },
  ]
}

export function CommandPalette({ open, onClose, items }: { open: boolean; onClose: () => void; items: PaletteItem[] }) {
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const shown = useMemo(() => filterItems(items, q).slice(0, 12), [items, q])

  useEffect(() => { if (open) { setQ(''); setIdx(0); setTimeout(() => input.current?.focus(), 0) } }, [open])
  useEffect(() => { setIdx(0) }, [q])
  if (!open) return null

  const choose = (i: PaletteItem | undefined) => { if (i) { i.run(); onClose() } }
  return (
    <div className="palette-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input ref={input} value={q} placeholder="Type a device id, a zone, a severity, or a command…" aria-label="Search" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
            else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(shown.length - 1, i + 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
            else if (e.key === 'Enter') choose(shown[idx])
          }} />
        <ul role="listbox" aria-label="Results">
          {shown.length === 0 && <li className="none">Nothing matches.</li>}
          {shown.map((i, n) => (
            <li key={i.id} role="option" aria-selected={n === idx} className={n === idx ? 'on' : ''} onMouseEnter={() => setIdx(n)} onClick={() => choose(i)}>
              <b>{i.label}</b><span>{i.hint}</span>
            </li>
          ))}
        </ul>
        <footer><kbd>↑</kbd><kbd>↓</kbd> move <kbd>Enter</kbd> go <kbd>Esc</kbd> close</footer>
      </div>
    </div>
  )
}
