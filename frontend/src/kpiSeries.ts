import { HISTORY_WINDOW_MS, type LiveState } from './live'

export interface KpiSeries { devices: number[]; open: number[]; peak: number[] }

// One value per bucket across the last HISTORY_WINDOW_MS of simulated time, taken from what the dashboard already holds:
// devices that had reported by then, alerts that had started and not yet ended, and the highest latest score.
export function kpiSeries(live: LiveState, buckets = 24): KpiSeries {
  const hist = Object.values(live.history).filter((h) => h.length)
  const empty: KpiSeries = { devices: [], open: [], peak: [] }
  if (!hist.length) return empty
  const end = hist.reduce((m, h) => Math.max(m, h[h.length - 1].t), -Infinity)
  const alerts = live.alerts.map((a) => ({ s: Date.parse(a.started), e: a.ended ? Date.parse(a.ended) : Infinity }))
  const out: KpiSeries = { devices: [], open: [], peak: [] }
  for (let i = 0; i < buckets; i++) {
    const t = end - HISTORY_WINDOW_MS + (HISTORY_WINDOW_MS * i) / (buckets - 1)
    let seen = 0
    let peak = 0
    for (const h of hist) {
      let last = -1
      for (let k = h.length - 1; k >= 0; k--) if (h[k].t <= t) { last = k; break }
      if (last >= 0) { seen++; peak = Math.max(peak, h[last].score) }
    }
    out.devices.push(seen)
    out.peak.push(peak)
    out.open.push(alerts.filter((a) => a.s <= t && a.e > t).length)
  }
  return out
}
