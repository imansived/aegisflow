import { useState } from 'react'
import { siteDayTime, siteTime } from '../format'
import type { FeedItem } from '../live'
import type { Alert } from '../types'
import { SeverityBadge } from './Glyph'

type View = 'alerts' | 'activity'

export function AlertFeed({ alerts, feed, selected, onSelect }: {
  alerts: Alert[]; feed: FeedItem[]; selected: string | null; onSelect: (device: string) => void
}) {
  const [view, setView] = useState<View>('alerts')
  const [openOnly, setOpenOnly] = useState(false)
  const shown = openOnly ? alerts.filter((a) => a.status !== 'closed') : alerts

  return (
    <section className="card" aria-label="Alerts and activity">
      <header>
        <h2>{view === 'alerts' ? 'Alerts' : 'Live activity'}</h2>
        <div className="seg" role="group" aria-label="Feed view">
          <button aria-pressed={view === 'alerts'} onClick={() => setView('alerts')}>Alerts</button>
          <button aria-pressed={view === 'activity'} onClick={() => setView('activity')}>Activity</button>
        </div>
      </header>

      {view === 'alerts' ? (
        <>
          <div style={{ marginBottom: 8 }} className="seg" role="group" aria-label="Alert filter">
            <button aria-pressed={!openOnly} onClick={() => setOpenOnly(false)}>All</button>
            <button aria-pressed={openOnly} onClick={() => setOpenOnly(true)}>Open</button>
          </div>
          {shown.length === 0 ? (
            <div className="empty">{openOnly ? 'No open alerts.' : 'No alerts yet. Use the demo controls to inject a scenario.'}</div>
          ) : (
            <ul className="list scroll" aria-live="polite">
              {shown.map((a) => (
                <li key={a.alert_id}>
                  <button className="row" aria-pressed={selected === a.device_id} onClick={() => onSelect(a.device_id)}>
                    <span className="top">
                      <span><span className="id">{a.device_id}</span> <SeverityBadge severity={a.severity} /></span>
                      <span className="chip">{a.status === 'closed' ? 'Closed' : 'Open'}</span>
                    </span>
                    <span className="why">{a.factors[0]?.reasons[0] ?? 'Elevated behaviour across several signals'}</span>
                    <span className="meta">{siteDayTime(a.started)} · peak {a.peak_score.toFixed(2)} · {a.factors.length} signal{a.factors.length === 1 ? '' : 's'}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : feed.length === 0 ? (
        <div className="empty">Waiting for activity…</div>
      ) : (
        <ul className="list scroll" aria-live="polite">
          {feed.map((f) => (
            <li key={f.id}>
              <button className="row" aria-pressed={selected === f.device_id} onClick={() => onSelect(f.device_id)}>
                <span className="top"><span><span className="id">{f.device_id}</span> <SeverityBadge severity={f.severity} /></span><span className="meta">{siteTime(f.ts)}</span></span>
                <span className="why">{f.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
