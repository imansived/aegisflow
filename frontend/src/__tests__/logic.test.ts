import { afterEach, describe, expect, it, vi } from 'vitest'
import { Api, ApiError } from '../api'
import { siteTime, formatUptime } from '../format'
import { makeProjection } from '../geo'
import { kpiSeries } from '../kpiSeries'
import { fleetCounts } from '../components/Panels'
import { MAX_FEED, MAX_POINTS, backoffDelay, initialState, parseMessage, reducer } from '../live'
import type { Alert, HistoryPoint, Modality, Severity, Site, Snapshot } from '../types'

const EFF: Record<Modality, number> = { spatial: 0, network: 0, device: 0, proximity: 0 }

function snap(over: Partial<Snapshot> = {}): Snapshot {
  return {
    device_id: 'D-001', ts: '2026-10-06T04:30:00+00:00', latitude: 18.5206, longitude: 73.8569, zone: 'Office', restricted: false,
    battery: 80, cpu_usage: 30, temperature: 40, network_connections: 5, nearby_devices: 2, score: 0.1, severity: 'normal',
    effective: EFF, contributions: {}, learning: false, ...over,
  }
}

function alert(over: Partial<Alert> = {}): Alert {
  return {
    alert_id: 'D-001@2026-10-06T04:31:00+00:00', device_id: 'D-001', started: '2026-10-06T04:31:00+00:00', ended: null,
    peak_score: 0.99, severity: 'high', fusion: 'learned', explanation: 'x',
    factors: [{ modality: 'spatial', contribution: 0.9, effective_score: 1, reasons: ['entered a restricted zone'] }], ...over,
  }
}

const SITE: Site = {
  origin: { latitude: 18.5204, longitude: 73.8567 },
  zones: [
    { name: 'Office', kind: 'allowed', polygon: [
      { latitude: 18.5204, longitude: 73.8567 }, { latitude: 18.5204, longitude: 73.8571 },
      { latitude: 18.5208, longitude: 73.8571 }, { latitude: 18.5208, longitude: 73.8567 }] },
    { name: 'ServerRoom', kind: 'restricted', polygon: [
      { latitude: 18.52, longitude: 73.8577 }, { latitude: 18.52, longitude: 73.8581 },
      { latitude: 18.5204, longitude: 73.8581 }, { latitude: 18.5204, longitude: 73.8577 }] },
  ],
}

describe('projection', () => {
  const p = makeProjection(SITE)
  it('maps the origin to (0,0) and puts north up', () => {
    expect(p.toSvg(18.5204, 73.8567)).toEqual({ x: 0, y: -0 })
    const north = p.toSvg(18.5205, 73.8567) // 0.0001 deg ~ 11.1 m
    expect(north.y).toBeCloseTo(-11.12, 1)
    expect(north.x).toBeCloseTo(0, 6)
  })
  it('scales longitude by cos(latitude)', () => {
    const east = p.toSvg(18.5204, 73.8568)
    expect(east.x).toBeCloseTo(0.0001 * 111_195 * Math.cos((18.5204 * Math.PI) / 180), 2)
  })
  it('builds a viewBox that contains every zone with a margin', () => {
    const { x, y, w, h } = p.viewBox
    for (const z of SITE.zones) for (const pt of z.polygon) {
      const s = p.toSvg(pt.latitude, pt.longitude)
      expect(s.x).toBeGreaterThan(x); expect(s.x).toBeLessThan(x + w)
      expect(s.y).toBeGreaterThan(y); expect(s.y).toBeLessThan(y + h)
    }
  })
  it('produces a closed path and a centre inside the zone', () => {
    const z = SITE.zones[0]
    expect(p.zonePath(z).startsWith('M')).toBe(true)
    expect(p.zonePath(z).endsWith('Z')).toBe(true)
    const c = p.zoneCenter(z)
    expect(c.x).toBeGreaterThan(0); expect(c.y).toBeLessThan(0)
  })
})

describe('live reducer', () => {
  it('replaces devices from a snapshot, seeds history, and marks open alerts open', () => {
    const s = reducer(initialState, { type: 'snapshot', live: [snap(), snap({ device_id: 'D-002' })], open_alerts: [alert()] })
    expect(Object.keys(s.devices)).toEqual(['D-001', 'D-002'])
    expect(s.history['D-001']).toHaveLength(1)
    expect(s.alerts[0].status).toBe('open')
  })

  it('appends history points, de-duplicating the same timestamp', () => {
    let s = reducer(initialState, { type: 'score', snap: snap({ score: 0.2 }) })
    s = reducer(s, { type: 'score', snap: snap({ score: 0.3 }) })  // same ts: replaces
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:05+00:00', score: 0.4 }) })
    expect(s.history['D-001'].map((p) => p.score)).toEqual([0.3, 0.4])
  })

  it('caps history length', () => {
    let s = initialState
    for (let i = 0; i < MAX_POINTS + 50; i++) s = reducer(s, { type: 'score', snap: snap({ ts: new Date(1_700_000_000_000 + i * 5000).toISOString() }) })
    expect(s.history['D-001']).toHaveLength(MAX_POINTS)
  })

  it('writes a feed entry only when severity changes, saying which way', () => {
    let s = reducer(initialState, { type: 'score', snap: snap() })
    expect(s.feed).toHaveLength(0) // the first sighting is not a change
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:05+00:00', severity: 'high', score: 0.97 }) })
    expect(s.feed[0].text).toMatch(/Rose from Normal to High/)
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:10+00:00', severity: 'high', score: 0.98 }) })
    expect(s.feed).toHaveLength(1)
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:15+00:00', severity: 'low', score: 0.35 }) })
    expect(s.feed[0].text).toMatch(/Fell from High to Low/)
  })

  it('caps the feed and keeps it newest-first', () => {
    let s = initialState
    for (let i = 0; i < MAX_FEED + 20; i++) {
      const sev: Severity = i % 2 ? 'high' : 'normal'
      s = reducer(s, { type: 'score', snap: snap({ ts: new Date(1_700_000_000_000 + i * 5000).toISOString(), severity: sev }) })
    }
    expect(s.feed).toHaveLength(MAX_FEED)
    expect(s.feed[0].id > s.feed[1].id || s.feed[0].ts >= s.feed[1].ts).toBe(true)
  })

  it('opens then closes the same alert without duplicating it', () => {
    let s = reducer(initialState, { type: 'alert', alert: alert({ event: 'opened' }) })
    expect(s.alerts).toHaveLength(1)
    expect(s.alerts[0].status).toBe('open')
    s = reducer(s, { type: 'alert', alert: alert({ event: 'closed', ended: '2026-10-06T04:36:00+00:00', peak_score: 0.995 }) })
    expect(s.alerts).toHaveLength(1)
    expect(s.alerts[0].status).toBe('closed')
    expect(s.alerts[0].peak_score).toBe(0.995)
    expect(s.feed.map((f) => f.text)).toEqual(expect.arrayContaining([expect.stringMatching(/closed/), expect.stringMatching(/High anomaly: entered a restricted zone/)]))
  })

  it('updates an open alert in place when another signal joins, and says so in the feed', () => {
    let s = reducer(initialState, { type: 'alert', alert: alert({ event: 'opened' }) })
    const joined = alert({
      event: 'updated',
      factors: [
        { modality: 'spatial', contribution: 0.5, effective_score: 1, reasons: ['entered a restricted zone'] },
        { modality: 'network', contribution: 0.4, effective_score: 0.9, reasons: [] },
      ],
    })
    s = reducer(s, { type: 'alert', alert: joined })
    expect(s.alerts).toHaveLength(1)
    expect(s.alerts[0].factors).toHaveLength(2)
    expect(s.alerts[0].status).toBe('open')
    expect(s.feed[0].text).toBe('Another signal joined (2 now): spatial, network')
  })

  it('keeps alerts newest first', () => {
    const s = reducer(initialState, { type: 'alerts_loaded', alerts: [
      alert({ alert_id: 'a', started: '2026-10-06T01:00:00+00:00' }), alert({ alert_id: 'b', started: '2026-10-06T03:00:00+00:00' }), alert({ alert_id: 'c', started: '2026-10-06T02:00:00+00:00' })] })
    expect(s.alerts.map((a) => a.alert_id)).toEqual(['b', 'c', 'a'])
  })

  it('merges loaded history before the live points without duplicating timestamps', () => {
    let s = reducer(initialState, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:10+00:00' }) })
    const old: HistoryPoint[] = [
      { device_id: 'D-001', ts: '2026-10-06T04:30:10+00:00', score: 0.9, severity: 'high', effective: EFF },
      { device_id: 'D-001', ts: '2026-10-06T04:30:00+00:00', score: 0.2, severity: 'normal', effective: EFF }]
    s = reducer(s, { type: 'history_loaded', device_id: 'D-001', points: old })
    expect(s.history['D-001'].map((p) => p.t)).toEqual([Date.parse('2026-10-06T04:30:00+00:00'), Date.parse('2026-10-06T04:30:10+00:00')])
    expect(s.history['D-001'][1].score).toBe(0.1) // the live value wins over the stored one
  })

  it('ignores stored history from an earlier run, which would draw as a sawtooth beside the live points', () => {
    let s = reducer(initialState, { type: 'score', snap: snap({ ts: '2026-11-03T04:30:00+00:00' }) })
    const p = (ts: string, score: number): HistoryPoint => ({ device_id: 'D-001', ts, score, severity: 'normal', effective: EFF })
    s = reducer(s, { type: 'history_loaded', device_id: 'D-001', points: [
      p('2026-10-27T04:35:00+00:00', 0.99), // a week earlier: a previous run
      p('2026-11-03T04:20:00+00:00', 0.2), // ten minutes before now: keep
      p('2026-11-03T03:00:00+00:00', 0.3), // an hour before now: outside the window
      p('2026-11-03T05:00:00+00:00', 0.4), // after the newest live point: cannot be part of this run yet
    ] })
    expect(s.history['D-001'].map((x) => x.score)).toEqual([0.2, 0.1])
  })

  it('drops the old run on reset but keeps stored alerts, so the new run starts clean', () => {
    let s = reducer(initialState, { type: 'snapshot', live: [snap()], open_alerts: [alert()] })
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-10-06T04:30:05+00:00', severity: 'high', score: 0.97 }) })
    expect(Object.keys(s.devices)).toHaveLength(1)
    expect(s.feed).toHaveLength(1)
    s = reducer(s, { type: 'reset' })
    expect(s.devices).toEqual({})
    expect(s.history).toEqual({})
    expect(s.feed).toEqual([])
    expect(s.alerts).toHaveLength(1)
    // and the next run's first point is not anchored to the old run's time
    s = reducer(s, { type: 'score', snap: snap({ ts: '2026-11-03T04:30:00+00:00' }) })
    expect(s.history['D-001']).toHaveLength(1)
  })

  it('tracks the connection state', () => {
    expect(reducer(initialState, { type: 'connection', state: 'open' }).connection).toBe('open')
  })
})

describe('websocket helpers', () => {
  it('backs off exponentially, capped, with bounded jitter', () => {
    const none = () => 0, max = () => 1
    expect([0, 1, 2, 3].map((a) => backoffDelay(a, none))).toEqual([1000, 2000, 4000, 8000])
    expect(backoffDelay(10, none)).toBe(15_000)
    expect(backoffDelay(10, max)).toBe(18_750)
  })
  it('accepts known frames and ignores junk', () => {
    expect(parseMessage('{"type":"ping"}')).toEqual({ type: 'ping' })
    expect(parseMessage('{"type":"reset"}')).toEqual({ type: 'reset' })
    expect(parseMessage('{"type":"warp"}')).toBeNull()
    expect(parseMessage('not json')).toBeNull()
    expect(parseMessage('null')).toBeNull()
  })
})

describe('formatting', () => {
  it('shows the SITE clock (IST), not the viewer clock', () => {
    expect(siteTime('2026-10-06T04:30:05+00:00')).toBe('10:00:05')
    expect(siteTime('garbage')).toBe('—')
  })
  it('formats uptime compactly', () => {
    expect([30, 600, 7200].map(formatUptime)).toEqual(['30 s', '10 min', '2.0 h'])
  })
})

describe('api client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the bearer token and returns the JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ role: 'admin', auth: true }) })
    vi.stubGlobal('fetch', fetchMock)
    expect(await new Api('secret-token').me()).toEqual({ role: 'admin', auth: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/me')
    expect(init.headers.Authorization).toBe('Bearer secret-token')
  })

  it('turns an error response into an ApiError carrying the server message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: 'Conflict', json: async () => ({ detail: 'the demo is already running' }) }))
    await expect(new Api('t').demoStart({ devices: 10, warmup_hours: 34, speed: 20 })).rejects.toMatchObject({ status: 409, message: 'the demo is already running' })
    await expect(new Api('t').me()).rejects.toBeInstanceOf(ApiError)
  })

  it('encodes query parameters and omits empty ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] })
    vi.stubGlobal('fetch', fetchMock)
    await new Api('t').alerts({ device_id: 'D-003', status: 'open', limit: 50 })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/alerts?device_id=D-003&status=open&limit=50')
    await new Api('t').history(undefined, 10)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/anomalies/history?limit=10')
  })

  it('posts JSON bodies', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ cleared: 1 }) })
    vi.stubGlobal('fetch', fetchMock)
    await new Api('t').demoClear('D-003')
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ device_id: 'D-003' })
    expect(init.headers['Content-Type']).toBe('application/json')
  })
})

describe('kpi trend series', () => {
  const T0 = Date.parse('2026-10-06T04:00:00+00:00')
  const pt = (min: number, score: number) => ({ t: T0 + min * 60_000, score, severity: 'normal' as Severity })
  const live = (history: Record<string, ReturnType<typeof pt>[]>, alerts: Alert[] = []) =>
    ({ ...initialState, history, alerts } as unknown as Parameters<typeof kpiSeries>[0])

  it('is empty without history', () => {
    expect(kpiSeries(initialState)).toEqual({ devices: [], open: [], peak: [] })
  })
  it('counts devices as they report and tracks the highest latest score', () => {
    const k = kpiSeries(live({ A: [pt(0, 0.2), pt(30, 0.6)], B: [pt(20, 0.9)] }), 4)
    expect(k.devices).toEqual([1, 1, 2, 2])
    expect(k.peak[0]).toBeCloseTo(0.2)
    expect(k.peak[3]).toBeCloseTo(0.9)
  })
  it('counts an alert only between its start and end', () => {
    const a = alert({ started: new Date(T0 + 10 * 60_000).toISOString(), ended: new Date(T0 + 25 * 60_000).toISOString() })
    const k = kpiSeries(live({ A: [pt(0, 0.1), pt(30, 0.1)] }, [a]), 4)
    expect(k.open).toEqual([0, 1, 1, 0])
  })
})

describe('fleet ring counts', () => {
  it('counts each device once: flagged first, then degraded, else normal', () => {
    const devs = {
      a: snap({ device_id: 'a' }),
      b: snap({ device_id: 'b', severity: 'high', score: 1 }),
      c: snap({ device_id: 'c', severity: 'normal', observability: 'degraded', unavailable: ['network'] } as Partial<Snapshot>),
      d: snap({ device_id: 'd', severity: 'low', observability: 'degraded', unavailable: ['network'] } as Partial<Snapshot>),
    }
    expect(fleetCounts(devs)).toEqual({ normal: 1, degraded: 1, flagged: 2, total: 4 })
  })
})
