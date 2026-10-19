import { IntervalPlot } from '../components/Charts'
import evidence from '../data/evidence.json'

const VERDICT = (lo: number, hi: number) => (lo > 0 ? 'Clear gain' : hi < 0 ? 'Worse' : 'No difference shown')

/** The research result behind the product, from the saved final-evaluation file: nothing here is typed in by hand. */
export default function Evidence() {
  return (
    <div className="page">
      <div className="callout">
        <b>What the final test showed.</b> Simulated devices only: this says nothing about real fleets or attacks the simulator cannot
        generate. The comparison was pre-registered and run once on {evidence.episodes.toLocaleString()} unseen episodes (code frozen at commit{' '}
        <code>{evidence.frozen_commit}</code>). Every system was held to the same false-alarm budget.
      </div>

      <section className="card">
        <header><h2>Pre-registered comparisons</h2><span className="sub">difference in recall on weak and staged episodes, with 95% interval</span></header>
        <IntervalPlot
          ariaLabel="Differences in recall with 95 percent intervals"
          rows={evidence.primary.map((p) => ({ label: p.label.replace(/^P\d /, ''), diff: p.diff, lo: p.lo, hi: p.hi }))}
        />
        <table className="data" style={{ marginTop: 10 }}>
          <thead><tr><th>Comparison</th><th>Difference</th><th>95% interval</th><th>Reading</th></tr></thead>
          <tbody>
            {evidence.primary.map((p) => (
              <tr key={p.label}>
                <td>{p.label}</td>
                <td>{p.diff > 0 ? '+' : ''}{p.diff.toFixed(3)}</td>
                <td>[{p.lo.toFixed(3)}, {p.hi.toFixed(3)}]</td>
                <td>{VERDICT(p.lo, p.hi)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="notice" style={{ marginTop: 10 }}>
          Where the interval crosses zero, no difference is claimed. Fusing signals clearly beats independent detection, but is not shown to
          beat the single best detector, and a supervised joint model does better than late fusion.
        </p>
      </section>

      <section className="card">
        <header><h2>Systems compared</h2><span className="sub">share of episodes caught at a 0.1% false-alarm budget</span></header>
        <table className="data">
          <thead><tr><th>System</th><th>Macro recall</th><th>Weak</th><th>Staged</th><th>Hardware</th><th>Location</th><th>Proximity</th><th>Avg. precision</th><th>Alert onsets / device / day</th></tr></thead>
          <tbody>
            {evidence.systems.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td><td>{s.macro.toFixed(2)}</td><td>{s.weak.toFixed(2)}</td><td>{s.staged.toFixed(2)}</td>
                <td>{s.hardware.toFixed(2)}</td><td>{s.spatial.toFixed(2)}</td><td>{s.proximity.toFixed(2)}</td><td>{s.ap.toFixed(2)}</td><td>{s.onsets.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="sub" style={{ marginTop: 8 }}>
          Macro recall averages seven scenario types. Alert onsets are raw, before incident merging. Full method, limits and the corrections made
          along the way are in <code>docs/AegisFlow-Technical-Manual.md</code> §5.7.
        </p>
      </section>
    </div>
  )
}
