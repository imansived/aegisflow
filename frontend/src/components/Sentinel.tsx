import { useState } from 'react'
import { siteDayTime, siteTime, sevRank } from '../format'
import type { FeedItem } from '../live'
import type { Alert, Snapshot } from '../types'
import { SeverityBadge } from './Glyph'

type View = 'log' | 'alerts' | 'roster'

/** The left rail. "Log" narrates what is happening in plain language, newest first; "Alerts" is the stored alert
 *  list; "Roster" is every device, riskiest first. Selecting anything focuses that device in the scene. */
export function Sentinel({ alerts, feed, devices, selected, onSelect }: {
  alerts: Alert[]; feed: FeedItem[]; devices: Record<string, Snapshot>; selected: string | null; onSelect: (id: string) => void
}) {
  const [view, setView] = useState<View>('log')
  const [openOnly, setOpenOnly] = useState(false)
  const roster = Object.values(devices).sort((a, b) => sevRank(b.severity) - sevRank(a.severity) || b.score - a.score || a.device_id.localeCompare(b.device_id))
  const shown = openOnly ? alerts.filter((a) => a.status !== 'closed') : alerts
  const openCount = alerts.filter((a) => a.status !== 'closed').length

  return (
    <section className="rail sentinel" aria-label="Sentinel">
      <header className="rail-head">
        <h2><span className="pip" aria-hidden="true" />SENTINEL</h2>
        <div className="seg" role="tablist" aria-label="Sentinel view">
          <button role="tab" aria-selected={view === 'log'} onClick={() => setView('log')}>Log</button>
          <button role="tab" aria-selected={view === 'alerts'} onClick={() => setView('alerts')}>Alerts{openCount ? <i className="count">{openCount}</i> : null}</button>
          <button role="tab" aria-selected={view === 'roster'} onClick={() => setView('roster')}>Roster</button>
        </div>
      </header>

      {view === 'log' && (feed.length === 0 ? (
        <div className="empty">Listening. Narration appears here as things happen.</div>
      ) : (
        <ol className="log" aria-live="polite" aria-label="Event log">
          {feed.map((f) => (
            <li key={f.id} className={`sev-${f.severity} ${f.kind}`}>
              <button onClick={() => onSelect(f.device_id)} aria-pressed={selected === f.device_id}>
                <span className="t">T+{siteTime(f.ts)}</span>
                <span className="who"><SeverityBadge severity={f.severity} label={false} /> {f.device_id}</span>
                <span className="what">{f.text}</span>
              </button>
            </li>
          ))}
        </ol>
      ))}

      {view === 'alerts' && (
        <>
          <div className="seg small" role="group" aria-label="Alert filter">
            <button aria-pressed={!openOnly} onClick={() => setOpenOnly(false)}>All</button>
            <button aria-pressed={openOnly} onClick={() => setOpenOnly(true)}>Open</button>
          </div>
          {shown.length === 0 ? (
            <div className="empty">{openOnly ? 'No open alerts.' : 'No alerts yet. Use the Simulation Deck to inject a scenario.'}</div>
          ) : (
            <ul className="cards" aria-live="polite">
              {shown.map((a) => (
                <li key={a.alert_id}>
                  <button className={`card-btn sev-${a.severity}`} aria-pressed={selected === a.device_id} onClick={() => onSelect(a.device_id)}>
                    <span className="top"><b>{a.device_id}</b> <SeverityBadge severity={a.severity} /><span className={`state ${a.status === 'closed' ? '' : 'open'}`}>{a.status === 'closed' ? 'Closed' : 'Open'}</span></span>
                    <span className="why">{a.factors[0]?.reasons[0] ?? 'Elevated behaviour across several signals'}</span>
                    <span className="meta">{siteDayTime(a.started)} · peak {a.peak_score.toFixed(2)} · {a.factors.length} signal{a.factors.length === 1 ? '' : 's'}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {view === 'roster' && (roster.length === 0 ? (
        <div className="empty">No devices yet. Start the fleet to bring the facility online.</div>
      ) : (
        <ul className="cards roster">
          {roster.map((d) => (
            <li key={d.device_id}>
              <button className={`card-btn sev-${d.severity}`} aria-pressed={selected === d.device_id} onClick={() => onSelect(d.device_id)}>
                <span className="top"><b>{d.device_id}</b><span className="meta">{d.zone ?? 'in transit'}{d.learning ? ' · learning' : ''}</span><SeverityBadge severity={d.severity} /></span>
                <span className="bar" aria-label={`risk score ${d.score.toFixed(2)}`}><i style={{ width: `${Math.round(d.score * 100)}%` }} /></span>
              </button>
            </li>
          ))}
        </ul>
      ))}
    </section>
  )
}
