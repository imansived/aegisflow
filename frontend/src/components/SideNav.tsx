export type View = 'overview' | 'network' | 'anomalies' | 'scenarios' | 'stats' | 'evidence'

const ICONS: Record<View, string> = {
  overview: 'M3 3h7v7H3zM14 3h7v4h-7zM14 10h7v11h-7zM3 13h7v8H3z',
  network: 'M12 3v4M12 17v4M3 12h4M17 12h4M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8',
  anomalies: 'M12 3 22 20H2zM12 10v5M12 17.5v.5',
  scenarios: 'M8 5v14l11-7z',
  stats: 'M4 20V10M10 20V4M16 20v-8M22 20H2',
  evidence: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7',
}
const MAIN: [View, string][] = [['overview', 'Overview'], ['network', 'Device Network'], ['anomalies', 'Anomalies'], ['scenarios', 'Scenarios']]
const MORE: [View, string][] = [['stats', 'Statistics'], ['evidence', 'Evidence']]

export function SideNav({ view, onView, openAlerts }: { view: View; onView: (v: View) => void; openAlerts: number }) {
  const item = ([v, label]: [View, string]) => (
    <li key={v}>
      <button aria-current={view === v ? 'page' : undefined} onClick={() => onView(v)}>
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d={ICONS[v]} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" /></svg>
        <span>{label}</span>
        {v === 'anomalies' && openAlerts > 0 && <i className="count" aria-label={`${openAlerts} open`}>{openAlerts}</i>}
      </button>
    </li>
  )
  return (
    <nav className="side" aria-label="Sections">
      <ul>{MAIN.map(item)}</ul>
      <p className="side-label">Reports</p>
      <ul>{MORE.map(item)}</ul>
    </nav>
  )
}
