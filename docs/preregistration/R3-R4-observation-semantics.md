<!-- Specification and acceptance criteria for R3 and R4. Committed before any implementation. These are operational-semantics and wording changes, not detection experiments. -->

# R3 and R4: what "not observed" means, and how an alert says it

**Status of the detector question.** R2 left detector effectiveness unresolved (false-alarm budget inconclusive, detection unmeasured on test data). R3 and R4 do
not try to resolve it and must not be read as improving detection. A separate detection experiment, on captures with a long benign baseline before the attack,
is a different protocol and is not started here. R2 stays sealed.

**Frozen, untouched:** `models/`, `docs/research/*` and everything their manifests hash (the `ml/` experiment code, results, protocols), the learned fusion, the
availability table. R3 and R4 change product code under `backend/`, `contracts/` where needed, and the frontend. They are a new product revision, not a
change to `research-v1`; their claim is about semantics, and the claim of "no change to detection" is itself tested (below).

## R3. Declared signal sets
Today a signal group that is null is "unavailable", whether the device never had it or lost it. A device that has only a network interface is therefore
Degraded for its whole life (R1 found exactly this).

**Model.** Each device may declare the signals it supports: a non-empty subset of `spatial, network, device, proximity`. No declaration = all four (existing
behaviour, so nothing changes for devices already enrolled). Per tick, per signal, exactly one status:

| status | meaning |
|---|---|
| **observed** | declared and reported this tick, judged normally |
| **unsupported** | not declared. Never reported on, never counts against the device. A value sent for an unsupported signal is ignored (not scored, not learned) and counted |
| **missing** | declared, reported null this tick |
| **recovering** | declared, reported again, but the detector withholds judgment while its window refills (11 ticks / 60 s after an outage, as today) |

**Rules.**
1. *Observability* is judged against what the device declared: `full` = every declared signal observed; `degraded` = some declared signal missing or recovering;
   `blind` = no declared signal observed. A network-only device with a healthy network signal is `full`, so its state can be Normal.
2. *Thresholds and scores are unchanged.* Unsupported, missing and recovering signals all hold the fusion column at 0 and use the validated threshold for the
   pattern of signals actually observed, exactly as before. R3 changes labels and state, not numbers. (The network-only threshold was validated on the simulator only;
   R1 showed it is badly calibrated for real traffic. That is R2's open question and is not touched.)
3. A *declared* signal that disappears behaves exactly as today: Degraded, `missing`, then `recovering`, the validated threshold or the conservative fallback.
4. Declaring is explicit and at registration (`POST /devices`, field `signals`); it is stored with the device and survives restart. Unknown signal names and an
   empty list are rejected.

**Acceptance tests (all must pass; none depends on a detector result).**
- R3-a. A device declared `network` only, with a healthy network signal and null elsewhere, is `full`, state is `normal` (not `degraded`), `unsupported` lists the other three, and `missing`/`recovering` are empty.
- R3-b. The same device, when its network signal goes null, is `degraded`/`blind` with `missing = [network]`, and 11 ticks after it returns `recovering = [network]` and `alertable` is False until judgment resumes.
- R3-c. A device with all four declared, location null: `degraded`, `missing = [spatial]`, as today.
- R3-d. **No change to detection.** For every tick of a scripted run, a device declared network-only and an undeclared device sent identical telemetry (the three other groups null) produce identical fused score, severity, pattern, `alertable` and alert events (apart from the new labels).
- R3-e. An unsupported signal sent non-null is ignored: the fused result equals that of the same run with it null, and the ignored count rises.
- R3-f. No declaration is identical to declaring all four. Existing tests (`tests/test_availability.py` and the rest) pass unmodified.
- R3-g. The declaration persists in the store, is returned by `GET /devices`, is rejected when empty or unknown, and `Runtime` restores it after restart.

## R4. Partial-observation wording
Every alert's explanation says what evidence it rests on. The wording is generated from the same sets as the alert record, never typed separately.

**Content.** After the factors, a line (or two) built from the peak moment's sets: which signals are **unsupported** ("not supported by this device"), **missing**
("stopped reporting"), **recovering** ("recovering after an outage; judgment withheld"), then "This alert is based on <observed signals> telemetry only." The
alert record gains `unsupported`, `missing`, `recovering` and `observation` (the sentence), beside the existing `unavailable` (everything not observed).
A fully observed alert says that all declared signals were observed. Names used: location (spatial), network, device-health (device), proximity.

**Rules.** Missing signals are never described as evidence for an anomaly: wording is neutral and always includes that missing signals neither confirm nor rule
out an attack. The text for an alert agrees with its record.

**Acceptance tests.**
- R4-a. For each of a set of availability scenarios (full; network-only; location lost; proximity lost; network-only plus lost network recovering; device-health lost and location recovering), the observation line names exactly the signals in the record's `unsupported`, `missing`, `recovering` and the observed signals, and no others.
- R4-b. The text contains "based on <observed> telemetry only" iff something is not observed, and the observed list in the sentence equals the signals not in `unavailable`.
- R4-c. For all scenarios the explanation never contains "confirm" without "neither confirm nor rule out", and never lists a missing signal among the contributing factors.
- R4-d. The explanation line is produced from the record: building it from the `Fused` result and from the stored alert event gives the same text.
- R4-e. Existing explanation tests keep passing (full-observation wording of the factors is unchanged).

## Not claimed
That declared sets improve detection, calibrate the network-only threshold, or make a partly observed device safe; that a device cannot lie about its declaration
(an operator declares it; a compromised device that stops reporting a declared signal is Degraded, but one that was never declared is not watched for it); that the dashboard
wording is understood by operators (no user study). Demo data and the simulator fleet keep declaring all four signals.
