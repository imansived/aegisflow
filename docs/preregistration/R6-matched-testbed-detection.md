<!-- Pre-registration R6. Committed before the continuity audit ran, before any 2018 capture was downloaded or opened, and before any R6 code scored anything. -->

# R6 — detection and false alarms in the same testbed, on one continuous stream per device

## 0. Why R6, what is locked, what R6 may not use
R5 could not measure detection (feasibility gate failed: no 6-hour clean prefix inside one day file) and measured false alarms on a different testbed (2016, 23 devices) from the attacked one (2018, 10 devices). R6 asks the detection question where both live in the **2018 testbed**, on the **same devices**, with a baseline that is a *verified continuous* recording and not an assumed one.
- **Frozen, unchanged:** `research-v1`, R1, R2, R3/R4, R5 (`inventory-r5`, `fa-r5`) and their manifests. No detector, constant or threshold rule changes: the arms are R2's (**A** zero-inflated, **B** windowed-rate, **R1-recal** the v1 detector re-thresholded), **A is the designated primary**, nothing is tuned.
- **Not used:** R5's test outcomes are not used to choose anything in R6. No 2016 data appears. The sealed R2 data stay sealed.
- **Erratum to R5 (disclosed here, not edited there).** The R5 inventory script's availability/size fields (`capture_files_needed`, `csv_head`) read 404 for every file because the script's HTTP HEAD request was rejected for its default User-Agent; the files exist. The R5 gate used the schedule and the date listings, not those fields, so the R5 gate and results are unaffected. `docs/results/r5_inventory_erratum.md` records it; R6's own script sends a browser User-Agent and its sizes are reported.

## 1. Question
In the 2018 testbed, with thresholds set on validation periods at a 3.0 false onsets per device-day budget, how often and how quickly are scheduled attacks detected, and does the false-alarm budget hold **in the same streams in which the attacks are detected**?

## 2. Material
**Devices:** the 10 devices of the testbed's device table (5 monitored 2018-05-28 to 06-17, 5 monitored 2018-09-24 to 10-26). **Day files:** the dataset's two lists, *benign* (27 dates) and *AttackAndBenign* (17 dates); a file's date is its label. Together they cover every date of the May cohort's range from 05-28 and of the October cohort's from 10-10 (file sizes 0.1 to 4.1 GB, 68 GB in all). **Attacks:** the per-device schedule (epoch start and end of each scheduled attack, tooling-written: Tier A under R5's E1).
**One stream per device.** Each device's day files in date order form one stream. The detectors run continuously through the stream, so a day's baseline is whatever the detector has learned from the days before it. Which day's ticks are *scored* is decided by the split (section 4); learning is not paused at split boundaries.

## 3. Continuity: what is checked, and what cannot be
**Stage 1, metadata only (this protocol's audit script).** For each device: the monitoring row (one MAC and IP for the whole range), the dates for which a file exists, gaps in the date sequence, and the adjacency of benign and attack files. **Not verifiable from metadata, stated as assumptions until stage 2:** no recording gap at a file boundary; the device was not reset or reconfigured; benign-list and attack-list days were recorded under the same conditions (for example, the attacking hosts being present); the benign-list days contain no unlabelled attack.
**Stage 2, after the protocol, the audit and the split are frozen and before any score exists: timestamps and Ethernet addresses only.** For each file: first and last packet time. For each device and file: packet-time gaps. No payload, no ports, no scores, no attack-window analysis. Rules fixed now:
- a **chain break** between two consecutive day files if the gap between the last packet of one and the first of the next exceeds **5 minutes**, or any silent gap of the whole capture exceeds 5 minutes inside a file; a stream restarts after a break;
- a device with a silent gap longer than **2 hours** inside the 24 hours before an onset is treated as possibly offline: that episode is excluded;
- a device's MAC absent from a file excludes its stream for that file's dates.
Episodes and exposure lost to stage 2 are counted and reported, and **the gate below is re-applied to what remains.** Stage 2 cannot add anything.

## 4. Eligibility, strata, split
**Episode eligibility (R5's E1, E3, E4 unchanged; E2 replaced by E2'):**
- **E2'(a)** at least **24 hours of verified continuous stream** end at the onset (chain start at least 24 h before it; the first 2 hours of any chain are burn-in, never scored);
- **E2'(b)** no annotated attack of that device in the **6 hours** before the onset;
- **E2'(c)** the offline rule above.
Attacks earlier than 6 h before the onset may be in the baseline. Episodes are therefore **stratified: S1** = no annotated attack of the device in the preceding 24 h (baseline completely unattacked); **S2** = at least one attack 6 to 24 h before (baseline carries earlier attacks, which the detectors' learning gate only partly resists). Both strata count; they are always reported separately, and a claim is made for a stratum only if it has at least 10 episodes.
**Split by capture, fixed before any scoring.** The unit is the day file label `R6/<yy-mm-dd>`; `sha256` of the UTF-8 string, first 8 bytes big-endian, mod 3: **0 = validation, 1 and 2 = test**. There is no development split (nothing in R6 is developed). Everything on a day goes with that day: the day's benign exposure, its attack-free exposure, its episodes. Thresholds use only validation days; claims use only test days.

## 5. What counts as a false alarm and what as detection
- **Benign exposure (two kinds, both counted per device-day after burn-in):** **F1** benign-list days of the device; **F2** the attack-free part of attack days, defined from the schedule alone as ticks outside every window `[onset − 60 s, end + 30 min]` of that device. A false onset is a rising edge of score ≥ threshold in F1 or F2 time. F1 and F2 are reported separately and pooled; the claim is on the pooled test exposure.
- **Detection** follows R5: Tier A onset to the first tick with score ≥ threshold; no lead credit; an episode already at or above threshold on its onset tick is **pre-empted** (excluded, counted, with a both-ways sensitivity); undetected episodes are **censored** at the episode end; recall within 60 s, 300 s and by episode end (600 s); the restricted mean time-to-detection with **τ = 300 s** (no episode has more than 600 s of follow-up) and a **30 s** margin; the median is reported, not decided on.
- **Alert-time bookkeeping:** an alert opens at an onset and closes when the score has stayed below (threshold − 0.08) for 60 s, as in R2/R5.

## 6. Thresholds
Per arm, on **validation days only** (F1 + F2 of validation days), the smallest threshold on the 0.005 grid with at most **3.0** false onsets per benign device-day. **If the smallest such threshold for an arm is ≥ 0.995, that arm is declared not calibratable** (R5: R1-recal landed on 1.000 and then almost never alerted). A non-calibratable arm is reported, is excluded from D2 and B, and its false-alarm "pass" is labelled vacuous.

## 7. Claims and decision rules (fixed now)
Uncertainty: **device-cluster bootstrap (10,000)**, resampling devices and carrying their sessions: with 10 devices the intervals are honestly wide. A session-cluster bootstrap is reported as a sensitivity analysis.
- **F, false-alarm budget (A).** Requires at least **100** pooled test benign device-days after burn-in. PASS if the upper 95% bound is at most 3.75; INCONCLUSIVE if the point estimate is at most 3.0 and the bound is above it, **or if exposure is below 100**; FAIL otherwise. The expected test exposure is computed from the frozen split before anything is scored (section 9) and reported with the protocol's audit.
- **D1, useful at this budget (A).** Pooled test recall by episode end: **PASS** if the point estimate is at least **0.50** and the lower 95% bound at least **0.30**; **INCONCLUSIVE** if the point estimate is at least 0.50 but the lower bound is below 0.30; **FAIL** if the point estimate is below 0.50.
- **D2, not worse than recalibration** (only if R1-recal is calibratable): recall no more than 0.10 below, restricted mean time-to-detection no more than 30 s longer, paired bootstrap.
- **B, better than recalibration:** only if F and D2 pass and A's false-alarm rate is below R1-recal's with a paired interval excluding zero.
- **J, the joint statement "the system detects attacks at an acceptable false-alarm rate in this testbed":** supported only if **F is PASS and D1 is PASS**, both measured on the same test streams. Otherwise the claim that failed or is inconclusive is what is reported.
- B (windowed-rate) is reported on the same rules; no winner is selected on test data.
- **Pre-declared sensitivity for unlabelled attacks in benign periods:** per-device-day false-onset rates, per-device totals, and an estimate with the top 5% of device-days removed. Claim F stands or falls on the untrimmed estimate; the trimmed one shows how much of it a few days carry.

## 8. Feasibility gate (kept, tightened to the test split)
Applied to the **test-split** episodes: at least **30** eligible episodes, in at least **8** (device, day) sessions, from at least **2** attack tools, and at least 10 per stratum for a stratum claim. Applied twice: stage 1 counts under the optimistic assumption that the audited date sequence is contiguous (if even that fails, stop); stage 2 re-applies it to the verified set (if it fails, the detection part is not run and the result is "insufficient eligible data"). Eligibility rules are not relaxed after any result.

## 9. Order of work (nothing is chosen on test data)
1. Commit this protocol, the erratum and the stage-1 audit script. 2. Run the audit: continuity calendar, E2' status, strata, split, expected exposure, stage-1 gate; commit its output. **Freeze the split.** 3. Download the day files; stage 2 (timestamps and addresses only); commit the verified chains, exclusions and the stage-2 gate. 4. Score the validation days; set thresholds; commit them and the code; record SHA-256 of the test files. 5. Score the test days **once** (guard `data/results/R6_TEST_USED`). 6. Report every arm, including the ones that lose; freeze with a manifest and tag.

## 10. Not claimed
That performance holds on other testbeds, device types or attack types (all attacks here are scheduled network attacks of a fixed list of tools); that benign-list days are free of unlabelled attacks, or that the testbed resembles a real fleet; detection by location, device health or proximity; that a device compromised before monitoring would be detected; that results transfer to longer baselines than the 24 h-to-days available. The operator-wording and declared-signal features (R3/R4) change no number here.
