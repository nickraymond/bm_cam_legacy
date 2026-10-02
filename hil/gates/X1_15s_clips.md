# X1 — 15 s video clips outdoors (post-exit experiment, NOT a release gate)

When: in the Tue 10/6 → Fri 10/9 slack, after G5 and the Mon 10/5 exit review. Owner: Test Engineer.
Asked by Nick (2026-10-02, via the EM): he wants 15 s clips; G3 only proved that 15 s is accepted and
recorded on the bench, not what it costs on the cellular link. Units: bmcam003 (SPOT-33507C), bmcam004
(SPOT-31593C), outdoors, production schedule (bus 10 min/hour, per_boot), nereus000 on the consoles.
Evidence: `runs/x1_15s_clips_<YYYYMMDD>/`. Baseline: the 5 s clips from G4/G5 (same units, same box).

## Question

Do 15 s clips (`video.send.duration_s: 15`) still complete within D1 (≤ 3 h, 0 redundant heals) on
the production schedule, and what do they cost relative to 5 s clips?

## Design (one variable)

- Change only `video.send.duration_s` 5 → 15 (keep `video.send.fps`, `size`, `message_cap`,
  framing, record fps at the G4/G5 values). Nick's UI also had record fps 14 / send fps 14: a
  separate run (X1b) if wanted, not mixed in.
- Same bus window, heal cap 24/day, Spotter firmware and placement as G4/G5.
- ≥ 12 production wakes per unit (12 h), so the count is comparable to G4.
- Send the change over the Sofar lane through the backend (`hil_sofar_change.sh`), as a customer
  would; revert the same way at the end.

## Measurements (per clip, both units; the same scripts on the G4/G5 runs = the baseline)

| metric | source | 5 s baseline | 15 s |
|---|---|---|---|
| messages per clip (planned / sent) | START `length`, END `sent_buffers` | G4/G5 | |
| burst time (s) | END `uart_duration_sec` | | |
| fits the 10-min window? | burst start + time vs window end | | |
| queue-full events per burst | console `MS_Q_CELLULAR_ONLY is full` between START and END+60 s | | |
| chunks missing at first look | backend media row after ingest | | |
| heal commands per clip | backend heal log | | |
| time to complete (capture → complete) | backend | | |
| redundant heals | backend heal log | 0 | |
| clips incomplete at 3 h | backend | 0 | |

## Pass / fail (for the decision, not the release)

| id | criterion | 15 s acceptable when |
|---|---|---|
| X1.1 | D1 completeness | 100 % complete ≤ 3 h, 0 redundant heals |
| X1.2 | window fit | every burst (+ its heals) ends inside the 10-min window |
| X1.3 | heal budget | heals/day ≤ 24 per Spotter (the production cap) |
| X1.4 | queue health | queue-full events per burst ≤ 2× the 5 s baseline |

Note (budget, from G3): 15 s at the default cap of 190 messages × 1.3 s/msg ≈ 250 s per burst
(5 s clips ~80 msgs ≈ 105 s). A 15 s clip at the same quality needs roughly 3× the bytes, so either the
cap truncates quality or the burst grows; the START `length` and the encode tries show which.

## Restore

`hil_sofar_change.sh X1.end <dev> '{"reset":["video.send.duration_s"]}'` per unit; confirm the next START
shows `d=5.0` and the reported hash equals the G5 hash.

## Not covered

Record-side fps changes (X1b), video quality judgement (needs Nick's eye on the clips), Iridium.
