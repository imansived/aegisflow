import type { Modality, Severity, Snapshot } from './types'

export const SEVERITY_ORDER: Severity[] = ['normal', 'low', 'medium', 'high']
export const sevRank = (s: Severity): number => SEVERITY_ORDER.indexOf(s)
export const SEVERITY_LABEL: Record<Severity, string> = { normal: 'Normal', low: 'Low', medium: 'Medium', high: 'High' }

export const MODALITY_LABEL: Record<Modality, string> = {
  spatial: 'Location',
  network: 'Network',
  device: 'Device health',
  proximity: 'Proximity',
}

/** Signals this device reported as unavailable. An unavailable signal is NOT a normal one (manual §5.11). */
export const lostSignals = (s: Pick<Snapshot, 'unavailable'>): Modality[] => s.unavailable ?? []
export const isDegraded = (s: Pick<Snapshot, 'observability'>): boolean => !!s.observability && s.observability !== 'full'
/** What to show for a device: Anomalous wins, then Degraded (partly observed), else Normal. */
export const deviceState = (s: Snapshot): 'anomalous' | 'degraded' | 'normal' =>
  s.severity !== 'normal' ? 'anomalous' : isDegraded(s) ? 'degraded' : 'normal'
export const lostText = (s: Pick<Snapshot, 'unavailable'>): string => lostSignals(s).map((m) => MODALITY_LABEL[m].toLowerCase()).join(' and ')

const siteClock = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Kolkata', // the simulated site's local time (contracts/site.py SITE_TZ)
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

/** HH:MM:SS in site local time. Telemetry carries simulated time, so this is the site's clock, not the viewer's. */
export function siteTime(iso: string): string {
  const t = Date.parse(iso)
  return Number.isNaN(t) ? '—' : siteClock.format(t)
}

export const score2 = (x: number): string => x.toFixed(2)

/** The weekday and clock of the site, e.g. "Tue 10:42". */
export function siteDayTime(iso: string): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const day = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', weekday: 'short' }).format(t)
  return `${day} ${siteClock.format(t).slice(0, 5)}`
}

export function formatUptime(s: number): string {
  if (s < 90) return `${Math.round(s)} s`
  if (s < 5400) return `${Math.round(s / 60)} min`
  return `${(s / 3600).toFixed(1)} h`
}
