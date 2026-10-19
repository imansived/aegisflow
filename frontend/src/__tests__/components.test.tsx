import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Api, ApiError } from '../api'
import { AlertFeed } from '../components/AlertFeed'
import { HBars, IntervalPlot, LineChart } from '../components/Charts'
import { DemoPanel } from '../components/DemoPanel'
import { DeviceDetail } from '../components/DeviceDetail'
import { DeviceList } from '../components/DeviceList'
import { Login } from '../components/Login'
import { SiteMap } from '../components/SiteMap'
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

describe('SiteMap', () => {
  it('draws every zone, labels the restricted one, and gives each device an accessible name with its severity', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99, { zone: 'ServerRoom', restricted: true }) }
    render(<SiteMap site={SITE} devices={devices} selected={null} onSelect={() => {}} trail={[]} />)
    expect(screen.getByText('ServerRoom · restricted')).toBeTruthy()
    expect(screen.getByText('Office')).toBeTruthy()
    expect(screen.getByRole('button', { name: /D-002, High, score 0.99, in ServerRoom/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /D-001, Normal/ })).toBeTruthy()
  })

  it('selects a device on click and on Enter, and labels only unusual or selected devices', () => {
    const onSelect = vi.fn()
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'medium', 0.7) }
    const { container } = render(<SiteMap site={SITE} devices={devices} selected="D-001" onSelect={onSelect} trail={[]} />)
    fireEvent.click(screen.getByRole('button', { name: /D-002/ }))
    fireEvent.keyDown(screen.getByRole('button', { name: /D-001/ }), { key: 'Enter' })
    expect(onSelect.mock.calls.map((c) => c[0])).toEqual(['D-002', 'D-001'])
    const labels = [...container.querySelectorAll('.marker text')].map((t) => t.textContent)
    expect(labels.sort()).toEqual(['D-001', 'D-002']) // D-001 because selected, D-002 because unusual
  })

  it('does not label a quiet, unselected device and shows a pulse only for high severity', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99) }
    const { container } = render(<SiteMap site={SITE} devices={devices} selected={null} onSelect={() => {}} trail={[]} />)
    expect([...container.querySelectorAll('.marker text')].map((t) => t.textContent)).toEqual(['D-002'])
    expect(container.querySelectorAll('.marker.pulse')).toHaveLength(1)
  })

  it('shows a loading note until the site is known', () => {
    render(<SiteMap site={null} devices={{}} selected={null} onSelect={() => {}} trail={[]} />)
    expect(screen.getByText('Loading site…')).toBeTruthy()
  })
})

describe('DeviceList', () => {
  it('lists the highest risk first and marks learning devices', () => {
    const devices = { 'D-001': snap('D-001', 'normal', 0.1), 'D-002': snap('D-002', 'high', 0.99), 'D-003': snap('D-003', 'low', 0.4, { learning: true }) }
    render(<DeviceList devices={devices} selected={null} onSelect={() => {}} />)
    const ids = screen.getAllByRole('button').map((b) => b.textContent!.slice(0, 5))
    expect(ids).toEqual(['D-002', 'D-003', 'D-001'])
    expect(screen.getByText(/learning/)).toBeTruthy()
  })
})

describe('AlertFeed', () => {
  const alerts = [alert('a', 'D-001', 'high'), alert('b', 'D-002', 'low', { status: 'closed', started: '2026-10-06T04:20:00+00:00' })]

  it('shows alerts with their leading reason and filters to open ones', () => {
    render(<AlertFeed alerts={alerts} feed={[]} selected={null} onSelect={() => {}} />)
    expect(screen.getAllByText('entered a restricted zone')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(screen.queryByText('D-002')).toBeNull()
    expect(screen.getByText('D-001')).toBeTruthy()
  })

  it('selects the device when an alert is clicked and can switch to the activity view', () => {
    const onSelect = vi.fn()
    render(<AlertFeed alerts={alerts} feed={[{ id: 'f1', ts: '2026-10-06T04:30:00+00:00', kind: 'severity', device_id: 'D-009', severity: 'medium', text: 'Rose from Normal to Medium (score 0.70)' }]} selected={null} onSelect={onSelect} />)
    fireEvent.click(screen.getByText('D-001'))
    expect(onSelect).toHaveBeenCalledWith('D-001')
    fireEvent.click(screen.getByRole('button', { name: 'Activity' }))
    expect(screen.getByText(/Rose from Normal to Medium/)).toBeTruthy()
  })

  it('explains the empty state', () => {
    render(<AlertFeed alerts={[]} feed={[]} selected={null} onSelect={() => {}} />)
    expect(screen.getByText(/No alerts yet/)).toBeTruthy()
  })
})

describe('DeviceDetail', () => {
  it('prompts for a selection when none is made', () => {
    render(<DeviceDetail snap={null} points={[]} alerts={[]} status={null} />)
    expect(screen.getByText(/Select a device/)).toBeTruthy()
  })

  it('shows why an open alert fired and states that the score is not a probability', () => {
    const s = snap('D-003', 'high', 0.99, { effective: { ...EFF, spatial: 1 }, contributions: { spatial: 0.89 } })
    const { container } = render(<DeviceDetail snap={s} points={[]} alerts={[alert('a', 'D-003', 'high')]} status={STATUS} />)
    expect(screen.getByText('Why it was flagged')).toBeTruthy()
    expect(screen.getByText('entered a restricted zone')).toBeTruthy()
    expect(screen.getByText(/not a probability/)).toBeTruthy()
    expect(screen.getByText(/contributes 0.90/)).toBeTruthy() // the alert's own factor list
    expect(container.querySelector('[title*="contributes 0.89"]')).toBeTruthy() // the live bar's tooltip
  })

  it('says when baselines are still learning', () => {
    render(<DeviceDetail snap={snap('D-003', 'normal', 0.05, { learning: true })} points={[]} alerts={[]} status={STATUS} />)
    expect(screen.getByText(/Still learning/)).toBeTruthy()
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

describe('DemoPanel', () => {
  const demo = (state: DemoStatus['state'], extra: Partial<DemoStatus> = {}): DemoStatus => ({ state, error: null, devices: 10, speed: 20, warmup_progress: 0, sim_time: null, active_episodes: [], ...extra })
  const fakeApi = (over: Record<string, unknown> = {}) => ({ demoStart: vi.fn().mockResolvedValue({}), demoStop: vi.fn(), demoInject: vi.fn().mockResolvedValue({}), demoClear: vi.fn().mockResolvedValue({ cleared: 1 }), ...over }) as unknown as Api

  it('hides every control from viewers', () => {
    render(<DemoPanel api={fakeApi()} isAdmin={false} demo={demo('idle')} selected="D-003" onChanged={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Start fleet' })).toBeNull()
    expect(screen.getByText(/Viewers can watch but not control/)).toBeTruthy()
  })

  it('shows warm-up progress while the baselines are being built', () => {
    render(<DemoPanel api={fakeApi()} isAdmin demo={demo('warming', { warmup_progress: 0.4 })} selected={null} onChanged={() => {}} />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('40')
  })

  it('injects the chosen scenario on the selected device and can restore it', async () => {
    const api = fakeApi()
    const onChanged = vi.fn()
    render(<DemoPanel api={api} isAdmin demo={demo('live')} selected="D-003" onChanged={onChanged} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enter restricted zone' }))
    await waitFor(() => expect(api.demoInject).toHaveBeenCalledWith({ device_id: 'D-003', scenario: 'spatial', magnitude: 0.8, duration_s: 240 }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore normal' }))
    await waitFor(() => expect(api.demoClear).toHaveBeenCalledWith('D-003'))
    expect(onChanged).toHaveBeenCalledTimes(2)
  })

  it('disables scenarios until a device is selected, and surfaces a server error', async () => {
    const api = fakeApi({ demoInject: vi.fn().mockRejectedValue(new ApiError(409, 'the demo is not live yet')) })
    const { rerender } = render(<DemoPanel api={api} isAdmin demo={demo('live')} selected={null} onChanged={() => {}} />)
    expect((screen.getByRole('button', { name: 'Combined' }) as HTMLButtonElement).disabled).toBe(true)
    rerender(<DemoPanel api={api} isAdmin demo={demo('live')} selected="D-001" onChanged={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Combined' }))
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
