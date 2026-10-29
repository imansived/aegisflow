<!-- Pre-registration R2. Committed before any candidate detector exists, before the test pcap days are downloaded and before any IoT-23 capture is opened for eligibility. research-v1, R1 and the frozen fusion are not modified by anything below. -->

# R2 — can modelling sparse network telemetry cut false alarms without losing attacks?

## 0. Baseline lock (R1) and what stays frozen
R1 replayed real IoT-23 traces through the unchanged `research-v1` system. Its result is the baseline and is **never re-run, re-tuned or edited**:
**31.6 false alert onsets per benign device-day** (95% interval 25.5–38.8; 91 onsets in 2.88 benign device-days over five captures), the protocol, the
traces' hashes and the results are frozen in `docs/research/r1/manifest.json` (tag `replay-r1`, guarded by `tests/test_frozen_artifact.py`).
**Untouched throughout R2:** everything under `backend/`, `contracts/`, `models/` (the learned fusion, the availability table), the D106/D107 results,
and the detectors of `research-v1`. R2 code lives only in `ml/` and the candidates are *evaluated beside* the v1 network detector, never replacing it.
If a candidate wins, adopting it is a separate versioned change (`research-v2`) with its own evaluation; R2 itself changes no product behaviour.

## 1. Question and hypothesis
**Q.** Can modelling sparse network telemetry reduce false alarms without materially reducing attack detection?
**H0 (the data-generating assumption).** R1's false alarms come from the v1 network detector's per-tick, approximately-Gaussian-on-log1p assumption, which
real traffic (90% silent ticks, rare large bursts) violates. **H0 is tested first, not assumed:** an arm that keeps the v1 detector and only recalibrates
its threshold on real benign traffic (R1-recal) separates "wrong distribution model" from "wrong threshold". If R1-recal already meets the budget at
acceptable detection, the finding is miscalibration, and neither candidate is credited with the improvement.

## 2. Arms (all online, unsupervised, 5-second ticks, per device, identical inputs)
| arm | what it is |
|---|---|
| **R1-shipped** | the `research-v1` network detector through the real pipeline and the shipped network-only threshold. Reference only (its R1 number) |
| **R1-recal** | the same v1 detector; its threshold re-set on validation benign traffic to the budget (section 5) |
| **A, zero-inflated** | below |
| **B, windowed-rate** | below |

Both candidates compute a volume score and combine it with the v1 *unfamiliar-endpoint* item unchanged (taken from the v1 engine's evidence), so only the volume
model differs: `z = max(z_volume, z_unfamiliar)`, `score = 1 − exp(−0.5 (z/3)²)` for z > 0 (the v1 soft ramp, σ = 3). Window W = 12 ticks (60 s); cold start
720 ticks; adaptation as v1 (Welford for 3,000 samples then exponential weighting with window 8,000). **These values are fixed here; none is tuned.**

**A (zero-inflated).** Per device, one bucket (no time-of-day). State: the probability `p` that a tick is active (any bytes or connections), an exponentially
weighted estimate (window 8,000), clipped to [0.02, 0.98]; and a running mean and standard deviation of `log1p(sent + recv)` over **active ticks only**.
Per tick, from the last W ticks: (a) `z_count` = the normal quantile of 1 − P(X ≥ n_active), with X ~ Binomial(W, p) and n_active the active ticks in the window;
(b) `z_mag` = (mean of `log1p(sent + recv)` over the window's active ticks − running mean) / max(running std, 0.5), only when n_active > 0, high side only.
`z_volume = max(z_count, z_mag)`. Scoring starts once 720 ticks and 50 active ticks have been seen.

**B (windowed-rate, non-parametric).** Features at each tick: bytes per second over the last 60 s and over the last 300 s, and connections per minute over the last 60 s and 300 s.
Baseline per feature: the last 17,280 values (24 h of ticks) in a sorted store. Score: the empirical upper-tail probability q = (number of stored values ≥ x + 1)/(N + 1),
converted to `z = Φ⁻¹(1 − q)` clipped to [−8, 8]. `z_volume` = the largest of the four. Cold start 720 ticks.

## 3. Data and the evaluation split (decided now, in this order)
Device scope for the UNSW testbed: the 23 IoT device MACs of the published `List_Of_Devices.txt` (everything except the gateway, the three phones/iPhone, the tablet,
the laptop and the two MacBooks); each (device, day) is one *benign device-day*.
- **Development (seen, design only, no claims):** IoT-23 captures Honeypot-4-1, Honeypot-5-1, Malware-42-1, -20-1, -21-1, -8-1, -34-1. Used to debug the candidates and
  to look at behaviour. Their false-alarm numbers are reported but are **not** evidence for R2.
- **Validation (decisions: threshold of every arm, choice of candidate):** benign = UNSW IoT-testbed day **16-10-12** (packet-metadata CSV, ≈ 23 devices);
  attacks = the IoT-23 captures R1 already processed: Malware-3-1, -1-1, 9-1 (head), 49-1 (head) plus the development attack captures 8-1, 20-1, 21-1, 34-1.
- **Test (sealed until the validation decisions are committed):** benign = UNSW days **16-10-13 and 16-10-14**, converted from their pcaps by the same adapter
  (checked first against the CSV on a prefix of 16-10-12); attacks = IoT-23 captures never opened before this commit, drawn from the first 100 MB (cut at a line boundary) of
  Malware-7-1, -17-1, -33-1, -35-1, -36-1, -39-1, -44-1, -52-1 and -60-1. **Eligibility is decided from labels alone, before any detector runs:** the capture's busiest private
  host has at least 60 minutes of flows before its first malicious flow, and at least 20 malicious ticks afterwards. Eligible captures are used in numerical order; ineligible
  ones are reported as such.
No test capture or day is opened, and no detector is run on it, before the validation decisions and the final code are committed (guard file `data/results/R2_TEST_USED`,
written once).

**Adapter.** UNSW packet metadata → telemetry per device per 5-second tick, location, device-health and proximity explicit nulls: `bytes_sent` = sum of packet sizes whose
source MAC is the device; `bytes_received` = sum where it is the destination; `network_connections` = distinct outbound (remote IP, remote port, protocol) triples in the tick;
`remote_endpoints` = distinct remote IPs. The metadata has one-second timestamps and no labels; **all UNSW traffic is treated as benign, which is an assumption** (the testbed
paper describes ordinary device operation). IoT-23 uses the R1 adapter unchanged.

## 4. Measurements
All arms use the same burn-in: the first 2 hours of each (device, day) or capture are excluded from every count. An **onset** is a rising edge of "score ≥ threshold" on a tick.
- **Primary: false alert onsets per benign device-day** (UNSW test days), pooled, with a **device-day cluster bootstrap** 95% interval (10,000 resamples of device-days).
- **Alert model for durations:** an alert opens at an onset and closes when the score has stayed below (threshold − 0.08) for 60 s. Reported: alerts per device-day and mean/median alert duration, for false alerts.
- **Detection (IoT-23 attack captures):** an *episode* = a run of malicious ticks with gaps ≤ 60 s. Episode recall = fraction with any tick ≥ threshold inside the episode or the 60 s before it
  (the second part allows for a flow's tick being counted slightly late); **detection delay** = time from the episode's first malicious tick to the first score ≥ threshold (undetected episodes excluded from the
  median and counted); AUROC of the score for malicious against benign ticks that carry traffic. Reported per capture and pooled, with the number of episodes.
- Reported but not decided on: the soft-score distribution on benign ticks; per-device false-alarm rates (overdispersion across devices).

## 5. Thresholds
For each arm independently, on **validation benign (UNSW 16-10-12) only**: a single global threshold on the score, the smallest on a 0.005 grid whose false onsets per benign device-day is
**at most 3.0**, the budget used in every earlier experiment. No threshold is set on test data, on development data, or by looking at attacks.

## 6. Decision rules (fixed now)
**Candidate choice (validation only).** Among A and B, the one with the higher pooled episode recall on the validation attack captures at its calibrated threshold; if within 0.03,
the lower median detection delay; if still tied, **B** (fewer distributional assumptions). The test set plays no part in this choice, and only the chosen candidate is reported as "the" R2
result (the other is reported as not selected).
**Test claims, in order:**
1. **False-alarm budget.** `PASS` if the upper 95% bound on false onsets per benign device-day is ≤ **3.75** (1.25 × budget). `INCONCLUSIVE` if the point estimate is ≤ 3.0 but the upper bound exceeds 3.75
   (not enough exposure). `FAIL` otherwise. At least **20 benign device-days after burn-in** are required for PASS; with fewer the result is INCONCLUSIVE whatever the estimate.
2. **Detection not materially worse than R1-recal, on the same budget.** Pass requires all of: pooled episode recall no more than **0.10 below** R1-recal's; median detection delay no more than **60 s worse**;
   AUROC no more than **0.05 lower**. Requires at least 10 test episodes in at least 3 eligible captures; otherwise INCONCLUSIVE, stated as such.
3. **Better than doing nothing clever.** The chosen candidate "improves on R1" only if claims 1 and 2 pass **and** its false onsets per device-day are below R1-recal's with the bootstrap interval of the paired
   difference excluding zero. If R1-recal itself passes claim 1, the conclusion is that the failure was **calibration, not the distribution model**, and the candidate is not credited.
A candidate that lowers false alarms by missing attacks fails claim 2 and is reported as such.

## 7. Not claimed
That any arm validates location, device-health, proximity detection, fusion or the correlation window; that UNSW's testbed (2016, consumer devices, one lab) represents fleets in general; that all UNSW
traffic is truly benign; that the packet-metadata adapter equals a Zeek flow; that an improved network detector would keep the fusion thresholds valid (a changed detector needs its own availability-table
calibration, which is `research-v2` work). Test exposure is limited to two days.

## 8. Order of work, so nothing is chosen on the test set
1. Commit this protocol. 2. Build the adapters and the candidates; debug on **development** captures only. 3. Calibrate and select on **validation**; commit the decisions and the final code with their results.
4. Only then acquire and open the test data; run once. 5. Report every arm, including the ones that lose.

## 9. What comes after, separately (not part of R2)
R3: declared signal sets, to tell a signal a device never has from one it lost (a network-only device is Degraded for good today). R4: partial-observation wording in alert text (an interpretation fix, not a
detector fix). Each gets its own protocol; neither is started before R2 is reported.
