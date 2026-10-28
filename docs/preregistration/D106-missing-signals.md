<!-- Pre-registration D106. This file is the freeze point: it is committed before any code for the experiment and before any test seed (300-309) is scored. The manual (kept out of git) holds the same text as section 5.11. -->

### 5.11 Experiment D106 (fresh test seeds 300–309): does AegisFlow degrade gracefully when signals go missing? — PRE-REGISTERED

**Status: pre-registered before any code for it exists and before any of its test seeds is scored.** This section is the
protocol; the commit that adds it is the freeze point. The question comes from §5.10, which found that setting a missing
signal to zero (what a dead sensor gives the current system) is not graceful: with the network signal gone, learned fusion's
recall on weak and staged episodes fell from 0.92 to 0.27. Guardrail adopted from the proposal: **a missing signal is never
silently read as a zero anomaly score unless the method explicitly justifies that**; missing data is not evidence of normal
behaviour.

**Objective.** Not a higher F1. It is: *detect anomalies reliably when inputs are imperfect, without an unusable volume of
false alarms.* So the false-alert budget is part of the design, not a footnote.

**Arms** (all use the frozen per-modality detectors and the frozen soft scores and 1 s half-life; only the fusion layer differs):
- **A, frozen control.** The current learned late fusion, unchanged, fed 0 for an unavailable signal (what the live code does
  today). Reported twice: **A-F1** with its F1 threshold, and **A-budget** with the same model re-thresholded once, on complete
  signals, to the alert budget below. A-budget is the control every decision is made against, because it is the stronger one.
- **B, missingness-aware learned fusion.** One learned fusion per availability pattern (15 non-empty subsets of the four
  signals), each fitted on the training seeds using only the signals present in its pattern (the absent columns are held at a
  constant and carry no weight; the model never learns that "zero" is normal). Each pattern has its **own threshold**
  calibrated to the alert budget. When no signal is available the system produces no score and no alert: that time is counted
  as **blind time** and reported, not treated as normal.
- **C, label-free rules with a documented missing-input rule.** Weighted sum renormalised over the available weights
  (sum of w·x over available divided by the sum of those w); noisy-OR over the available signals (missing contributes no
  evidence, which is the justified reading for combining independent evidence, **together with a per-pattern threshold** so
  that fewer signals does not silently mean fewer alerts). Same blind-time rule as B.
- Reference arms, reported but never used for a decision: network-only, max, Isolation Forest.

**Alert budget (primary operating point).** Every threshold (A-budget, B per pattern, C per pattern) is set on **ordinary time
of the threshold seeds** so that false alert onsets equal **3.0 per device per day** (the F1 systems ran 2.2–7.2; 3.0 sits in
the middle and is still too high for production, which §5.10 says plainly). Benign look-alike episodes are in every
evaluation and every flagged one counts as a false alert in precision and F1, so a robustness gain cannot hide as a more
trigger-happy detector.

**Training and calibration data: validation seeds 10–19 only.** Fit on 10–15, thresholds on 16–19, exactly as F1 stage 1. No
new parameter is fitted on anything else. **Test: seeds 300–309**, the original generator, 20 devices, 72 simulated hours,
never used for anything. A dry run on validation seeds (fit 10–15, thresholds 16–17, evaluate 18–19) checks the pipeline and may
fix bugs but **may not change a design choice or a decision rule below**; any code change after this commit is listed in the
results with its reason. The test run is made once, refuses to run twice (guard file), and its numbers are reported whatever
they are.

**Missingness conditions** (masks are applied to the detector outputs after the fact, per signal and tick, from a seeded
random generator, so every arm sees the same masks; a limit noted below):
1. **Complete** (control).
2. **Single signal missing, sustained** (4 conditions: the signal is unavailable for the whole run).
3. **Two signals missing, sustained** (6 conditions: every pair).
4. **Intermittent**: every signal independently unavailable in bursts, 20% of ticks, mean burst 12 ticks (one minute).
5. **Anomaly-correlated**: for each episode and each signal the episode touches, with probability 0.6 that signal is
   unavailable for the whole episode (an attacker blocking or jamming it), plus 2% background bursts. Arm B and C thresholds are
   calibrated without ever seeing this mechanism; arm B is not trained on any mask.

**Measurements, per arm and condition, per seed (10 seeds):** event-level recall on malicious episodes (headline = weak +
staged; also all malicious and per scenario), false alerts per device-day on ordinary time, precision and F1 as defined in §5.10,
benign look-alike flag rate, blind-time fraction, degradation = recall under the condition minus the same arm's recall on
complete signals, and the spread across seeds (standard deviation, minimum, maximum). Paired differences use seed-level 95%
t-intervals (seeds, not episodes, are the independent units).

**Decision rules (fixed now).**
- **Adopt B** (missingness-aware learned fusion) over A only if, over the macro average of conditions 2–5 (4 + 6 + 1 + 1 = 12
  conditions, each weighted equally): (i) B's mean degradation in headline recall is smaller than A-budget's by at least 0.05,
  the seed-level interval of the paired difference excluding zero; **and** (ii) in every one of the 12 conditions B's false
  alerts are at most 3.75 per device-day (1.25 × the budget); **and** (iii) B's benign look-alike flag rate is not more than
  0.05 above A-budget's; **and** (iv) on complete signals B's headline recall is no more than 0.03 below A-budget's.
- **Prefer C over B** if C satisfies (ii)–(iv) and its mean degradation is within 0.03 of B's: the simpler method wins ties. Say
  so even if C is also better than A.
- **Otherwise keep A unchanged** and record that no tested fix justified a change. A null result is a legitimate result.
- Nothing is adopted because it raised F1. F1 is reported, not decided on.
- The anomaly-correlated condition is a stress test of whether a fix is exploitable: if B or C is *worse* than A-budget there by
  more than 0.05 recall, say so prominently whatever else holds.

**Known limits, stated before the run.** Masks are applied to detector outputs, not by removing fields from simulated
telemetry, so detector internal state (baselines, endpoint familiarity) is not disturbed by the loss; the 1 s half-life makes the
time window inactive, so no stale-evidence rule is needed or tested; one generator, one fleet size; masks I invented, not
measured failure statistics; no real-data component (TON_IoT has no missing-signal structure worth testing). The learned
arms inherit the simulator's reliance on the network signal (§5.10). Thresholds are calibrated on pooled ordinary rows, not
per device.
