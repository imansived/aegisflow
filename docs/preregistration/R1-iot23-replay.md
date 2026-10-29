<!-- Pre-registration R1. Committed before the replay adapter exists and before any held-out capture is downloaded or processed. -->

# R1 — replaying real device traces (Stratosphere IoT-23) through the unchanged research-v1 system

**Status: exploratory characterisation of a frozen system, not a hypothesis test.** The system under test is exactly the tag `research-v1`
(detectors, learned fusion, availability table, alert manager): nothing is tuned on these traces. Seven captures were already run once through an
earlier, cruder script (`ml/realdata_iot23.py`, constants standing in for the missing signals) and showed 12–76 false alert onsets per device-day,
so a poor false-alarm result is **expected, not a surprise**; the new parts are the faithful adapter, the availability and explainability checks,
and a held-out set. If the replay reveals a problem, `research-v1` stays as the original benchmark and any fix is a new version with its own protocol
and fresh evaluation.

## What a replay can and cannot say
IoT-23 has Zeek flow logs of real IoT devices (some infected with real malware) with per-flow labels. It supports **one** AegisFlow signal: network.
It contains **no location, no device-health counters and no proximity data.** So this replay says nothing about spatial, device or proximity
detection, nothing about cross-signal fusion, and nothing about the correlation window. It does test the network detector and the shipped
thresholds on data nobody designed them for, and it exercises the availability machinery for real, because three of four signals are genuinely absent.

## Adapter (flow log → telemetry)
| AegisFlow field | Source | Status |
|---|---|---|
| `timestamp` | Zeek `ts`, **preserved** (real epoch); one telemetry tick per 5 s from first to last flow, silent ticks included | observed (ticks derived) |
| `device_id` | the capture's busiest private-address host, mapped to `D-001` | derived |
| `bytes_sent`, `bytes_received` | sum of `orig_bytes`, `resp_bytes` of flows **starting** in the tick (`-` = 0) | observed, aggregated |
| `network_connections` | number of flows starting in the tick | observed, aggregated |
| `remote_endpoints` | distinct `id.resp_h` of those flows (max 100) | observed |
| latitude/longitude, battery/cpu/memory/temperature/uptime, nearby devices | **explicit null**: the dataset does not contain them | unavailable |
| ground truth | flow `label` (anything not `Benign` is malicious); never enters telemetry | evaluation only |

A tick with no flows is read as "observed, zero traffic" (a passive tap sees silence). That is an assumption: a capture outage would look the same.
Working-hours buckets use the site timezone, which means nothing for these devices (documented; not corrected).

## Captures
Inspected earlier with the old script (**development**): Honeypot-4-1 and -5-1 (benign), Malware-42-1, 20-1, 21-1, 8-1, 34-1.
Never processed (**held out**): Malware-3-1 and 1-1 in full, and the first 150 MB (cut at a line boundary) of 9-1 and 49-1. Results are reported
separately for the two groups.

## Conditions
**A. steady replay.** **B. intermittent loss on real data:** the network group is replaced by explicit nulls in bursts (20% of ticks, mean burst 12 ticks,
seeded) after the first 33 simulated hours' worth of ticks have passed or, for shorter captures, after 25% of the capture, to test the availability and
recovery states on real traffic.

## Measurements and what each answers
1. **False alarms.** Alert onsets per device-day on benign ticks after the detector has left cold start, at the shipped threshold for the network-only
   availability pattern (and, for reference, the old F1 threshold); alerts opened by the real alert manager per device-day. Reference line: the 3.0
   per device-day budget used throughout. *Excessive* = above 3.75 (1.25 × the budget).
2. **Distribution shift.** Per-tick network features of benign real ticks against ordinary simulator ticks (fraction of ticks with any flow; bytes sent,
   bytes received, connections: Kolmogorov–Smirnov distance and quantiles) and the detector's soft-score distribution on benign real ticks against ordinary
   simulated ticks.
3. **Availability and recovery.** Over every replayed tick: (a) a device whose location, device-health and proximity signals are null is never shown
   as Normal (state Degraded) and is judged by the validated network-only threshold, never the fallback; (b) in condition B, ticks with the network
   group null are blind (no signal at all), raise no alert, and are followed by a recovering stretch of one smoothing window (60 s) before judgment resumes;
   no alert is opened or kept open while blind or recovering; the false-alarm rate after recovery is compared with steady replay.
4. **Explainability.** For every alert the real alert manager opens: does its record name which signals were unavailable, does its reason text name the
   network feature(s) that actually moved, and, where several network features disagree (bytes high, connections ordinary), does the explanation list only
   those that moved? Reported as counts plus ten worked examples per group (true and false alerts), including whether the dashboard's alert card discloses that
   the device was partly observed.
5. **Ranking quality** (not a claim of detection): AUROC of the network soft score for malicious against benign ticks that have flows, and episode-level recall at the
   shipped threshold, on captures with at least 50 malicious ticks after cold start; captures where the device is infected from the first flow are reported as such.

## Not claimed, whatever the numbers
That the replay validates spatial, device or proximity detection, fusion, or the correlation window; that one device per capture is a fleet; that the
labelled flows are the only anomalies present; that 5-second aggregation is how a real device would report. Few benign device-days are available, so false-alarm rates
are estimates with wide uncertainty (reported with counts and a Poisson interval).

---

## Addendum 1 (written after the other held-out captures were replayed, before this one was downloaded)

The four planned held-out captures turned out to be almost entirely malicious from their first flow (9-1 and 49-1 heads and 3-1 and 1-1 have
0 to 0.13 benign device-days), so they cannot say anything about false alarms. One more held-out capture is added, benign by construction and
not previously seen: **CTU-Honeypot-Capture-7-1 / Somfy-01** (a real Somfy door lock, benign flows only). Everything else in this protocol is unchanged.
Given the small number of benign device-days overall, the false-alarm conclusion rests mostly on the development captures, which were seen before; this is
stated in the results.
