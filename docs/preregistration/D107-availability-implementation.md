<!-- Pre-registration D107. This file is the freeze point: committed before any D107 code and before any test seed (400-409) is built or scored. D106's test seeds (300-309) are spent and are NOT reused. -->

# D107 — independent validation of the shipped availability handling (fresh seeds 400–409)

**What is being validated.** The implementation, not an idea. Commit `0b3592f` ships: telemetry that marks a signal group
unavailable with explicit nulls (`contracts/telemetry.py`); detectors that score nothing and learn nothing from an unavailable
signal (`backend/detection/engine.py`); the **unchanged** learned fusion (`models/frozen_design.json`) with a missing signal's
column held at 0 and a **validated threshold per availability pattern** (`models/availability_thresholds.json`, calibrated on
validation seeds 16–19 only, alert budget 3.0 false onsets per device-day); a **conservative fallback** (no anomaly alert,
reported as Degraded with the reason) for any pattern without a validated threshold and for "nothing available"; and a dashboard
that shows **Normal / Anomalous / Degraded** and never calls a partly observed device Normal. Call the shipped alerting arm **S**.

**Why this is separate from D106.** D106 tested ideas on detector outputs masked after the fact (seeds 300–309, spent). Its
post-hoc ablation suggested that thresholds alone capture most of the gain; S is that ablation, implemented. D107 uses new seeds
and the real code path, and does not alter any D106 number.

## Arms (all share the frozen detectors, soft scores and 1 s half-life)
- **A-budget, the control:** the same model with a missing signal read as 0 and ONE threshold, calibrated on complete signals to the same
  budget. A-F1 (F1's threshold) is reported for reference.
- **S, the shipped implementation:** model + per-pattern validated thresholds + fallback, exactly as exported (not refitted, not retuned).
- **B, the D106 arm:** one learned fusion per availability pattern, refitted on validation seeds 10–15 with D106's code, thresholds on 16–19.
  A comparison point only.

## Data and conditions
Seeds **400–409**, original generator, 20 devices, 72 simulated hours (the same settings as F1 and D106). The 13 conditions are D106's
(complete; 4 single signals; 6 pairs; intermittent loss; anomaly-correlated loss) with the same mask definitions, but applied to
**telemetry** as explicit nulls and run through the real detection engine and pipeline, not to detector outputs:
- *Sustained* conditions: detectors are independent per signal, so a sustained loss leaves every other signal's detector output
  identical to the complete run's. For seeds 402–409 these 11 conditions are scored from the complete run with the lost
  signals' columns removed; for seeds **400 and 401 all 13 conditions are also run end to end through the product pipeline** to
  test that equivalence and the implementation's conformance (R5).
- *Intermittent* and *anomaly-correlated* conditions change detector state (gaps), so they are run end to end through the product
  pipeline for all 10 seeds.

## Measurements
Per arm, condition and seed: event-level recall on malicious episodes (headline = weak+staged; also all malicious), false alert
onsets per device-day on ordinary time, precision and F1 (the §5.10 definitions, benign look-alikes counted as false alerts),
benign look-alike flag rate, blind-time fraction, degradation = recall under the condition minus the same arm's complete-signal
recall, spread across seeds. For S additionally, from the real alert manager: alerts opened per device-day and the share of malicious
episodes during which an alert was opened. Paired differences use seed-level 95% t-intervals.

## Pre-registered success criteria (all must hold to call the implementation validated)
- **R1 Benefit.** Over the 12 missing-signal conditions, S loses less headline recall than A-budget by **at least 0.08**, the seed-level
  interval of the paired difference excluding zero. (D106's equivalent ablation was +0.114; 0.08 leaves room for the real gaps.)
- **R2 Not worse than B.** S's mean degradation is no more than 0.03 worse than B's: the lower bound of the seed-level interval of
  (S − B) exceeds −0.03.
- **R3 Guardrails.** S's mean false alerts per device-day are at most 3.75 in each of the 12 conditions; its benign look-alike
  flag rate (macro over the 13 conditions) is at most 0.05 above A-budget's; its complete-signal headline recall is at least 0.03 below
  A-budget's at worst.
- **R4 Observability invariants (zero tolerance), on every tick of every end-to-end run:** (a) any tick with an unavailable signal has
  observability other than "full"; (b) any alertable tick with an unavailable signal was judged by exactly the threshold validated for its
  pattern; (c) no tick that took the fallback opened or kept open an alert; (d) the fallback fires for every pattern absent from the table
  (checked by removing patterns from a copy of the table).
- **R5 Conformance.** On seeds 400 and 401, for all 13 conditions, the product pipeline's fused score on every usable tick equals the
  offline recomputation (model on the masked soft scores) within 1e-9, and alertable/pattern agree on every tick; and the sustained-condition
  detector outputs equal the complete run's for the signals that remain (bit-identical).
- **R6 Benefit vs the dashboard promise.** Under the sustained network-missing condition S's headline recall must exceed A-budget's
  by at least 0.20 (D106 saw 0.18 → 0.46–0.54).

If any criterion fails, the result is reported as such and the implementation is described as **not validated on that criterion**; nothing
is retuned on these seeds. A change made after seeing the results needs new seeds.

## Limits stated in advance
Masks are mine, not measured failure statistics; one generator and fleet size; the sustained equivalence argument is verified on two seeds, not
ten; the alert budget of 3 false onsets per device-day is still too high for production; the dashboard is checked by an automated browser run,
not by a user study; the frozen model's reliance on the network signal (§5.10) inflates how much losing it hurts.

---

## Addendum 1 (before any test seed was scored)

A pipeline check on **validation seed 18** (all 13 conditions end to end; `python -m ml.d107 build-dry`, evaluated with `ml.d107_eval --dry`) found two
defects, both fixed before any D107 test seed was scored. The test data first built from seeds 400–409 (with the defective detector) was **discarded
unscored** and will be rebuilt with the fix.

1. **Detector windows after an outage.** With telemetry gaps (the intermittent condition), every arm raised about 20 false alert onsets per
   device-day instead of about 3. The cause was the network detector (and, mildly, the device detector): after a gap their one-minute smoothing window
   holds one or two samples, so a single noisy tick looks extreme (7.4% of network scores above 0.3 right after a return, against 0.3% in steady state).
   Fix: a detector whose signal was unavailable reports itself **unavailable (and learns nothing) until one smoothing window has refilled**, so the
   observable state is "degraded: that signal", validated by the same per-pattern thresholds. Complete-signal behaviour is **bit-identical** to the
   pre-availability engine (21,600 events, 6 devices, no mismatch), so no frozen result changes. The shipped commit is now the one containing this fix
   (recorded in the results); the criteria below are unchanged.
2. **Evaluator.** The dry evaluator scored raw soft scores instead of the frozen 1 s decayed peak the correlation engine applies; corrected. R5 is also
   restated at the resolution the product rows are stored (float32): the product's fused score must equal the offline recomputation within 1.2e-7,
   one float32 step, not 1e-9.

Nothing else changes: arms, conditions, seeds, thresholds table (not recalibrated), criteria R1–R6 and their cut-offs are as registered above.
