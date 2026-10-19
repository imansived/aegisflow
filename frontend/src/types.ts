// Shapes of the JSON the backend sends (backend/api/*, manual §3.22). Kept in one place so the compiler
// catches a mismatch between the API and the UI.

export type Severity = 'normal' | 'low' | 'medium' | 'high'
export type Modality = 'spatial' | 'network' | 'device' | 'proximity'
export const MODALITIES: Modality[] = ['spatial', 'network', 'device', 'proximity']

export interface Snapshot {
  device_id: string
  ts: string
  latitude: number
  longitude: number
  zone: string | null
  restricted: boolean
  battery: number
  cpu_usage: number
  temperature: number
  network_connections: number
  nearby_devices: number
  score: number
  severity: Severity
  effective: Record<Modality, number>
  contributions: Partial<Record<Modality, number>>
  learning: boolean
}

export interface Factor {
  modality: Modality
  contribution: number
  effective_score: number
  reasons: string[]
}

export interface Alert {
  alert_id: string
  device_id: string
  started: string
  ended: string | null
  peak_score: number
  severity: Severity
  fusion: string
  factors: Factor[]
  explanation: string
  status?: 'open' | 'closed'
  event?: 'opened' | 'escalated' | 'updated' | 'closed'
}

export interface DeviceInfo {
  device_id: string
  name: string
  type: string
  created_at: string
  has_key: boolean
  live: Snapshot | null
}

export interface Zone {
  name: string
  kind: 'allowed' | 'restricted'
  polygon: { latitude: number; longitude: number }[]
}

export interface Site {
  origin: { latitude: number; longitude: number }
  zones: Zone[]
}

export interface Status {
  uptime_s: number
  fusion: string
  correlation: { half_life_s: number; score_type: string }
  alert_thresholds: { raise_at: number; clear_at: number }
  stream: { length: number; lag: number | null; pending: number }
  dead_letters: number
  processor: { processed: number; failed: number; p50_ms?: number; p95_ms?: number; p99_ms?: number } | null
  devices_live: number
  websocket_clients: number
}

export interface DemoEpisode {
  episode_id: string
  device_id: string
  scenario: string
  magnitude: number
  ends_in_sim_s: number
}

export interface DemoStatus {
  state: 'idle' | 'registering' | 'warming' | 'live' | 'stopping' | 'error'
  error: string | null
  devices: number
  speed: number
  warmup_progress: number
  sim_time: string | null
  active_episodes: DemoEpisode[]
}

export interface HistoryPoint {
  device_id: string
  ts: string
  score: number
  severity: Severity
  effective: Record<Modality, number>
}

export type WsMessage =
  | { type: 'snapshot'; live: Snapshot[]; open_alerts: Alert[] }
  | ({ type: 'score' } & Snapshot)
  | ({ type: 'alert' } & Alert)
  | { type: 'reset' } // the server restarted its analysis: drop live devices and histories
  | { type: 'ping' }
