# AegisFlow: research results (frozen as `research-v1`)

**What was studied.** Whether fusing four per-signal anomaly detectors (location, network, device health, proximity) is more useful than
the detectors on their own, and whether the system can keep working when a signal disappears. Everything is evidence about a **simulator I
wrote**, except two public real-data checks that cover only part of the system. Nothing here says the method works on a real fleet.

**How to read the claims.** Each row names the experiment, the file with the raw numbers, and the commits. A row marked *pre-registered* had its
protocol committed before its test seeds were generated or scored; *exploratory* means it was done after seeing results and decides nothing.
Seeds: validation 10–19 (fit and thresholds only); test seeds 100–109 (F1), 300–309 (D106), 400–409 (D107) are **spent**: scored once each.
The artifacts behind these claims are frozen with hashes in [`research/v1/manifest.json`](research/v1/manifest.json) and guarded by
`tests/test_frozen_artifact.py`.

## Headline claims

| # | Claim | Verdict | Evidence | Experiment (status, seeds) | Commits |
|---|---|---|---|---|---|
| 1 | Fusing signals beats "any detector fires" (max of detectors) | **Yes**: +0.43 recall, 95% interval [+0.39, +0.48] | `results/f1.json` (P1) | F1 (pre-registered, 100–109); holds in every stress condition (+0.14 to +0.57), `results/audit.json` | pre-reg `8294f17`, results `9d6df10` |
| 2 | Fusion beats the single best detector (network-only) | **Not shown**: +0.017 [−0.006, +0.041]; seed-level +0.019 [−0.002, +0.040]; it reverses under sensor noise (−0.08, interval excludes 0) and when location is missing (−0.06) | `results/f1.json` (P2), `results/audit.json` | F1; audit (exploratory, fresh seeds 200–254) | `9d6df10`, `37cc1f2` |
| 3 | A supervised joint model is better than late fusion on recall | Yes: −0.09 [−0.12, −0.07] for late fusion | `results/f1.json` (P3) | F1 | `9d6df10` |
| 4 | The "0.1% false-alarm budget" is a usable alert rate | **No**: it means 2.2 to 7.2 false alert onsets per device per day; alert-level precision 0.28–0.48 | `results/audit.json`, `audit_summary.txt` | audit (exploratory) | `37cc1f2` |
| 5 | Reading a missing signal as zero is safe | **No**: with the network signal missing, headline recall falls from 0.83–0.87 to 0.18–0.23 while the alert rate drops, which looks quiet and is blind | `results/d106_final.json`, `d107_final.json` | D106 (pre-registered, 300–309), D107 (pre-registered, 400–409) | `4733306`, `8ca1069`, `b02c018`; `8eacdbb`, `cbda3e8` |
| 6 | Per-availability learned models (B) degrade more gracefully than the old behaviour | Yes: loses 0.124 less recall [+0.112, +0.137], 10/10 seeds | `results/d106_final.json` | D106 | `b02c018` |
| 7 | **Thresholds alone capture almost all of B's gain** | Yes: frozen model + a validated threshold per availability pattern loses 0.226 vs B's 0.216; B's models add +0.011 [+0.005, +0.017] | `results/d106_ablation.json` | D106 post-hoc ablation (exploratory, same seeds) | `b02c018` |
| 8 | The shipped implementation (explicit nulls, per-pattern thresholds, conservative fallback, Degraded state) works end to end | **Validated, all six criteria**: loses 0.102 less recall than the old behaviour [+0.086, +0.118], 10/10 seeds; 0.013 behind B (inside the registered 0.03 margin); worst false alerts 3.28 per device-day; zero observability-invariant violations in 14.6 M rows; product scores match an independent recomputation to 2e-15 | `results/d107_final.json`, `d107_final.log` | D107 (pre-registered, real pipeline, 400–409) | `8eacdbb` (+ addenda `5eca50a`, `232dbc0`, `dbfd2f4`), `cbda3e8` |
| 9 | The network detector and shipped thresholds behave sensibly on real device traffic | **No**: on real benign IoT traces the shipped threshold raises **31.6 false alert onsets per device-day** (95% interval 25.5–38.8; 91 onsets in 2.9 benign device-days), about 10× the 3.0 budget and 5.6 in simulation. The availability and recovery states behave exactly as designed | `results/r1_replay.json`, `r1_compare.json` | R1 (exploratory replay of the unchanged `research-v1`; real IoT-23 traces, network signal only) | pre-reg `e6c9658` + addendum; results tag `replay-r1` |

## Main comparison for the shipped design (D107, 10 fresh seeds, 12 missing-signal conditions)

| arm | recall, all signals | false alerts / device-day | mean recall lost when signals go missing | worst false alerts in any condition |
|---|---|---|---|---|
| Old behaviour, original threshold | 0.88 | 4.9 | −0.31 | 4.8 |
| Old behaviour, one threshold at the alert budget (**control**) | 0.83 | 2.6 | −0.31 | 3.2 |
| **Shipped: validated threshold per available-signal pattern** | 0.83 | 2.6 | **−0.21** | 3.3 |
| Per-pattern learned models (D106's B) | 0.83 | 2.6 | −0.20 | 3.3 |

By condition (headline recall, control → shipped): network missing 0.23 → 0.44; network + device 0.01 → 0.51; location missing 0.71 → 0.75;
anomaly-correlated loss 0.31 → 0.44; intermittent loss 0.74 → 0.79.

## Why thresholds only, not per-pattern models

D106 compared reading a missing signal as zero (control) with a separate learned fusion per availability pattern (B). B lost 0.124 less recall.
A post-hoc ablation then gave the *unchanged* model a separate threshold per pattern and nothing else: it lost 0.226 against B's 0.216, so
**about nine tenths of the gain is knowing a signal is gone and re-setting the alert level to match, not refitting the model**. The thresholds-only
version is 15 auditable numbers instead of 15 trained models, leaves the validated model untouched (so every earlier result stays valid), and
makes the fallback simple (a pattern with no validated number raises no alert and is shown as Degraded). D107 then validated that version on new
seeds: 0.013 behind B, inside a margin fixed in advance. B remains the upgrade if the last 0.013 (concentrated in network-missing cases) is worth
15 models to maintain.

## Real-trace replay (R1): the system on data its simulator did not make

Protocol: [`preregistration/R1-iot23-replay.md`](preregistration/R1-iot23-replay.md). The unchanged `research-v1` system (detectors, learned fusion,
availability table, alert manager) was fed real Stratosphere IoT-23 flow logs, tick by tick with real timestamps, through the same pipeline the server uses.
Only the **network** signal exists in this data; location, device health and proximity are explicit nulls. So this says nothing about those three signals or
about fusion, and it replays one device per capture, not a fleet. It was exploratory: seven captures had already been run once through a cruder script, so a poor
false-alarm result was expected. The held-out captures (3-1, 1-1, 9-1 head, 49-1 head, Somfy-01) turned out to be malicious from their first flow or too short,
so they carry almost no benign time; the false-alarm numbers rest on five development captures.

| Question | Result |
|---|---|
| **False alarms** (benign ticks, shipped threshold) | **31.6 per device-day** pooled over five benign-rich captures (2.88 benign device-days, 91 onsets; 95% interval 25.5–38.8); per capture 24–57. At the old F1 threshold: 34. The alert manager (which merges bursts) opened 15 false alerts per device-day. With 20% intermittent network loss: 38.7 (30.5–48.6) |
| **Real vs simulated distributions** | A real device sends nothing on **90%** of 5-second ticks (simulator: 3%). Bytes per tick: real median 0, 99th percentile 90 B; simulated median 10 KB, 99th percentile 110 KB (Kolmogorov–Smirnov distance 0.999). The network score exceeds 0.3 on 0.5–5.2% of real benign ticks against 0.12% in simulation, 4 to 44 times more |
| **Availability states** | Over 207,812 replayed ticks the device was **never shown as Normal** (187,141 Degraded, 20,671 Anomalous: three of its four signals do not exist), was always judged by the validated network-only threshold, and the fallback never fired while the network signal was present |
| **Recovery** | With injected outages: 55,530 blind ticks (no signal at all) raised no alert; after every uninterrupted outage (1,904 cases) judgment resumed after exactly 11 ticks (the 60-second window), and **no alert opened while blind or recovering** (0 violations in 194 alerts) |
| **Explainability** | All 176 alerts in steady replay name the network features that moved (130 name two, 13 three or more) and record the unavailable signals, **but none of the explanation texts says the device was only partly observed**: the fact is in the alert record, not in the sentence an analyst reads |
| **Ranking** | AUROC 0.76 (3-1 and 34-1), 0.38 (8-1), 0.51 (1-1); where the device is infected from its first flow the baseline learns the malware as normal. 20-1 and 21-1 (long benign baseline, 6 and 10 malicious ticks) are too small to score |

What this means: the network detector's per-tick, lognormal-style assumptions fit the simulator's always-chatty devices and fail on sparse, bursty real traffic.
Two further design gaps surfaced: availability cannot tell a signal a device **never has** from one it **lost** (this device is Degraded for the whole replay by
construction), and the explanation sentence omits partial observation. **`research-v1` is left as it is.** Candidate changes for a new version, each needing its own
protocol and fresh evaluation: a zero-inflated or windowed-rate network detector; a per-device declared signal set; partial-observation wording in alert text.

## Sparse-network detectors (R2): did modelling the zeros fix the false alarms?

Protocol: [`preregistration/R2-sparse-network-detector.md`](preregistration/R2-sparse-network-detector.md), committed before any candidate existed. Two offline
candidates (A zero-inflated, B windowed-rate) ran beside the unchanged v1 detector, with a control (**R1-recal**: the v1 detector, threshold re-set to the 3.0/day
budget on validation benign traffic) so that "wrong distribution model" could be told from "wrong threshold". Thresholds and the candidate choice were fixed on
validation (UNSW IoT testbed day 16-10-12 as benign); the test (UNSW days 16-10-13 and 16-10-14, 28.4 benign device-days after burn-in from 31 device-day traces) was opened once.

| Arm | Validation benign /device-day | **Test benign /device-day [95% CI]** | Test alerts: mean / median duration |
|---|---|---|---|
| R1-shipped (v1, shipped threshold) | 71.0 | **56.7** | n/a |
| R1-recal (v1, re-calibrated) | 2.88 | **11.7 [0.7, 31.3]** | 1038 s / 68 s |
| **A zero-inflated (chosen on validation)** | 2.88 | **2.36 [0.67, 4.54]** | 880 s / 55 s |
| B windowed-rate (not chosen) | 2.88 | 1.94 [0.53, 3.91] | 1159 s / 70 s |

Pre-registered claims, as scored:
1. **False-alarm budget (chosen A): INCONCLUSIVE.** The point estimate (2.36) is under 3.0, but the upper bound (4.54) is above the 3.75 limit: 28 device-days are not enough exposure. B's interval (upper 3.91) misses the same way.
2. **Detection not worse than R1-recal: INCONCLUSIVE, no test episodes exist.** None of the nine never-opened IoT-23 captures met the label-only eligibility rule (at least 60 minutes of benign flows before the first malicious flow): all are malicious from the first minutes (first malicious tick 0 to 105, rule 720). So **detection was not measured on test data.** On validation (19 episodes, thresholds at the 3.0/day budget) all arms detected about the same small number: R1-recal 6, A 5, B 5, with AUROC 0.68, 0.67, 0.70.
3. **Better than recalibration: NOT SHOWN.** A minus R1-recal is -9.3/device-day, but its paired interval [-28.2, +0.6] includes zero.

What this supports, and what it does not:
- The v1 threshold set for the simulator is badly wrong for real devices (57 false onsets per device-day), and recalibration alone did **not** transfer: R1-recal went from 2.88 on validation to 11.7 on test, with a very wide interval. Nobody should read "just re-tune the threshold" as solved.
- Both candidates landed near the budget on held-out days (2.36 and 1.94), consistent with, but not proof of, the windowed or zero-inflated models generalising better. The exposure is too small to claim it, and the improvement over R1-recal is not statistically shown.
- At the budget, detection is weak for every arm (about 5 of 19 validation episodes). Lower false alarms were not bought by missing *more* attacks than the control, but the whole family catches few. There is no test-set detection evidence at all.
- Unmet by design: all UNSW traffic was assumed benign; the packet-metadata adapter is not a Zeek flow; candidates have no learn-gate and use one time bucket. The unseen IoT-23 captures are infected from the start, so they cannot test "attack after a benign baseline".
- `research-v1` is unchanged. Adopting A or B would be a `research-v2` with its own availability calibration and a detection test on data that has a benign baseline before the attack.

## Observation semantics (R3) and alert wording (R4): product changes, not detection results

Specification and acceptance tests, committed before the code: [`preregistration/R3-R4-observation-semantics.md`](preregistration/R3-R4-observation-semantics.md). They do **not**
improve detection and say nothing about the open R2 question. R2 stays sealed and unchanged.

- **R3.** A device may declare the signals it supports (`signals` on `POST /devices`, stored with the device; default all four). Each tick, each signal is *observed*, *unsupported* (never declared;
  a value sent anyway is ignored and counted), *missing* (declared, null) or *recovering* (declared, back, judgment withheld for 60 s). Observability is judged against the declared set, so a network-only
  device with a healthy network signal is `full`/Normal instead of Degraded for life, while a declared signal that disappears is Degraded exactly as before. Scores, thresholds and alert decisions are unchanged:
  a test feeds one scripted run to a declared and an undeclared network-only device and requires identical scores, severities, patterns and alert events.
- **R4.** Every alert record carries `unsupported`, `missing`, `recovering` and an `observation` sentence generated from those same sets, e.g. "The location, device-health and proximity signals are not supported by this
  device. This alert is based on network telemetry only. Signals that were not observed neither confirm nor rule out an attack." Tests check that the sentence names exactly the signals in the record, never lists a
  missing signal as a contributing factor, and that fully observed alerts keep their original wording.
- Release evidence is frozen (`docs/research/r3r4`, tag `observation-r3r4`, [`results/r3r4_release.json`](results/r3r4_release.json)): 221 backend tests (real Redis, none skipped), 62 frontend tests, the three earlier manifests still matching. Limitations kept with it: Docker images not rebuilt for this release; persistence tested at store/API level, not through a container restart; the fake-Redis test server is not a supported test target (it caused timing failures that real Redis does not).
- Next: a separate detection experiment on attacks with a clean baseline and a dated onset, specified before any of its data is collected: [`preregistration/R5-detection-delay.md`](preregistration/R5-detection-delay.md).
- Evidence: 23 new tests (`tests/test_observation.py`), one API test and one frontend test. Not claimed: that operators understand the wording (no user study), or that an operator-set declaration cannot be wrong.

## Detection on attacks with a clean baseline (R5): the data did not exist, the false-alarm budget held

Protocol: [`preregistration/R5-detection-delay.md`](preregistration/R5-detection-delay.md) (two revisions, both before any R5 data was opened). Freeze: `docs/research/r5inv` (inventory) and `docs/research/r5` (results).

**Feasibility gate: FAILED, so no detection claim exists.** A metadata-only inventory (schedule, device table and file listings; no capture, flow data or attack log opened) of the UNSW IoT attack traces found 234 scheduled attack episodes with exact epoch onsets and
**15 eligible** against 30 required (13 sessions, 5 tools, none with 10 episodes). All 219 exclusions are the clean-prefix rule: the attacks are scheduled back to back about every 27 minutes, so only the first attack of a run on a device has 6 hours of unattacked traffic before it.
Neither the rules nor the gate were loosened; the detection part (recall, delay, claims D1/D2) was **not run**, and no capture-to-split assignment exists. Delay would have been compared by restricted mean time-to-detection (tau 300 s, since attacks last 600 s). Detection of attacks remains **unmeasured**.

**Claim F (false-alarm budget) ran as specified.** Thresholds were set per arm on validation benign (UNSW 16-10-12 plus the three oldest unopened days, 52.2 benign device-days) and then applied once to nine further unopened days (175 device-day traces, **159.6 benign device-days** after burn-in, 120 required):

| Arm | Threshold | Validation /device-day | **Test /device-day [95% CI]** | Claim F |
|---|---|---|---|---|
| R1-shipped (v1) | shipped | n/a | 45.5 | n/a |
| R1-recal | 1.000 | 0.02 | 0.21 [0.03, 0.56] | PASS, **vacuous** (see below) |
| **A zero-inflated (primary)** | 0.725 | 2.87 | **2.68 [2.00, 3.42]** | **PASS** (upper bound under 3.75) |
| B windowed-rate | 0.570 | 2.85 | 2.10 [1.33, 2.97] | PASS |

- **A and B hold the 3.0 budget on held-out days** in this testbed, with enough exposure to say so (R2's 28 device-days could not). The R2 thresholds applied unchanged also stayed under budget (A 1.05, B 0.83) but R1-recal at its R2 threshold did not (3.70 [2.32, 5.37]).
- **R1-recal "passes" only because it is disabled.** The v1 score saturates, no threshold below 1.0 reaches the budget, and at 1.000 it raised 0.06 alerts per device-day. A false-alarm test cannot tell a good detector from one that never fires; claim F is half of the evidence and the detection half is missing.
- **"Better than recalibration" is not shown:** A has 2.46 more false onsets per device-day than R1-recal (interval 1.70 to 3.24), because it can alert at all; whether those alerts buy detection is exactly what could not be measured.
- Alerts under A last a median of 40 s (mean 155 s); under B 75 s (mean 329 s); R1-recal's few alerts last a median of 655 s.
- **Not supported:** that A or B catches attacks; that thresholds transfer across years or devices. The benign days are the 2016 testbed (23 devices) while the attacked devices are the 2018 testbed (10 devices). All UNSW traffic is assumed benign. The synthetic-overlay (S3) and matched 2018 check (S4) were not run: both are detection analyses and were held back when the gate failed.

## Matched-testbed detection (R6): stopped at the stage-1 gate

Protocol: [`preregistration/R6-matched-testbed-detection.md`](preregistration/R6-matched-testbed-detection.md); audit frozen as `docs/research/r6audit` (tag `audit-r6`). R6 asked the R5 detection question in the 2018 testbed with a stitched, verified-continuous baseline, thresholds set on validation days of the same devices, and false alarms measured in the same streams as detection.

**Result: the detection part was not run.** The metadata-only audit (schedule, device table, file listings, HTTP HEAD for size; no capture opened) found 36 eligible episodes in total and **26 in the test split against 30 required**, even on the optimistic assumption that adjacent daily files are contiguous. The protocol says that a failed optimistic gate stops the experiment, so nothing was downloaded (68 GB) or scored, and the rules were not relaxed. 198 of 234 episodes fail the 6-hour clean-before-onset rule (attacks are scheduled in runs); the 24-hour chain rule excluded none on the optimistic count. The test split (hash of the day label, mod 3) has 106.8 benign device-days (75.0 benign-list, 31.8 attack-free), enough for claim F, but the joint claim "detects attacks at an acceptable false-alarm rate" requires detection, so it cannot be made.
**Detection of attacks remains unmeasured.** Two correction notes: the R5 inventory recorded `404` for every file-availability check because of a faulty HTTP HEAD helper (files exist; the gate and results were unaffected; `results/r5_inventory_erratum.md`).

## Separated-baseline gap (R7): stage-1 gate passed, captures not yet opened

Protocol: [`preregistration/R7-separated-baseline-gap.md`](preregistration/R7-separated-baseline-gap.md) (a new version; R6 is unchanged). R7 replaces R6's 6-hour clean-before-onset rule by a **60-minute gap**, derived from the detectors' code (longest window 300 s, alert close 60 s) and R6's own 30-minute false-alarm guard plus 30 minutes of pre-onset observation, and separates it from the 24-hour verified-stream requirement, which is unchanged. The R6 split, continuity rules, arms and claims are unchanged, with a stated heterogeneity rule for the new 1-to-6-hour stratum (S0). The author knew R6's count when writing it; the protocol discloses that and forbids any further revision of this dataset's rules.
**Stage-1 audit (schedule and listings only): 47 eligible episodes in the test split (30 required), 20 sessions, 9 devices, 6 tools** (S0 21, S1 12, S2 14; TcpSyn variants account for 33 of the 47), 63 eligible overall, 171 excluded as campaign continuations. Test benign exposure 106.8 device-days. Frozen as `docs/research/r7audit` (tag `audit-r7`). This is an optimistic count that assumes contiguous daily files; **nothing has been downloaded or scored**, stage 2 (timestamps and Ethernet addresses only) and the gate re-applied to the verified set come next.

## Failure cases (what it cannot do)

| Situation | Result | Source |
|---|---|---|
| Location **and** network both unavailable | recall 0.01–0.08 for every arm: weak attacks need one of the two | D107 `pair:spatial+network` |
| Noisy sensors (GPS 10 m, jittery readings) | with the frozen thresholds the learned fusion raises 111 false alerts per device-day (precision 0.03); re-thresholded it is *worse than network-only* | audit `noisy` |
| Benign drift on half the devices | flood (10–290 false alerts/day, by model) or, re-thresholded, near blindness (recall 0.15–0.20) | audit `drift` |
| Real IoT traffic (IoT-23, network signal only) | the frozen network threshold gives **12–76 false alerts per device-day** (5.6 in simulation); as a ranking it still separates rare malicious flows well (AUROC 0.92–0.96, few windows) | `results/iot23.json` |
| Device infected from the start | baseline learns the malware as normal: AUROC 0.38 on IoT-23 capture 8-1 | `results/iot23.json` |
| Benign look-alikes (firmware update, authorised visit) | 97–98% flagged by every fusion system: there is no context input | `results/audit.json` |
| An attack inside a blind spot | invisible by construction; flagged only after the signal returns and its one-minute window refills | demo scene 4–6 |
| Signal returns after an outage | judgment withheld for one smoothing window (60 s) | D107 addendum 1 |
| Attack that blocks the signals it touches | recall falls to 0.44 for the shipped design from 0.83 | D107 `correlated` |

## What the evidence does not cover

Location, device-health and proximity detection on real devices (the replay covers the network signal only); per-device baselines on a real fleet; the correlation time window on real time series
(the frozen window is 1 s, i.e. none); cold-start contamination fixes; any alert rate low enough for production (the budget used is 3 false
onsets per device-day); a user study of the Degraded display. The real-data checks: TON_IoT Windows host counters (two signals, label confounded
with capture session; fusion beat every single group on one OS and none on the other) and IoT-23 (network only). Masks used for missingness are
mine, not measured failure statistics.

## Honest corrections made along the way

C2b in E1/E2 was inflated by a 60 s memory handicap on the Isolation Forest control (F1 P4 found no difference); a firmware-update claim was
withdrawn; D107's dry run on a validation seed found two real defects (after an outage the detectors judged a nearly empty window, raised false alerts from about 3 to about 20 per device-day; GPS loss stretched the proximity clock) and they were fixed before any test seed was scored, recorded as pre-registration addenda 1–3.

## Reproduce and verify

Verified on 29 Oct 2026 against the Docker stack: 192 Python tests against real Redis 7.4, the container smoke test, the browser end-to-end run and the demo story,
first through a registry-less overlay build. **The official build was then verified from a clean environment**: a fresh `git clone` of commit `b203fa1`, every
`aegisflow-*` image and the build cache removed, `docker compose build --no-cache --pull` (no overlay, no pre-existing project images), the stack started, and the smoke
test, browser E2E and demo story all passed against it.

```
python -m pytest tests/test_frozen_artifact.py          # the frozen files, results and raw data still match the manifest
python -m ml.d107 build-dry && python -m ml.d107_eval --dry   # the evaluator on a validation seed (the real run is guarded: one use)
docker compose up -d --build && python scripts/smoke_stack.py && python scripts/e2e_dashboard.py && python scripts/demo_story.py
```

Protocols: [`preregistration/D106-missing-signals.md`](preregistration/D106-missing-signals.md),
[`preregistration/D107-availability-implementation.md`](preregistration/D107-availability-implementation.md). The earlier protocols (E1, E2, F1) are in
the technical manual (§5.5–§5.7); the manual is a local study document and is not in the repository.
