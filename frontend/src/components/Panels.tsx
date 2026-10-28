import { useMemo } from 'react'
import { MODALITY_LABEL, SEVERITY_LABEL, isDegraded, lostSignals, lostText, siteDayTime, siteTime } from '../format'
import type { FeedItem, Point } from '../live'
import { MODALITIES, type Alert, type Snapshot, type Status } from '../types'
import { DegradedBadge, SeverityBadge, StateBadge } from './Glyph'
import { CorrelationLanes, MultiLine } from './MiniCharts'
import { ACTIVE_LEVEL, SIGNAL_COLOR, SignalRing, activeSignals } from './SignalRing'

/** The riskiest device, used when nothing has been selected so the bottom panels are never empty. */
export function focusDevice(devices: Record<string, Snapshot>, selected: string | null): Snapshot | null {
  if (selected && devices[selected]) return devices[selected]
  return Object.values(devices).sort((a, b) => b.score - a.score)[0] ?? null
}

/** Plain-language account of how the signals combine right now. Pure, so it can be tested. */
export function correlationSentence(snap: Snapshot): string {
  const lost = lostSignals(snap)
  const seen = MODALITIES.filter((m) => !lost.includes(m))
  const active = seen.filter((m) => snap.effective[m] >= ACTIVE_LEVEL).sort((a, b) => snap.effective[b] - snap.effective[a])
  const strongest = Math.max(0, ...seen.map((m) => snap.effective[m]))
  // A missing signal is never reported as a normal one: say what cannot be seen.
  const blind = lost.length ? `Not fully observed: ${lostText(snap)} unavailable. ` : ''
  if (seen.length === 0) return 'No signal is available: this device cannot be judged.'
  if (active.length === 0) {
    return lost.length
      ? `${blind}The ${seen.length} signal${seen.length === 1 ? '' : 's'} that can be seen look ordinary; that says nothing about the ones that cannot.`
      : 'All four signals are within their normal range.'
  }
  const list = active.map((m) => `${MODALITY_LABEL[m]} ${snap.effective[m].toFixed(2)}`).join(', ')
  if (active.length === 1) return `${blind}One signal is active: ${list}.`
  return `${blind}${active.length} signals are active at once: ${list}. The strongest alone is ${strongest.toFixed(2)}; combined they score ${snap.score.toFixed(2)}.`
}

// ---------------------------------------------------------------------------------------- live anomaly feed

export function AnomalyFeed({ alerts, feed, selected, onSelect, onOpenAll, devices = {} }: {
  alerts: Alert[]; feed: FeedItem[]; selected: string | null; onSelect: (id: string) => void; onOpenAll: () => void; devices?: Record<string, Snapshot>
}) {
  const open = alerts.filter((a) => a.status !== 'closed')
  const blind = Object.values(devices).filter(isDegraded).sort((a, b) => a.device_id.localeCompare(b.device_id))
  return (
    <section className="panel feed" aria-label="Live anomalies">
      <header className="panel-head">
        <h2>Live anomalies</h2>
        <button className="link" onClick={onOpenAll}>{open.length ? `${open.length} open · view all` : 'view all'}</button>
      </header>
      <div className="feed-scroll">
        {open.length > 0 && (
          <ul className="alert-cards" aria-label="Open alerts">
            {open.slice(0, 5).map((a) => (
              <li key={a.alert_id}>
                <button className={`acard sev-${a.severity}`} aria-pressed={selected === a.device_id} onClick={() => onSelect(a.device_id)}>
                  <span className="acard-top"><b>{a.device_id}</b><SeverityBadge severity={a.severity} /><span className="meta">{siteTime(a.started)}</span></span>
                  <span className="acard-why">{a.factors[0]?.reasons[0] ?? 'Elevated behaviour across several signals'}</span>
                  <span className="acard-sig">
                    {a.factors.map((f) => <span key={f.modality} className={`sigchip m-${f.modality}`}><i />{MODALITY_LABEL[f.modality]}</span>)}
                    <span className="meta">peak {a.peak_score.toFixed(2)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {blind.length > 0 && (
          <>
            <h3>Blind spots</h3>
            <ul className="alert-cards" aria-label="Devices that are not fully observed">
              {blind.map((d) => (
                <li key={d.device_id}>
                  <button className="acard degraded" aria-pressed={selected === d.device_id} onClick={() => onSelect(d.device_id)}>
                    <span className="acard-top"><b>{d.device_id}</b><StateBadge snap={d} /></span>
                    <span className="acard-why">{lostText(d)} unavailable. {d.severity === 'normal' ? 'A quiet score here does not mean healthy.' : 'Alerts for this device rest on fewer signals.'}</span>
                    {d.fallback && <span className="meta">{d.fallback}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        <h3>Activity</h3>
        {feed.length === 0 ? (
          <div className="empty">Listening. Changes in risk appear here as they happen.</div>
        ) : (
          <ol className="activity" aria-live="polite" aria-label="Activity">
            {feed.slice(0, 40).map((f) => (
              <li key={f.id}>
                <button className={`sev-${f.severity}`} onClick={() => onSelect(f.device_id)} aria-pressed={selected === f.device_id}>
                  {f.kind === 'observability' ? <DegradedBadge label={false} /> : <SeverityBadge severity={f.severity} label={false} />}
                  <span className="a-body"><b>{f.device_id}</b> {f.text}</span>
                  <span className="meta">{siteTime(f.ts)}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------------------- bottom panels

const pts = (history: Record<string, Point[]>, id: string | null): Point[] => (id ? history[id] ?? [] : [])

export function TrafficPanel({ snap, history }: { snap: Snapshot | null; history: Record<string, Point[]> }) {
  const p = pts(history, snap?.device_id ?? null)
  const times = p.map((q) => q.t)
  const kb = (v: number) => (Number.isFinite(v) ? v / 1024 : NaN)
  const haveBytes = p.some((q) => Number.isFinite(q.sent))
  return (
    <section className="panel" aria-label="Network traffic">
      <header className="panel-head"><h2>Network traffic</h2><span className="sub">{snap ? snap.device_id : 'no device'}</span></header>
      {haveBytes ? (
        <MultiLine times={times} ariaLabel={`Bytes sent and received by ${snap?.device_id}`} unit=" KB" height={150}
          series={[
            { key: 'sent', label: 'Sent', color: 'var(--sig-network)', values: p.map((q) => kb(q.sent)) },
            { key: 'recv', label: 'Received', color: 'var(--accent)', values: p.map((q) => kb(q.recv)) },
          ]} />
      ) : (
        <MultiLine times={times} ariaLabel={`Open connections of ${snap?.device_id}`} height={150}
          series={[{ key: 'conns', label: 'Open connections', color: 'var(--sig-network)', values: p.map((q) => q.conns) }]} />
      )}
      {snap && (snap.network_connections === null
        ? <p className="mini-foot lost">Network signal unavailable: no traffic is being reported. This is a blind spot, not silence.</p>
        : <p className="mini-foot">{snap.network_connections} open connections now</p>)}
    </section>
  )
}

export function HealthPanel({ snap, history }: { snap: Snapshot | null; history: Record<string, Point[]> }) {
  const p = pts(history, snap?.device_id ?? null)
  return (
    <section className="panel" aria-label="Device health">
      <header className="panel-head"><h2>Device health</h2><span className="sub">{snap ? snap.device_id : 'no device'}</span></header>
      <MultiLine times={p.map((q) => q.t)} ariaLabel={`Battery and CPU of ${snap?.device_id}`} unit="%" max={100} height={150}
        series={[
          { key: 'cpu', label: 'CPU', color: 'var(--sig-device)', values: p.map((q) => q.cpu) },
          { key: 'battery', label: 'Battery', color: 'var(--accent)', values: p.map((q) => q.battery) },
        ]} />
      {snap && (snap.temperature === null
        ? <p className="mini-foot lost">Device-health signal unavailable. This is a blind spot, not a healthy device.</p>
        : <p className="mini-foot">{snap.temperature.toFixed(0)} °C{snap.memory_usage != null ? ` · memory ${snap.memory_usage.toFixed(0)}%` : ''}</p>)}
    </section>
  )
}

export function CorrelationPanel({ snap, history, alerts, status }: {
  snap: Snapshot | null; history: Record<string, Point[]>; alerts: Alert[]; status: Status | null
}) {
  const p = pts(history, snap?.device_id ?? null)
  const alert = useMemo(() => (snap ? alerts.filter((a) => a.device_id === snap.device_id).sort((a, b) => Number(b.status !== 'closed') - Number(a.status !== 'closed'))[0] : undefined), [alerts, snap])
  if (!snap) {
    return (
      <section className="panel correlation" aria-label="Cross-modal correlation">
        <header className="panel-head"><h2>Cross-modal correlation</h2></header>
        <div className="empty">Start the fleet in Scenarios, then select a device. This panel shows how weak signals combine into one risk.</div>
      </section>
    )
  }
  const n = activeSignals(snap.effective)
  return (
    <section className="panel correlation" aria-label="Cross-modal correlation">
      <header className="panel-head">
        <h2>Cross-modal correlation</h2>
        <span className="sub"><b className="who">{snap.device_id}</b> · {snap.zone ?? 'in transit'} · <StateBadge snap={snap} />{isDegraded(snap) && snap.severity !== 'normal' && <> · <DegradedBadge lost={lostText(snap)} /></>}</span>
      </header>
      <div className="corr-body">
        <div className="corr-ring">
          <SignalRing effective={snap.effective} score={snap.score} severity={snap.severity} size={132} lost={lostSignals(snap)} />
          <p className="corr-note">{correlationSentence(snap)}</p>
        </div>
        <div className="corr-main">
          <CorrelationLanes
            times={p.map((q) => q.t)}
            lanes={MODALITIES.map((m) => ({ key: m, label: MODALITY_LABEL[m], color: SIGNAL_COLOR[m], values: p.map((q) => ((q.missing >> MODALITIES.indexOf(m)) & 1 ? NaN : q.effective[m])), now: snap.effective[m],
              lost: lostSignals(snap).includes(m), gaps: p.map((q) => ((q.missing >> MODALITIES.indexOf(m)) & 1) === 1) }))}
            fused={p.map((q) => q.score)} fusedNow={snap.score} activeLevel={ACTIVE_LEVEL} alertLevel={status?.alert_thresholds.raise_at} />
        </div>
        <div className="corr-why" aria-label="Why it was flagged">
          <h3>{alert && alert.status !== 'closed' ? 'Why it was flagged' : n >= 2 ? 'Why the risk is rising' : 'Signal detail'}</h3>
          {alert ? (
            <ul>
              {alert.factors.map((f) => (
                <li key={f.modality}>
                  <span className={`sigchip m-${f.modality}`}><i />{MODALITY_LABEL[f.modality]}</span>
                  <span className="reason">{f.reasons[0] ?? 'elevated'}</span>
                  <span className="meta">+{f.contribution.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <ul>
              {MODALITIES.filter((m) => snap.effective[m] >= ACTIVE_LEVEL).map((m) => (
                <li key={m}><span className={`sigchip m-${m}`}><i />{MODALITY_LABEL[m]}</span><span className="reason">{snap.effective[m].toFixed(2)} of 1.00</span>{snap.contributions[m] !== undefined && <span className="meta">+{snap.contributions[m]!.toFixed(2)}</span>}</li>
              ))}
              {n === 0 && <li className="reason">{isDegraded(snap) ? 'Nothing unusual among the signals that can be seen.' : 'Nothing unusual.'}</li>}
            </ul>
          )}
          {lostSignals(snap).length > 0 && (
            <ul className="blind-list" aria-label="Unavailable signals">
              {lostSignals(snap).map((m) => (
                <li key={m}><span className={`sigchip lost m-${m}`}><i />{MODALITY_LABEL[m]}</span><span className="reason">unavailable: not observed, not normal</span></li>
              ))}
              {snap.fallback && <li><span className="reason">{snap.fallback}: no alert can be raised for this combination.</span></li>}
            </ul>
          )}
          <p className="fine">The fused score ranks how unusual this is across all signals. It is not a probability that an attack occurred.</p>
          {alert && <p className="fine">{SEVERITY_LABEL[alert.severity]} · {siteDayTime(alert.started)} · peak {alert.peak_score.toFixed(2)}</p>}
        </div>
      </div>
    </section>
  )
}
