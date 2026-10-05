# Sprint28 LADDER R6 (DRAFT) — B3a Pi check on bmcam004

B3a (Nick, 2026-10-05, via the EM): the Pi demosaics the 1600×900 crop to linear RGB (WB as a coding transform), encodes
cjxl VarDCT, the backend undoes it; crop unchanged, ≤ 195 messages; the 4-plane nrjxl path stays the fallback.
Question: does B3a run on the Pi Zero 2 W inside the encoder guard and the wake budget, round-trip at the backend, and
fall back loudly? Owner: Test Engineer. Host: bmcam004 / SPOT-31593C (has libjxl). Times UTC.
Status: DRAFT; the EM picks option (a) or (b) once the camera/backend estimates land. Build = the B3a branch stacked
on #120/#133 (sha to be filled in). The Tue keep_crop capture (R4 follow-up) is also the real-IMX708 input for B3a.

## Criteria

| id | PASS when | evidence |
|---|---|---|
| R6.1 memory | every B3a wake: the encoder runs under `ulimit -v 250 MB`; cycle log VmPeak and peak RSS recorded; 0 OOM / guard kills | cycle log `[RAW]` lines, dmesg |
| R6.2 time | demosaic + encode × attempts (≤ 3) recorded per wake; wake→halt < 570 s every wake | cycle log timings, wake report |
| R6.3 round-trip | every B3a still is complete at the backend and decodes to a display JPEG (`render_state renderable`); the inverse (WB / colour transform) applied, image not tinted vs the paired pjpg | `/media/{id}`, display JPEG, Nick's eye on one sample |
| R6.4 loud fallback | one forced-failure wake (backend `still.raw.encode_max_s=5` → `rfb=time`) sends the pjpg with the reason; 0 silent | START `fmt=pjpg rfb=time`, media row |
| R6.5 no regression | START every wake; heals as usual; RSS / time not worse than the 4-plane path on the same scene ± 10 % (measured, not gated if B3a is intrinsically heavier) | R4 table as the 4-plane baseline |

## Option (a): B3a is the RC still path

R6 = the first ~6 RC wakes on bmcam004 (no extra rig time) + one forced-fallback wake near the end of day 1 (counts
as an injected fallback, excluded from RC.5's ≤ 2/24). R6.1–R6.3 read from the same artifacts as the RC.

## Option (b): B3a is not in the RC

Wed daytime on bmcam004 production wakes: one-window deploy of the B3a build as soon as it exists, ~5 wakes + 1 forced
fallback, read-only log pull in one window; then the RC deploy at the Wed ~22:00Z window → 24 RC wakes end Thu ~22:00Z
(tight, fits Fri).

## Precondition

SPOT-31593C's uplink must be draining (external event X1, 2026-10-05 from ~20:16Z): config changes and the forced
fallback travel the Sofar lane, and R6.3 needs the stills at the backend.
