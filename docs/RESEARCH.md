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

Location and proximity detection on real devices; per-device baselines on a real fleet; the correlation time window on real time series
(the frozen window is 1 s, i.e. none); cold-start contamination fixes; any alert rate low enough for production (the budget used is 3 false
onsets per device-day); a user study of the Degraded display. The real-data checks: TON_IoT Windows host counters (two signals, label confounded
with capture session; fusion beat every single group on one OS and none on the other) and IoT-23 (network only). Masks used for missingness are
mine, not measured failure statistics.

## Honest corrections made along the way

C2b in E1/E2 was inflated by a 60 s memory handicap on the Isolation Forest control (F1 P4 found no difference); a firmware-update claim was
withdrawn; D107's dry run on a validation seed found two real defects (after an outage the detectors judged a nearly empty window, raised false alerts from about 3 to about 20 per device-day; GPS loss stretched the proximity clock) and they were fixed before any test seed was scored, recorded as pre-registration addenda 1–3.

## Reproduce and verify

Verified on 29 Oct 2026: 192 Python tests against real Redis 7.4 (Docker), the container smoke test, the browser end-to-end run and the demo story, all against the
running Docker stack (built with the registry-less overlay because Docker Hub was unreachable; a from-scratch `docker compose build` was not re-verified).

```
python -m pytest tests/test_frozen_artifact.py          # the frozen files, results and raw data still match the manifest
python -m ml.d107 build-dry && python -m ml.d107_eval --dry   # the evaluator on a validation seed (the real run is guarded: one use)
docker compose up -d --build && python scripts/smoke_stack.py && python scripts/e2e_dashboard.py && python scripts/demo_story.py
```

Protocols: [`preregistration/D106-missing-signals.md`](preregistration/D106-missing-signals.md),
[`preregistration/D107-availability-implementation.md`](preregistration/D107-availability-implementation.md). The earlier protocols (E1, E2, F1) are in
the technical manual (§5.5–§5.7); the manual is a local study document and is not in the repository.
