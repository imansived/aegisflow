<!-- Pre-registration R7. Supersedes R6's clean-gap rule only. Committed before the R7 audit ran and before any 2018 capture was downloaded or opened. R6 stays frozen and unmodified. -->

# R7 — R6 with a separately justified gap between attacks

**Relation to R6.** R6 (`docs/preregistration/R6-matched-testbed-detection.md`, audit frozen as `audit-r6`) stopped at its stage-1 gate: 26 eligible test episodes of 30 required. R7 is a **new protocol version**, not an edit of R6. **Everything in R6 applies unchanged** (question, material, one stream per device, continuity stage 1 and stage 2 with their rules, arms, thresholds and the "not calibratable" rule, F1/F2 exposure, detection definitions, claims F, D1, D2, B, J, the sensitivity analysis, the **same split** `sha256('R6/<yy-mm-dd>') mod 3` with the same assignments, the gate numbers, order of work, not-claimed) **except** the items in sections 2 and 3 below. The split is deliberately not re-drawn: re-drawing it after seeing the R6 outcome would be a free choice made with the answer in view.

**Disclosure.** The author wrote this after seeing R6's stage-1 count, and already knew from the R5 inventory that scheduled attacks in a run start about 27 minutes apart. The gap below is therefore justified from the detectors' code and R6's own earlier conventions only, and this protocol commits to **no further revision of the gap or any eligibility rule** (section 5).

## 1. What R6's 6-hour rule was doing, and why it conflates two jobs
R6's E2'(b) asked for 6 hours with no attack before an onset, and R6 also asked for 24 hours of verified stream (E2'(a)) *in which earlier attacks are allowed* (stratum S2). The 24 hours are the baseline-sufficiency requirement (a full day-night cycle after the 1-hour cold start). The gap before an onset has a different job: **make the onset a separate detection opportunity whose alerts can be attributed to it.** The 6 hours was not derived from any detector property; it was carried over from R5.

## 2. The new gap: 60 minutes, derived from the code and from R6's own conventions
Time constants, all read from the code (`backend/detection/config.py`, `ml/r2_detectors.py`, `backend/services/alerts.py`):
| quantity | value | where |
|---|---|---|
| longest scoring window (B: bytes/s and connections/min, A: 60 s window; v1: smoothing 60 s, drain 300 s) | **300 s** | `ml/r2_detectors.py`, `config.py` |
| an alert closes after the score stays below threshold − 0.08 for | 60 s | `ml/r2_eval.alerts` |
| a detector withholds judgment after a signal gap for | 60 s | `smooth_window_s` |
| decayed-peak hold half-life | 1 s | `FROZEN_CORRELATION` |
| FA-attribution guard after an attack end (R6 section 5, fixed before R6 ran) | **30 min** | R6 |

**Rule R7-E2b: at least 60 minutes between the end of the previous annotated attack of the same device and this onset.** It is the sum of two parts, each independent of any episode count:
1. **30 minutes**: the existing R6 guard, during which alerts are not counted as false alarms because they may be the previous attack's tail. It is already six times the longest window (300 s) plus the alert close time (60 s): any state the previous attack left in the scores has gone well before it ends.
2. **30 minutes** of clean pre-onset observation after that guard: six full 300 s windows, enough to see whether the detector sits at its baseline score before the attack (the pre-empted-episode test of R5/R6 section 5 needs that) and to count any false onset in that time as false.
An attack less than 60 minutes after the previous one is a **campaign continuation**: it is not an independent opportunity (an alert from the earlier attack may still be open or just closed), it is never evaluated as an episode, and its count is reported.
**Baseline contamination, stated honestly.** A and B have **no learning gate** (R2 specified none; the v1 detector, R1-recal, has one), so an earlier attack is learned as normal. Its weight is bounded by its length over the memory: 120 ticks (a 600 s attack) over 8,000 ticks (exponential window, v1 and A) = 1.5%, over 17,280 ticks (B's 24-hour store) = 0.7%, per attack still in memory. That is bounded, not zero; so **it is measured, not assumed away** (section 3).

## 3. Strata and the heterogeneity rule (replaces R6 section 4's strata)
By time since the previous annotated attack of the device: **S0** = 1 h to under 6 h; **S2** = 6 h to under 24 h; **S1** = 24 h or more, or none. All three are reported separately. A claim for a stratum needs at least 10 episodes in it.
**Heterogeneity rule, fixed now.** Compare recall by episode end in S0 with S1 ∪ S2 combined (device-cluster bootstrap, 95% interval of the difference). If the point difference exceeds **0.20** in either direction, the pooled claim D1 is withdrawn and only per-stratum statements are made (for strata with at least 10 episodes); otherwise the pooled claim stands and the strata are reported beside it. This is how the shorter gap is prevented from inflating or deflating pooled recall without anyone noticing.

## 4. What does not change, and what a shorter gap does not fix
- **Continuity stays strict.** E2'(a) (24 hours of *verified* continuous stream) and the whole stage-2 audit with its 5-minute boundary rule and 2-hour offline rule are unchanged. A shorter gap proves nothing about recording gaps.
- **Residual after-effects of a previous attack at 60 minutes** (for example a persistent ARP-cache change) cannot be excluded from metadata or from timestamps. They are handled only by the pre-empted rule and by the pre-onset false-onset count, and are listed as a limitation.
- A shorter gap admits attacks with less evidence of recent normal behaviour than R6 required. That changes the scientific question from "detection after a long quiet period" to "detection of a separate attack opportunity after at least an hour of quiet"; claims are worded that way.

## 5. Gate and the end of this dataset's use
Gate numbers unchanged: **test split** at least 30 eligible episodes, 8 sessions, 2 tools; stage 1 optimistic, stage 2 on the verified set. **If either stage fails, the detection part is not run and the UNSW attack collection is not revisited with another rule: further shortening of the gap, a different split or different exposure definitions are all excluded by this protocol.** The next step would then be a different dataset.

## 6. Order of work
1. Commit this protocol. 2. Commit the R7 audit script (R6's, with the three changes above and nothing else). 3. Run the stage-1 audit; commit and freeze its output. 4. Only if the optimistic gate passes: download the day files, run stage 2, commit the verified chains, exclusions and gate. 5. Validation scoring, thresholds, test file hashes, then the single test run (guard `data/results/R7_TEST_USED`). 6. Report every arm; freeze with a manifest and tag.
