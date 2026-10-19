import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Api, ApiError } from '../api'
import { HBars, IntervalPlot, LineChart } from '../components/Charts'
import { CommandPalette, buildItems, filterItems } from '../components/CommandPalette'
import { HoloFloor } from '../components/HoloFloor'
import { Inspector } from '../components/Inspector'
import { Login } from '../components/Login'
import { Sentinel } from '../components/Sentinel'
import { SignalRing, activeSignals } from '../components/SignalRing'
import { SimDeck } from '../components/SimDeck'
import { ribbonSeries } from '../components/TimeRibbon'
import { makeIso } from '../iso'
import Evidence from '../pages/Evidence'
import { fleetPeakSeries } from '../pages/Analytics'
import type { Alert, DemoStatus, Modality, Severity, Site, Snapshot, Status } from '../types'

afterEach(() => cleanup())

const EFF: Record<Modality, number> = { spatial: 0, network: 0, device: 0, proximity: 0 }
const snap = (id: string, severity: Severity, score: number, over: Partial<Snapshot> = {}): Snapshot => ({
  device_id: id, ts: '2026-10-06T04:30:00+00:00', latitude: 18.5206, longitude: 73.8569, zone: 'Office', restricted: false, battery: 80,
  cpu_usage: 30, temperature: 40, network_connections: 5, nearby_devices: 2, score, severity, effective: { ...EFF }, contributions: {}, learning: false, ...over,
})
const alert = (id: string, device: string, severity: Severity, over: Partial<Alert> = {}): Alert => ({
  alert_id: id, device_id: device, started: '2026-10-06T04:31:00+00:00', ended: null, peak_score: 0.98, severity, fusion: 'learned',
  explanation: 'x', status: 'open', factors: [{ modality: 'spatial', contribution: 0.9, effective_score: 1, reasons: ['entered a restricted zone'] }], ...over,
})
const SITE: Site = {
  origin: { latitude: 18.5204, longitude: 73.8567 },
  zones: [
    { name: 'Office', kind: 'allowed', polygon: [{ latitude: 18.5204, longitude: 73.8567 }, { latitude: 18.5204, longitude: 73.8571 }, { latitude: 18.5208, longitude: 73.8571 }, { latitude: 18.5208, longitude: 73.8567 }] },
    { name: 'ServerRoom', kind: 'restricted', polygon: [{ latitude: 18.52, longitude: 73.8577 }, { latitude: 18.52, longitude: 73.8581 }, { latitude: 18.5204, longitude: 73.8581 }, { latitude: 18.5204, longitude: 73.8577 }] },
  ],
}
const STATUS = { alert_thresholds: { raise_at: 0.98, clear_at: 0.9 } } as Status

describe('HoloFloor', () => {
  it('draws every zone, labels the restricted one, and gives each device an accessible name with its severity', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99, { zone: 'ServerRoom', restricted: true }) }
    render(<HoloFloor site={SITE} devices={devices} selected={null} onSelect={() => {}} trail={[]} />)
    expect(screen.getByText(/SERVER ROOM · RESTRICTED/)).toBeTruthy()
    expect(screen.getByText('OFFICE')).toBeTruthy()
    expect(screen.getByRole('button', { name: /D-002, High, score 0.99, in ServerRoom/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /D-001, Normal/ })).toBeTruthy()
  })

  it('selects a device on click and on Enter', () => {
    const onSelect = vi.fn()
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'medium', 0.7) }
    render(<HoloFloor site={SITE} devices={devices} selected="D-001" onSelect={onSelect} trail={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /D-002/ }))
    fireEvent.keyDown(screen.getByRole('button', { name: /D-001/ }), { key: 'Enter' })
    expect(onSelect.mock.calls.map((c) => c[0])).toEqual(['D-002', 'D-001'])
  })

  it('pings only for high severity and shows a loading note until the site is known', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99) }
    const { container, rerender } = render(<HoloFloor site={SITE} devices={devices} selected={null} onSelect={() => {}} trail={[]} />)
    expect(container.querySelectorAll('.ping')).toHaveLength(1)
    rerender(<HoloFloor site={null} devices={{}} selected={null} onSelect={() => {}} trail={[]} />)
    expect(screen.getByText('Loading the facility…')).toBeTruthy()
  })
})

describe('iso projection', () => {
  it('keeps projected points inside the view box', () => {
    const iso = makeIso(SITE)
    for (const p of [iso.at(18.5205, 73.8568), iso.at(18.5201, 73.8579)]) {
      expect(p.x).toBeGreaterThanOrEqual(iso.viewBox.x)
      expect(p.x).toBeLessThanOrEqual(iso.viewBox.x + iso.viewBox.w)
      expect(p.y).toBeGreaterThanOrEqual(iso.viewBox.y)
      expect(p.y).toBeLessThanOrEqual(iso.viewBox.y + iso.viewBox.h)
    }
  })
})

describe('SignalRing', () => {
  it('counts signals at or above the alert manager level and announces a lock at three', () => {
    expect(activeSignals({ ...EFF, spatial: 0.3, network: 0.29 })).toBe(1)
    render(<SignalRing effective={{ spatial: 1, network: 0.8, device: 0.5, proximity: 0 }} score={0.99} severity="high" />)
    expect(screen.getByLabelText(/3 of 4 signals active/)).toBeTruthy()
    expect(screen.getByText('LOCK · 3 SIGNALS')).toBeTruthy()
  })
})

describe('Sentinel', () => {
  const alerts = [alert('a', 'D-001', 'high'), alert('b', 'D-002', 'low', { status: 'closed', started: '2026-10-06T04:20:00+00:00' })]
  const feed = [{ id: 'f1', ts: '2026-10-06T04:30:00+00:00', kind: 'severity' as const, device_id: 'D-009', severity: 'medium' as Severity, text: 'Rose from Normal to Medium (score 0.70)' }]

  it('narrates the log first and selects the device on click', () => {
    const onSelect = vi.fn()
    render(<Sentinel alerts={alerts} feed={feed} devices={{}} selected={null} onSelect={onSelect} />)
    fireEvent.click(screen.getByText(/Rose from Normal to Medium/))
    expect(onSelect).toHaveBeenCalledWith('D-009')
  })

  it('lists alerts with their leading reason and filters to open ones', () => {
    render(<Sentinel alerts={alerts} feed={[]} devices={{}} selected={null} onSelect={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: /Alerts/ }))
    expect(screen.getAllByText('entered a restricted zone')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(screen.queryByText('D-002')).toBeNull()
    expect(screen.getByText('D-001')).toBeTruthy()
  })

  it('ranks the roster riskiest first, marks learning devices, and explains empty states', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99), 'D-003': snap('D-003', 'low', 0.4, { learning: true }) }
    const { unmount } = render(<Sentinel alerts={[]} feed={[]} devices={devices} selected={null} onSelect={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Roster' }))
    const ids = screen.getAllByRole('button').filter((b) => b.className.includes('card-btn')).map((b) => b.textContent!.slice(0, 5))
    expect(ids).toEqual(['D-002', 'D-003', 'D-001'])
    expect(screen.getByText(/learning/)).toBeTruthy()
    unmount()
    render(<Sentinel alerts={[]} feed={[]} devices={{}} selected={null} onSelect={() => {}} />)
    expect(screen.getByText(/Listening/)).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /Alerts/ }))
    expect(screen.getByText(/No alerts yet/)).toBeTruthy()
  })
})

describe('Inspector', () => {
  it('prompts for a selection when none is made', () => {
    render(<Inspector snap={null} points={[]} alerts={[]} status={null} />)
    expect(screen.getByText(/Select a beacon/i)).toBeTruthy()
  })

  it('shows why an open alert fired and states that the score is not a probability', () => {
    const s = snap('D-003', 'high', 0.99, { effective: { ...EFF, spatial: 1 }, contributions: { spatial: 0.89 } })
    render(<Inspector snap={s} points={[]} alerts={[alert('a', 'D-003', 'high')]} status={STATUS} />)
    expect(screen.getByText('Why it was flagged')).toBeTruthy()
    expect(screen.getByText('entered a restricted zone')).toBeTruthy()
    expect(screen.getByText(/not a probability/)).toBeTruthy()
  })

  it('says when baselines are still learning', () => {
    render(<Inspector snap={snap('D-003', 'normal', 0.05, { learning: true })} points={[]} alerts={[]} status={STATUS} />)
    expect(screen.getByText(/Still learning/)).toBeTruthy()
  })
})

describe('CommandPalette', () => {
  it('filters by every typed term and lists devices before commands', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99, { zone: 'ServerRoom' }) }
    const items = buildItems(devices, () => {}, () => {}, () => {})
    expect(items[0].id).toBe('D-002')
    expect(filterItems(items, 'high server').map((i) => i.id)).toEqual(['D-002'])
    expect(filterItems(items, '')).toHaveLength(items.length)
    expect(filterItems(items, 'zzz')).toHaveLength(0)
  })

  it('runs the highlighted item on Enter and closes', () => {
    const run = vi.fn(), onClose = vi.fn()
    render(<CommandPalette open onClose={onClose} items={[{ id: 'x', label: 'Go to Archive', hint: 'stats', run }]} />)
    fireEvent.keyDown(screen.getByLabelText('Search'), { key: 'Enter' })
    expect(run).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })
})

describe('ribbonSeries', () => {
  it('keeps the highest score per bucket, in time order', () => {
    const p = (ts: string, score: number) => ({ t: Date.parse(ts), score, severity: 'normal', lat: 0, lon: 0 }) as never
    const s = ribbonSeries({ a: [p('2026-10-06T04:30:05+00:00', 0.2), p('2026-10-06T04:30:10+00:00', 0.7)], b: [p('2026-10-06T04:30:30+00:00', 0.4)] })
    expect(s.map((x) => x.v)).toEqual([0.7, 0.4])
  })
})

describe('charts', () => {
  it('HBars gives every row a visible name and value, draws no bar for zero, and has an empty state', () => {
    const { container, rerender } = render(<HBars ariaLabel="demo" rows={[{ label: 'Network', value: 0.8 }, { label: 'Location', value: 0 }]} max={1} />)
    expect(screen.getByText('Network')).toBeTruthy()
    expect(screen.getByText('0.80')).toBeTruthy()
    expect(screen.getByText('0.00')).toBeTruthy()
    expect(container.querySelectorAll('.bar')).toHaveLength(1) // the zero row has its number but no sliver of a bar
    rerender(<HBars ariaLabel="demo" rows={[]} />)
    expect(screen.getByText('Nothing to show yet.')).toBeTruthy()
  })

  it('LineChart waits for data, then draws a line and labels the reference level', () => {
    const { container, rerender } = render(<LineChart points={[]} ariaLabel="score" />)
    expect(screen.getByText('Waiting for data…')).toBeTruthy()
    rerender(<LineChart points={[{ t: 0, v: 0.1 }, { t: 1000, v: 0.9 }]} reference={0.98} referenceLabel="alert 0.98" ariaLabel="score" />)
    expect(container.querySelector('path.line')).toBeTruthy()
    expect(screen.getByText('alert 0.98')).toBeTruthy()
  })

  it('IntervalPlot prints each difference with its sign', () => {
    render(<IntervalPlot ariaLabel="x" rows={[{ label: 'A vs B', diff: 0.432, lo: 0.387, hi: 0.476 }, { label: 'C vs D', diff: -0.092, lo: -0.12, hi: -0.066 }]} />)
    expect(screen.getByText('+0.432')).toBeTruthy()
    expect(screen.getByText('-0.092')).toBeTruthy()
  })

  it('fleetPeakSeries keeps the highest score per minute, in order', () => {
    const p = (ts: string, score: number) => ({ device_id: 'D-001', ts, score, severity: 'normal' as Severity, effective: { ...EFF } })
    const s = fleetPeakSeries([p('2026-10-06T04:31:30+00:00', 0.9), p('2026-10-06T04:30:10+00:00', 0.2), p('2026-10-06T04:30:50+00:00', 0.6)])
    expect(s.map((x) => x.v)).toEqual([0.6, 0.9])
    expect(s[0].t).toBeLessThan(s[1].t)
    // an earlier run (a week before) must not be drawn beside the current one
    const withOldRun = fleetPeakSeries([p('2026-10-27T04:31:30+00:00', 0.99), p('2026-11-03T04:31:30+00:00', 0.5)])
    expect(withOldRun.map((x) => x.v)).toEqual([0.5])
  })
})

describe('Login', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('rejects a bad token with a clear message and does not log in', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: 'Unauthorized', json: async () => ({ detail: 'x' }) }))
    const onLogin = vi.fn()
    render(<Login onLogin={onLogin} />)
    fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'not-a-real-token' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/not accepted/))
    expect(onLogin).not.toHaveBeenCalled()
  })

  it('passes the role the server reports', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ role: 'viewer', auth: true }) }))
    const onLogin = vi.fn()
    render(<Login onLogin={onLogin} />)
    fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'viewer-token-1234' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith('viewer-token-1234', 'viewer'))
  })

  it('will not submit a token that is too short to be valid', () => {
    render(<Login onLogin={() => {}} />)
    fireEvent.change(screen.getByLabelText('Access token'), { target: { value: 'short' } })
    expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('SimDeck', () => {
  const demo = (state: DemoStatus['state'], extra: Partial<DemoStatus> = {}): DemoStatus => ({ state, error: null, devices: 10, speed: 20, warmup_progress: 0, sim_time: null, active_episodes: [], ...extra })
  const fakeApi = (over: Record<string, unknown> = {}) => ({ demoStart: vi.fn().mockResolvedValue({}), demoStop: vi.fn(), demoInject: vi.fn().mockResolvedValue({}), demoClear: vi.fn().mockResolvedValue({ cleared: 1 }), ...over }) as unknown as Api
  const deck = (api: Api, d: DemoStatus, o: { isAdmin?: boolean; selected?: string | null } = {}) =>
    <SimDeck api={api} isAdmin={o.isAdmin ?? true} targets={['D-001','D-003']} demo={d} selected={o.selected === undefined ? null : o.selected} onChanged={() => {}} open onToggle={() => {}} />

  it('hides every control from viewers', () => {
    render(deck(fakeApi(), demo('idle'), { isAdmin: false, selected: 'D-003' }))
    expect(screen.queryByRole('button', { name: 'Start fleet' })).toBeNull()
    expect(screen.getByText(/Viewers can watch but not control/)).toBeTruthy()
  })

  it('injects the chosen scenario on the selected device and can restore it', async () => {
    const api = fakeApi()
    render(deck(api, demo('live'), { selected: 'D-003' }))
    fireEvent.click(screen.getByRole('button', { name: /Breach/ }))
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith({ device_id: 'D-003', scenario: 'spatial', magnitude: 0.8, duration_s: 240 }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore normal' }))
    await waitFor(() => expect(api.demoClear).toHaveBeenCalledWith('D-003'))
  })

  it('acts on the first device when none is chosen, and surfaces a server error', async () => {
    const api = fakeApi({ demoInject: vi.fn().mockRejectedValue(new ApiError(409, 'the demo is not live yet')) })
    const onSelect = vi.fn()
    render(<SimDeck api={api} isAdmin demo={demo('live')} selected={null} targets={['D-001', 'D-003']} onSelect={onSelect} onChanged={() => {}} open onToggle={() => {}} />)
    const b = screen.getByRole('button', { name: /Combined strike/ }) as HTMLButtonElement
    expect(b.disabled).toBe(false)
    fireEvent.click(b)
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith(expect.objectContaining({ device_id: 'D-001', scenario: 'combined' })))
    expect(onSelect).toHaveBeenCalledWith('D-001')
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('the demo is not live yet'))
  })
})

describe('Evidence page', () => {
  it('reports each pre-registered comparison with its interval and a plain-language reading', () => {
    render(<Evidence />)
    expect(screen.getAllByText(/Clear gain/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/No difference shown/).length).toBeGreaterThan(0) // P2 and P4 cross zero
    expect(screen.getAllByText(/Worse/).length).toBeGreaterThan(0) // P3: the supervised joint model is better
    expect(screen.getByText(/says nothing about real fleets/)).toBeTruthy()
  })
})
