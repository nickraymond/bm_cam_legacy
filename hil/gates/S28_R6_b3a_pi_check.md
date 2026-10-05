# Sprint28 LADDER R6 (DRAFT) — B3a Pi check on bmcam004

B3a (Nick, 2026-10-05, via the EM): the Pi demosaics the 1600×900 crop to linear RGB (WB as a coding transform), encodes
cjxl VarDCT, the backend undoes it; crop unchanged, ≤ 195 messages; the 4-plane nrjxl path stays the fallback.
Question: does B3a run on the Pi Zero 2 W inside the encoder guard and the wake budget, round-trip at the backend, and
fall back loudly? Owner: Test Engineer. Host: bmcam004 / SPOT-31593C (has libjxl). Times UTC.
**Status: option (b) chosen by the EM 2026-10-05 ~22:45Z**, with R6 = the camera's B0/B1/B2
(`sprints/Sprint28_raw_jxl/DESIGN_B3a.md` §6 on feature/sprint28-b3a d694b6a). B3a ships behind `still.raw.layout`
(bayer4 default | rgb). Build target Wed ~16:00Z, backend decode Wed ~12:00Z; a slip past 16:00Z → R6 moves to pool week. Build = the B3a branch stacked
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

## Wed 10/7 timeline (option b; SPOT-31593C: a command lands at the :05 sync and applies at the NEXT boot)

| Z | step |
|---|---|
| Tue 17:00 window (optional, early) | **B0-encoder** on the Tue deploy: 10× `cjxl` VarDCT d 2.6 e5 on a 1600×900 RGB PPM (made from Tue's kept PGM, demosaiced on the Mac) under the real guard (`oom_score_adj 1000; ulimit -v 250 MB`), VmPeak / VmHWM / time per run; a 3-encode search. Needs only libjxl, not the B3a build → de-risks Wed |
| Wed ~16:30 | B3a build ready → one-window deploy at the **17:00 window**; `still.raw.layout=rgb` set in the local YAML in that window (applies at 18:00); B0 again with the real prep (supervisor RSS, prep time); backend: `encode_max_s=5` (applies 19:00) |
| 18:00 wake | **B1**: B3a still over the production lane → reassembled sha, backend decode + render, LinearRaw DNG |
| 19:00 wake | **B2 time** (`rfb=time`, pjpg complete); backend: reset encode_max_s + `d_max` low to force `floor` (applies 20:00) |
| 20:00 wake | **B2 floor**; backend reset (applies 21:00) |
| 21:00 wake | clean B3a wake (second B1 sample) |
| 22:00 window | RC deploy (development tip, EM-confirmed); layout=rgb on bmcam004 only if R6 PASSED |
B2 `fit` = desk only (logic shared with #120, covered by its tests) unless the camera session needs it on hardware.
B1 is a production-lane wake, not the console lane: the console lane needs Nick's OK (cmd.txt).
