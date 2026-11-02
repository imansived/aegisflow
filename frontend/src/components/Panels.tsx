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
/** How to read the score: what it is, what the labels mean, and when an alert opens and closes. Bands follow the alert thresholds (fully observed device). */
export function ScoreGuide({ status }: { status?: Status | null }) {
  const raise = status?.alert_thresholds.raise_at
  const clear = status?.alert_thresholds.clear_at
  return (
    <details className="score-guide">
      <summary>How to read this score</summary>
      <ul>
        <li><b>The score</b> is this device's fused risk right now, recomputed with every 5-second update. Each signal's own score is held and fades with a one-second half-life, so a brief spike is not missed. It is a ranking on a learned, saturating scale, not a probability: 0.75 is not "three quarters of the way" to an alert.</li>
        {raise !== undefined && clear !== undefined && (
          <li><b>Labels</b> follow the alert thresholds: Low from {(0.5 * clear).toFixed(2)}, Medium from {clear.toFixed(2)} (the clear level), High from {raise.toFixed(2)} (the alert level). They shift slightly when a device is partly observed.</li>
        )}
        <li><b>An alert opens</b> when the fused score reaches the alert level and the device has finished learning its normal. <b>It closes</b> only after the score has stayed below the clear level for 60 seconds, so a dip does not end it and a device can fall from High to Normal while the alert is still open.</li>
        <li><b>Degraded</b> means some signals cannot be seen; a quiet score then says nothing about them.</li>
      </ul>
    </details>
  )
}

export function correlationSentence(snap: Snapshot, alertLevel?: number): string {
  const lost = lostSignals(snap)
  const seen = MODALITIES.filter((m) => !lost.includes(m))
  const active = seen.filter((m) => snap.effective[m] >= ACTIVE_LEVEL).sort((a, b) => snap.effective[b] - snap.effective[a])
  const strongest = Math.max(0, ...seen.map((m) => snap.effective[m]))
  // The fused score is learned and saturating: weak signals that are each below the active level can still add up to a non-normal band.
  // Say so, instead of calling the device normal while its risk label is not.
  const band = snap.severity !== 'normal'
    ? `the fused risk is ${snap.score.toFixed(2)}: ${SEVERITY_LABEL[snap.severity]} band${alertLevel !== undefined ? `, alert level ${alertLevel.toFixed(2)}` : ''}.`
    : ''
  // A missing signal is never reported as a normal one: say what cannot be seen.
  const blind = lost.length ? `Not fully observed: ${lostText(snap)} ${(snap.recovering ?? []).length === lost.length ? 'recovering (judgment withheld while its window refills)' : 'unavailable'}. ` : ''
  if (seen.length === 0) return 'No signal is available: this device cannot be judged.'
  if (active.length === 0) {
    return lost.length
      ? `${blind}The ${seen.length} signal${seen.length === 1 ? '' : 's'} that can be seen look ordinary; that says nothing about the ones that cannot.${band ? ` Together ${band}` : ''}`
      : band ? `No single signal is active (${ACTIVE_LEVEL.toFixed(2)}+), yet together ${band}` : 'All four signals are within their normal range.'
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
                    <span className="acard-why">{lostText(d)} {(d.recovering ?? []).length ? 'recovering (window refilling).' : 'unavailable.'} {d.severity === 'normal' ? 'A quiet score here does not mean healthy.' : 'Alerts for this device rest on fewer signals.'}</span>
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
                  <span className="a-body"><b>{f.device_id}</b> <span className="a-text">{f.text}</span></span>
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

/** Where the fleet stands: flagged (any non-normal risk band) first, then degraded (some signal cannot be seen), else normal. Each device counts once. */
export function fleetCounts(devices: Record<string, Snapshot>): { normal: number; degraded: number; flagged: number; total: number } {
  const all = Object.values(devices)
  const flagged = all.filter((d) => d.severity !== 'normal').length
  const degraded = all.filter((d) => d.severity === 'normal' && isDegraded(d)).length
  return { normal: all.length - flagged - degraded, degraded, flagged, total: all.length }
}

function FleetRing({ devices }: { devices: Record<string, Snapshot> }) {
  const c = fleetCounts(devices)
  if (!c.total) return null
  const R = 21
  const L = 2 * Math.PI * R
  const parts = [
    { key: 'normal', label: 'Normal', n: c.normal, color: 'var(--good)' },
    { key: 'degraded', label: 'Degraded', n: c.degraded, color: 'var(--degraded)' },
    { key: 'flagged', label: 'Flagged', n: c.flagged, color: 'var(--sev-medium)' },
  ]
  let off = 0
  return (
    <div className="fleet" role="group" aria-label={`Fleet: ${c.normal} normal, ${c.degraded} degraded, ${c.flagged} flagged, of ${c.total} devices`}>
      <svg viewBox="0 0 56 56" width="56" height="56" aria-hidden="true">
        <circle cx="28" cy="28" r={R} className="fleet-track" />
        {parts.filter((x) => x.n).map((x) => {
          const len = (x.n / c.total) * L
          const el = <circle key={x.key} cx="28" cy="28" r={R} fill="none" stroke={x.color} strokeWidth="5" strokeDasharray={`${Math.max(len - 1.5, 0.5)} ${L}`} strokeDashoffset={-off} transform="rotate(-90 28 28)" />
          off += len
          return el
        })}
        <text x="28" y="32" textAnchor="middle" className="fleet-n">{c.normal}/{c.total}</text>
      </svg>
      <ul>
        {parts.map((x) => <li key={x.key}><i style={{ background: x.color }} />{x.label}<b>{x.n}</b></li>)}
      </ul>
    </div>
  )
}

export function HealthPanel({ snap, history, devices = {} }: { snap: Snapshot | null; history: Record<string, Point[]>; devices?: Record<string, Snapshot> }) {
  const p = pts(history, snap?.device_id ?? null)
  return (
    <section className="panel" aria-label="Device health">
      <header className="panel-head"><h2>Device health</h2><span className="sub">{snap ? snap.device_id : 'no device'}</span></header>
      <FleetRing devices={devices} />
      <MultiLine times={p.map((q) => q.t)} ariaLabel={`Battery and CPU of ${snap?.device_id}`} unit="%" max={100} height={112}
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
          <p className="corr-note">{correlationSentence(snap, status?.alert_thresholds.raise_at)}</p>
        </div>
        <div className="corr-main">
          <CorrelationLanes
            times={p.map((q) => q.t)}
            lanes={MODALITIES.map((m) => ({ key: m, label: MODALITY_LABEL[m], color: SIGNAL_COLOR[m], values: p.map((q) => ((q.missing >> MODALITIES.indexOf(m)) & 1 ? NaN : q.effective[m])), now: snap.effective[m],
              lost: lostSignals(snap).includes(m), recovering: (snap.recovering ?? []).includes(m), gaps: p.map((q) => ((q.missing >> MODALITIES.indexOf(m)) & 1) === 1) }))}
            fused={p.map((q) => q.score)} fusedNow={snap.score} activeLevel={ACTIVE_LEVEL} alertLevel={status?.alert_thresholds.raise_at} />
        </div>
        <div className="corr-why" aria-label="Why it was flagged">
          <h3>{alert && alert.status !== 'closed' ? 'Why it was flagged' : n >= 2 ? 'Why the risk is rising' : 'Signal detail'}</h3>
          <ScoreGuide status={status} />
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
                <li key={m}><span className={`sigchip lost m-${m}`}><i />{MODALITY_LABEL[m]}</span><span className="reason">{(snap.recovering ?? []).includes(m) ? 'recovering: the signal is back but its window is refilling, so judgment is withheld' : 'unavailable: not observed, not normal'}</span></li>
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
