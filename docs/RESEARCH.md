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
