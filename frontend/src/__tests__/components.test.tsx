import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Api, ApiError } from '../api'
import { HBars, IntervalPlot, LineChart } from '../components/Charts'
import { CommandPalette, buildItems, filterItems } from '../components/CommandPalette'
import { HoloFloor } from '../components/HoloFloor'
import { CorrelationPanel, AnomalyFeed, correlationSentence, focusDevice } from '../components/Panels'
import { linePath, spans } from '../components/MiniCharts'
import { StateBadge } from '../components/Glyph'
import { SideNav } from '../components/SideNav'
import Anomalies from '../pages/Anomalies'
import Network from '../pages/Network'
import Scenarios from '../pages/Scenarios'
import { Login } from '../components/Login'
import { SignalRing, activeSignals } from '../components/SignalRing'
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

describe('AnomalyFeed', () => {
  const alerts = [alert('a', 'D-001', 'high'), alert('b', 'D-002', 'low', { status: 'closed', started: '2026-10-06T04:20:00+00:00' })]
  const feed = [{ id: 'f1', ts: '2026-10-06T04:30:00+00:00', kind: 'severity' as const, device_id: 'D-009', severity: 'medium' as Severity, text: 'Rose from Normal to Medium (score 0.70)' }]

  it('pins open alerts with their leading reason and signals, and leaves closed ones out', () => {
    render(<AnomalyFeed alerts={alerts} feed={[]} selected={null} onSelect={() => {}} onOpenAll={() => {}} />)
    expect(screen.getByText('entered a restricted zone')).toBeTruthy()
    expect(screen.queryByText('D-002')).toBeNull()
    expect(screen.getByText('1 open · view all')).toBeTruthy()
  })

  it('narrates activity and selects the device on click', () => {
    const onSelect = vi.fn()
    render(<AnomalyFeed alerts={[]} feed={feed} selected={null} onSelect={onSelect} onOpenAll={() => {}} />)
    fireEvent.click(screen.getByText(/Rose from Normal to Medium/))
    expect(onSelect).toHaveBeenCalledWith('D-009')
  })

  it('explains the empty state', () => {
    render(<AnomalyFeed alerts={[]} feed={[]} selected={null} onSelect={() => {}} onOpenAll={() => {}} />)
    expect(screen.getByText(/Listening/)).toBeTruthy()
  })
})

describe('correlation', () => {
  it('describes how the signals combine, in words', () => {
    expect(correlationSentence(snap('D-1', 'normal', 0.1))).toMatch(/within their normal range/)
    expect(correlationSentence(snap('D-1', 'low', 0.5, { effective: { ...EFF, network: 0.6 } }))).toMatch(/One signal is active: Network 0.60/)
    const s = correlationSentence(snap('D-1', 'high', 0.97, { effective: { spatial: 0.35, network: 0.62, device: 0.5, proximity: 0 } }))
    expect(s).toMatch(/3 signals are active at once/)
    expect(s).toMatch(/strongest alone is 0.62; combined they score 0.97/)
  })

  it('focuses the selected device, else the riskiest one', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99) }
    expect(focusDevice(devices, null)?.device_id).toBe('D-002')
    expect(focusDevice(devices, 'D-001')?.device_id).toBe('D-001')
    expect(focusDevice({}, null)).toBeNull()
  })

  it('shows why an open alert fired, and that the score is not a probability', () => {
    const s = snap('D-003', 'high', 0.99, { effective: { ...EFF, spatial: 1 } })
    render(<CorrelationPanel snap={s} history={{}} alerts={[alert('a', 'D-003', 'high')]} status={STATUS} />)
    expect(screen.getByText('Why it was flagged')).toBeTruthy()
    expect(screen.getByText('entered a restricted zone')).toBeTruthy()
    expect(screen.getByText(/not a probability/)).toBeTruthy()
  })

  it('tells the user what to do when nothing is running', () => {
    render(<CorrelationPanel snap={null} history={{}} alerts={[]} status={null} />)
    expect(screen.getByText(/Start the fleet in Scenarios/)).toBeTruthy()
  })

  it('linePath breaks the line at missing values instead of bridging them', () => {
    expect(linePath([0, 1, 2, 3], [5, 6, NaN, 7])).toBe('M0.0 5.0L1.0 6.0M3.0 7.0')
  })
})

describe('Device network and Anomalies pages', () => {
  it('lists devices riskiest first and opens one on click', () => {
    const onOpen = vi.fn()
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99), 'D-003': snap('D-003', 'low', 0.4, { learning: true }) }
    render(<Network devices={devices} selected={null} onOpen={onOpen} />)
    const ids = screen.getAllByRole('button').map((b) => b.textContent)
    expect(ids.slice(0, 3)).toEqual(['D-002', 'D-003', 'D-001'])
    expect(screen.getByText(/learning/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'D-002' }))
    expect(onOpen).toHaveBeenCalledWith('D-002')
  })

  it('shows the signals behind an alert, and filters to open ones', () => {
    const alerts = [alert('a', 'D-001', 'high'), alert('b', 'D-002', 'low', { status: 'closed', started: '2026-10-06T04:20:00+00:00' })]
    render(<Anomalies alerts={alerts} onOpen={() => {}} />)
    expect(screen.getByText('Signals that combined')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(screen.queryByText('D-002')).toBeNull()
  })
})

describe('SideNav', () => {
  it('marks the current section and switches on click', () => {
    const onView = vi.fn()
    render(<SideNav view="overview" onView={onView} openAlerts={2} />)
    expect(screen.getByRole('button', { name: /Overview/ }).getAttribute('aria-current')).toBe('page')
    fireEvent.click(screen.getByRole('button', { name: /Scenarios/ }))
    expect(onView).toHaveBeenCalledWith('scenarios')
    expect(screen.getByLabelText('2 open')).toBeTruthy()
  })
})

describe('CommandPalette', () => {
  it('filters by every typed term and lists devices before commands', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99, { zone: 'ServerRoom' }) }
    const items = buildItems(devices, () => {}, () => {})
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

describe('Scenarios', () => {
  const demo = (state: DemoStatus['state'], extra: Partial<DemoStatus> = {}): DemoStatus => ({ state, error: null, devices: 10, speed: 20, warmup_progress: 0, sim_time: null, active_episodes: [], ...extra })
  const fakeApi = (over: Record<string, unknown> = {}) => ({ demoStart: vi.fn().mockResolvedValue({}), demoStop: vi.fn(), demoInject: vi.fn().mockResolvedValue({}), demoClear: vi.fn().mockResolvedValue({ cleared: 1 }), ...over }) as unknown as Api
  const page = (api: Api, d: DemoStatus, o: { isAdmin?: boolean; selected?: string | null; onSelect?: () => void } = {}) =>
    <Scenarios api={api} isAdmin={o.isAdmin ?? true} demo={d} selected={o.selected === undefined ? null : o.selected} targets={['D-001', 'D-003']} onSelect={o.onSelect ?? (() => {})} onChanged={() => {}} onWatch={() => {}} />

  it('hides every control from viewers', () => {
    render(page(fakeApi(), demo('idle'), { isAdmin: false, selected: 'D-003' }))
    expect(screen.queryByRole('button', { name: 'Start fleet' })).toBeNull()
    expect(screen.getByText(/Viewers can watch but not control/)).toBeTruthy()
  })

  it('injects the chosen scenario on the selected device and can restore it', async () => {
    const api = fakeApi()
    render(page(api, demo('live'), { selected: 'D-003' }))
    fireEvent.click(screen.getByRole('button', { name: /Breach/ }))
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith({ device_id: 'D-003', scenario: 'spatial', magnitude: 0.8, duration_s: 240 }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore normal' }))
    await waitFor(() => expect(api.demoClear).toHaveBeenCalledWith('D-003'))
  })

  it('acts on the first device when none is chosen, and surfaces a server error', async () => {
    const api = fakeApi({ demoInject: vi.fn().mockRejectedValue(new ApiError(409, 'the demo is not live yet')) })
    const onSelect = vi.fn()
    render(page(api, demo('live'), { onSelect }))
    const b = screen.getByRole('button', { name: /Combined strike/ }) as HTMLButtonElement
    expect(b.disabled).toBe(false)
    fireEvent.click(b)
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith(expect.objectContaining({ device_id: 'D-001', scenario: 'combined' })))
    expect(onSelect).toHaveBeenCalledWith('D-001')
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('the demo is not live yet'))
  })
})

describe('degraded observability', () => {
  const degraded = (id: string, lost: Modality[], over: Partial<Snapshot> = {}) =>
    snap(id, 'normal', 0.05, { unavailable: lost, observability: 'degraded', state: 'degraded', ...over })

  it('never calls a partly observed device Normal', () => {
    const { container } = render(<StateBadge snap={degraded('D-001', ['network'])} />)
    expect(container.textContent).toContain('Degraded')
    expect(container.textContent).not.toContain('Normal')
    cleanup()
    render(<StateBadge snap={snap('D-002', 'normal', 0.05)} />)
    expect(screen.getByText('Normal')).toBeTruthy()
    cleanup()
    render(<StateBadge snap={snap('D-003', 'high', 0.99, { observability: 'degraded', unavailable: ['network'] })} />)
    expect(screen.getByText('High')).toBeTruthy() // an anomaly still shows as an anomaly
  })

  it('names the missing signals on the map and says the position is stale', () => {
    const devices = { 'D-001': degraded('D-001', ['spatial', 'network'], { position_stale: true }) }
    render(<HoloFloor site={SITE} devices={devices} selected={null} onSelect={() => {}} trail={[]} />)
    expect(screen.getByRole('button', { name: /D-001, Degraded, location and network unavailable, score 0.05.*last known position/ })).toBeTruthy()
  })

  it('does not call a device with signals missing ordinary: the sentence says what cannot be seen', () => {
    const s = correlationSentence(degraded('D-001', ['network', 'proximity']))
    expect(s).toMatch(/Not fully observed: network and proximity unavailable/)
    expect(s).toMatch(/says nothing about the ones that cannot/)
    expect(s).not.toMatch(/All four signals are within/)
    expect(correlationSentence(degraded('D-001', ['spatial', 'network', 'device', 'proximity']))).toMatch(/cannot be judged/)
  })

  it('lists blind spots in the live feed and the correlation panel', () => {
    const d = degraded('D-004', ['network'], { fallback: 'no validated threshold for the available signals (spatial+device+proximity)' })
    render(<AnomalyFeed alerts={[]} feed={[]} devices={{ 'D-004': d }} selected={null} onSelect={() => {}} onOpenAll={() => {}} />)
    expect(screen.getByText('Blind spots')).toBeTruthy()
    expect(screen.getByText(/A quiet score here does not mean healthy/)).toBeTruthy()
    cleanup()
    render(<CorrelationPanel snap={d} history={{}} alerts={[]} status={STATUS} />)
    expect(screen.getByLabelText('Unavailable signals').textContent).toMatch(/unavailable: not observed, not normal/)
    expect(screen.getByLabelText('Unavailable signals').textContent).toMatch(/no alert can be raised/)
  })

  it('shows unavailable readings as a dash in the device table and ranks blind spots above normal devices', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': degraded('D-002', ['network', 'device'], { battery: null, cpu_usage: null, network_connections: null }) }
    render(<Network devices={devices} selected={null} onOpen={() => {}} />)
    const firstRow = screen.getAllByRole('row')[1]
    expect(firstRow.textContent).toContain('D-002')
    expect(firstRow.textContent).toContain('Degraded')
    expect(firstRow.querySelectorAll('.na')).toHaveLength(3)
  })

  it('says recovering, not unavailable, while a returned signal refills its window', () => {
    const d = degraded('D-001', ['network'], { recovering: ['network'] })
    expect(correlationSentence(d)).toMatch(/network recovering \(judgment withheld while its window refills\)/)
    render(<CorrelationPanel snap={d} history={{}} alerts={[]} status={STATUS} />)
    expect(screen.getByLabelText('Unavailable signals').textContent).toMatch(/recovering: the signal is back but its window is refilling/)
  })

  it('spans finds the runs where a signal was unavailable', () => {
    expect(spans([false, true, true, false, true])).toEqual([[1, 3], [4, 4]])
    expect(spans(undefined)).toEqual([])
  })

  it('can start a signal loss from Scenarios and reports it as Degraded, not an attack', async () => {
    const api = { demoInject: vi.fn().mockResolvedValue({}), demoClear: vi.fn(), demoStart: vi.fn(), demoStop: vi.fn() } as unknown as Api
    render(<Scenarios api={api} isAdmin demo={{ state: 'live', error: null, devices: 10, speed: 20, warmup_progress: 1, sim_time: null, active_episodes: [] }}
      selected="D-003" targets={['D-001', 'D-003']} onSelect={() => {}} onChanged={() => {}} onWatch={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /Lose network signal/ }))
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith({ device_id: 'D-003', scenario: 'signal_loss', magnitude: 1, duration_s: 240, signals: ['network'] }))
    expect(await screen.findByText(/should show as Degraded, not Normal/)).toBeTruthy()
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
