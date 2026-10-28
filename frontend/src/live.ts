import { useEffect, useReducer, type Dispatch } from 'react'
import { MODALITY_LABEL, SEVERITY_LABEL, sevRank } from './format'
import { MODALITIES, type Alert, type HistoryPoint, type Modality, type Severity, type Snapshot, type WsMessage } from './types'

export interface FeedItem {
  id: string
  ts: string
  kind: 'alert' | 'severity' | 'observability'
  device_id: string
  severity: Severity
  text: string
}

export interface Point {
  t: number // epoch ms of the (simulated) event time
  score: number
  severity: Severity
  lat: number // NaN for points loaded from history, which carries no position
  lon: number
  effective: Record<Modality, number>
  // Telemetry, NaN where unknown (points loaded from stored history carry only the score and the four signals).
  battery: number
  cpu: number
  temp: number
  conns: number
  sent: number
  recv: number
  missing: number // bitmask of signals reported unavailable at this point (bit = MODALITIES order); 0 = all seen
}

export interface LiveState {
  connection: 'connecting' | 'open' | 'closed'
  devices: Record<string, Snapshot>
  alerts: Alert[] // newest first, unique by alert_id
  feed: FeedItem[] // newest first
  history: Record<string, Point[]> // oldest first
}

export const HISTORY_WINDOW_MS = 30 * 60_000 // a device chart shows at most the last 30 (simulated) minutes
export const MAX_FEED = 150
export const MAX_POINTS = 240
export const MAX_ALERTS = 300

export const initialState: LiveState = { connection: 'connecting', devices: {}, alerts: [], feed: [], history: {} }

export type Action =
  | { type: 'connection'; state: LiveState['connection'] }
  | { type: 'reset' }
  | { type: 'snapshot'; live: Snapshot[]; open_alerts: Alert[] }
  | { type: 'score'; snap: Snapshot }
  | { type: 'alert'; alert: Alert }
  | { type: 'alerts_loaded'; alerts: Alert[] }
  | { type: 'history_loaded'; device_id: string; points: HistoryPoint[] }

export const missingMask = (s: Snapshot): number => (s.unavailable ?? []).reduce((m, x) => m | (1 << MODALITIES.indexOf(x)), 0)

const toPoint = (s: Snapshot): Point => ({
  t: Date.parse(s.ts), score: s.score, severity: s.severity, lat: s.latitude, lon: s.longitude, effective: s.effective,
  battery: s.battery ?? NaN, cpu: s.cpu_usage ?? NaN, temp: s.temperature ?? NaN, conns: s.network_connections ?? NaN,
  sent: s.bytes_sent ?? NaN, recv: s.bytes_received ?? NaN, missing: missingMask(s),
})

function upsertAlerts(existing: Alert[], incoming: Alert[]): Alert[] {
  const byId = new Map(existing.map((a) => [a.alert_id, a]))
  for (const a of incoming) byId.set(a.alert_id, { ...byId.get(a.alert_id), ...a })
  return [...byId.values()].sort((x, y) => (x.started < y.started ? 1 : x.started > y.started ? -1 : 0)).slice(0, MAX_ALERTS)
}

const pushFeed = (feed: FeedItem[], item: FeedItem): FeedItem[] => [item, ...feed].slice(0, MAX_FEED)

function alertText(a: Alert): string {
  const lead = a.factors[0]
  const why = lead?.reasons[0] ?? lead?.modality ?? 'elevated behaviour'
  if (a.event === 'closed') return `Alert closed (peak ${a.peak_score.toFixed(2)})`
  if (a.event === 'escalated') return `Escalated to ${SEVERITY_LABEL[a.severity]}: ${why}`
  if (a.event === 'updated') return `Another signal joined (${a.factors.length} now): ${a.factors.map((f) => f.modality).join(', ')}`
  return `${SEVERITY_LABEL[a.severity]} anomaly: ${why}`
}

export function reducer(state: LiveState, action: Action): LiveState {
  switch (action.type) {
    case 'connection':
      return { ...state, connection: action.state }

    case 'reset':
      // A new run: its devices and score histories replace the old run's. Alerts are kept (they are stored
      // history, and the server has already closed any that were open).
      return { ...state, devices: {}, history: {}, feed: [] }

    case 'snapshot': {
      const devices: Record<string, Snapshot> = {}
      const history = { ...state.history }
      for (const s of action.live) {
        devices[s.device_id] = s
        if (!history[s.device_id]?.length) history[s.device_id] = [toPoint(s)]
      }
      return {
        ...state, devices, history,
        alerts: upsertAlerts(state.alerts, action.open_alerts.map((a) => ({ ...a, status: 'open' as const }))),
      }
    }

    case 'score': {
      const s = action.snap
      const prev = state.devices[s.device_id]
      let feed = state.feed
      if (prev && prev.severity !== s.severity) {
        const up = sevRank(s.severity) > sevRank(prev.severity)
        feed = pushFeed(feed, {
          id: `${s.device_id}@${s.ts}`, ts: s.ts, kind: 'severity', device_id: s.device_id, severity: s.severity,
          text: `${up ? 'Rose' : 'Fell'} from ${SEVERITY_LABEL[prev.severity]} to ${SEVERITY_LABEL[s.severity]} (score ${s.score.toFixed(2)})`,
        })
      }
      const gone = (s.unavailable ?? []).filter((m) => !(prev?.unavailable ?? []).includes(m))
      const back = (prev?.unavailable ?? []).filter((m) => !(s.unavailable ?? []).includes(m))
      if (prev && gone.length) {
        feed = pushFeed(feed, {
          id: `${s.device_id}@${s.ts}:lost`, ts: s.ts, kind: 'observability', device_id: s.device_id, severity: 'normal',
          text: `Lost the ${gone.map((m) => MODALITY_LABEL[m].toLowerCase()).join(' and ')} signal: now only partly observed (a quiet score is not evidence of normal behaviour)`,
        })
      } else if (prev && back.length) {
        feed = pushFeed(feed, {
          id: `${s.device_id}@${s.ts}:back`, ts: s.ts, kind: 'observability', device_id: s.device_id, severity: 'normal',
          text: `${back.map((m) => MODALITY_LABEL[m]).join(' and ')} signal restored${(s.unavailable ?? []).length ? '' : ': fully observed again'}`,
        })
      }
      const pts = state.history[s.device_id] ?? []
      const last = pts[pts.length - 1]
      const p = toPoint(s)
      const next = last && last.t === p.t ? [...pts.slice(0, -1), p] : [...pts, p]
      return {
        ...state, feed,
        devices: { ...state.devices, [s.device_id]: s },
        history: { ...state.history, [s.device_id]: next.slice(-MAX_POINTS) },
      }
    }

    case 'alert': {
      const a = action.alert
      const stored: Alert = { ...a, status: a.event === 'closed' ? 'closed' : 'open' }
      return {
        ...state,
        alerts: upsertAlerts(state.alerts, [stored]),
        feed: pushFeed(state.feed, {
          id: `${a.alert_id}:${a.event}:${a.peak_score}`, ts: a.ended ?? a.started, kind: 'alert', device_id: a.device_id,
          severity: a.severity, text: alertText(a),
        }),
      }
    }

    case 'alerts_loaded':
      return { ...state, alerts: upsertAlerts(state.alerts, action.alerts) }

    case 'history_loaded': {
      const existing = state.history[action.device_id] ?? []
      const all: Point[] = action.points.map((h) => ({
        t: Date.parse(h.ts), score: h.score, severity: h.severity, lat: NaN, lon: NaN, effective: h.effective, battery: NaN, cpu: NaN, temp: NaN, conns: NaN, sent: NaN, recv: NaN, missing: 0,
      }))
      // Only the recent past, relative to the live data. The server returns a device's newest stored points
      // across ALL runs; a demo run starts a week later in simulated time, so without this window an old
      // run's alert episodes were drawn into the same chart, squeezed together (a sawtooth, seen in the browser).
      const newest = existing.length ? existing[existing.length - 1].t : Math.max(-Infinity, ...all.map((p) => p.t))
      const loaded = all.filter((p) => p.t <= newest && p.t >= newest - HISTORY_WINDOW_MS)
      const seen = new Set(existing.map((p) => p.t))
      const merged = [...loaded.filter((p) => !seen.has(p.t)), ...existing].sort((a, b) => a.t - b.t)
      return { ...state, history: { ...state.history, [action.device_id]: merged.slice(-MAX_POINTS) } }
    }
  }
}

/** Reconnect delay: 1 s, 2 s, 4 s ... capped at 15 s, with up to 25% jitter so many tabs do not reconnect in lockstep. */
export function backoffDelay(attempt: number, rand: () => number = Math.random): number {
  const base = Math.min(15_000, 1000 * 2 ** attempt)
  return Math.round(base * (1 + 0.25 * rand()))
}

export const SILENCE_LIMIT_MS = 60_000 // the server pings every 25 s; silence this long means the link is dead

export function parseMessage(raw: string): WsMessage | null {
  try {
    const m = JSON.parse(raw)
    if (m && typeof m === 'object' && ['snapshot', 'score', 'alert', 'reset', 'ping'].includes(m.type)) return m as WsMessage
  } catch {
    /* ignore malformed frames */
  }
  return null
}

export function useLive(token: string | null): [LiveState, Dispatch<Action>] {
  const [state, dispatch] = useReducer(reducer, initialState)

  useEffect(() => {
    if (!token) return
    let ws: WebSocket | null = null
    let attempt = 0
    let retry: ReturnType<typeof setTimeout> | undefined
    let lastMessage = Date.now()
    let stopped = false

    const connect = () => {
      dispatch({ type: 'connection', state: 'connecting' })
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`)
      ws.onopen = () => {
        attempt = 0
        lastMessage = Date.now()
        dispatch({ type: 'connection', state: 'open' })
      }
      ws.onmessage = (e) => {
        lastMessage = Date.now()
        const m = parseMessage(String(e.data))
        if (!m || m.type === 'ping') return
        if (m.type === 'reset') dispatch({ type: 'reset' })
        else if (m.type === 'snapshot') dispatch({ type: 'snapshot', live: m.live, open_alerts: m.open_alerts })
        else if (m.type === 'score') dispatch({ type: 'score', snap: m })
        else dispatch({ type: 'alert', alert: m })
      }
      ws.onclose = () => {
        dispatch({ type: 'connection', state: 'closed' })
        if (!stopped) retry = setTimeout(connect, backoffDelay(attempt++))
      }
    }

    connect()
    const watchdog = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMessage > SILENCE_LIMIT_MS) ws.close()
    }, 10_000)
    return () => {
      stopped = true
      clearInterval(watchdog)
      if (retry) clearTimeout(retry)
      ws?.close()
    }
  }, [token])

  return [state, dispatch]
}
