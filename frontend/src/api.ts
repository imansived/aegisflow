import type { Alert, DemoStatus, DeviceInfo, HistoryPoint, Site, Status } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface AlertQuery {
  device_id?: string
  status?: 'open' | 'closed'
  limit?: number
}

/** Typed client for the AegisFlow API. All paths go through `/api`, which the dev server and the production
 *  reverse proxy both map to the backend, so the browser only ever talks to its own origin. */
export class Api {
  private token: string
  private base: string

  constructor(token: string, base = '/api') {
    this.token = token
    this.base = base
  }

  private async req<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const res = await fetch(this.base + path, {
      method,
      headers: { Authorization: `Bearer ${this.token}`, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      let detail = res.statusText || `HTTP ${res.status}`
      try {
        const j = await res.json()
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
      } catch {
        /* keep the status text */
      }
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as T
  }

  private static qs(params: Record<string, string | number | undefined>): string {
    const q = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
    return q.length ? '?' + q.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&') : ''
  }

  me = () => this.req<{ role: 'admin' | 'viewer'; auth: boolean }>('GET', '/me')
  site = () => this.req<Site>('GET', '/site')
  status = () => this.req<Status & { demo: DemoStatus }>('GET', '/system/status')
  devices = () => this.req<DeviceInfo[]>('GET', '/devices')
  alerts = (q: AlertQuery = {}) => this.req<Alert[]>('GET', '/alerts' + Api.qs({ ...q }))
  history = (device_id?: string, limit = 500) => this.req<HistoryPoint[]>('GET', '/anomalies/history' + Api.qs({ device_id, limit }))
  demoStatus = () => this.req<DemoStatus>('GET', '/demo/status')
  demoStart = (body: { devices: number; warmup_hours: number; speed: number }) => this.req<DemoStatus>('POST', '/demo/start', body)
  demoStop = () => this.req<DemoStatus>('POST', '/demo/stop', {})
  demoInject = (body: { device_id: string; scenario: string; magnitude: number; duration_s: number; signals?: string[] }) =>
    this.req<{ episode_id: string }>('POST', '/demo/inject', body)
  demoClear = (device_id: string) => this.req<{ cleared: number }>('POST', '/demo/clear', { device_id })
}
