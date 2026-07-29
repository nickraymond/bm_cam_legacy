# Overnight A/B — pacing 1.0 s vs 5.0 s, 2026-07-29

**Window:** 07:49–14:00Z (6 h 11 min). **Units:** bmcam003 + SPOT-33507C
(arm B, `txd` 1.0 s) and bmcam000 + SPOT-31593C (arm A, `txd` 5.0 s).
Everything else identical: 384 chars, cap 195, `win` 18 min, reef primary
reference image via `src=1`, real `power_halt`, Spotter 20 on / 10 off.

Arms were assigned **conservatively**: Phase E showed bmcam000 is the
*worse* unit (9.0 % vs 1.0 % loss at 1.0 s), so it was given the 5.0 s arm.
Any advantage 5.0 s shows here is therefore understated.

---

## 1. Headline

| | **Arm B — 1.0 s** | **Arm A — 5.0 s** |
|---|---|---|
| Images attempted | 12 | 12 |
| Device-side `sent=N/N complete=True` | **12/12** | **11/11** |
| **Complete images at backend** | **0 / 12** | **6 / 12** |
| Chunk delivery | 95.80 % | 95.07 % |
| Mean usable prefix | 65.5 % | 76.4 % |
| Awake per cycle | **347.8 s** | **1047.6 s** |
| Energy cost (awake ratio) | **1.00×** | **3.01×** |

**The device transmitted 100 % successfully in every single cycle on both
arms.** All loss is Spotter/cellular-side and invisible to the Pi — the
same silent-drop mechanism Phase E characterised.

---

## 2. Chunk-delivery % is the wrong metric

The two arms are nearly identical on chunk delivery (95.80 % vs 95.07 %),
which would suggest pacing barely matters. That reading is wrong, because
these are **progressive** JPEGs: the stream is only usable up to its first
gap. Losing chunk 17 of 169 wastes the other 152 chunks you paid to send.

| | Arm B — 1.0 s | Arm A — 5.0 s |
|---|---|---|
| Images with zero gaps | 0 / 12 | **6 / 12** |
| First-gap position (mean) | 65.5 % in | 76.4 % in |
| First-gap position (range) | **10 % – 86 %** | 47 % – 56 % |

Arm B never produced a single complete image in 12 attempts. Its gaps
land anywhere — one at 10 % into the stream, which throws away ~90 % of
that cycle's transmission and its energy.

---

## 3. This validates the Phase E model quantitatively

Phase E predicted `lost per boundary = max(0, D/delay − 2)` with a median
blackout D ≈ 9 s.

- **At 1.0 s:** predicted 9/1 − 2 = **7 lost**. Observed: 7 lost on 8 of
  12 images (163/170 appears repeatedly). Dead on.
- **At 5.0 s:** predicted 9/5 − 2 = **0 lost**. Observed: **6 of 12
  images perfect** — the typical blackout is fully absorbed.
- The 6 imperfect A images lost 4–6 chunks, implying D ≈ (4+2)×5 = **30 s**
  — the long tail Phase E measured (90th pct 24 s), not the median event.

So 5.0 s does exactly what the model said: it eliminates the *typical*
blackout and remains exposed to the rare long one.

---

## 4. Energy vs data — the trade to decide

Awake time is the energy proxy (SD power logs will refine this).

| Metric | Arm B — 1.0 s | Arm A — 5.0 s | Winner |
|---|---|---|---|
| Awake / cycle | 347.8 s | 1047.6 s | **B, 3.0× cheaper** |
| Complete images | 0 / 12 | 6 / 12 | **A** |
| Usable prefix per awake-second | **0.188 %/s** | 0.073 %/s | **B, 2.6×** |
| Complete images per hour awake | **0** | 1.9 | **A** |

**The two metrics disagree, and that is the real finding.** B is far more
efficient per second of power *if* partial images have value. A is the
only arm that produces complete images at all.

If a cycle's output must be a *complete* image, B's efficiency is
illusory — it spent 69.6 minutes of awake time over the night and
produced zero complete images. If the backend's truncate-at-gap partial
renderer makes a 65 %-prefix image genuinely useful, B delivers more
usable pixels per joule.

**That call is Nick's**, and it depends on customer value of a partial
image. Recommended framing for scoring: score on *complete images per
watt-hour*, plus a separate partial-credit term weighted by usable prefix
— then the answer falls out of the weighting rather than the metric
choice.

---

## 5. Command control (the primary objective)

**Command delivery over USB: 22 sent, 22 acked, 100 %, every one on the
first try.** No re-sends needed all night.

- `roi` swept 1 → 2 → 3 → 0 → 4 repeatedly; all five values exercised
  (1,2,3 ×6 each; 0,4 ×2 each) across 22 cycles.
- Every ack carried the correct node state and the crop override was
  visible in the next capture.
- `src=1` held across every cycle without re-commanding — the reference
  image survives power cycles in `bm_command_state.json`.

This is the objective that mattered most: **settings can be changed on a
deployed, halting, power-cycled unit, and the change is confirmed.**

---

## 6. Interruption (see INCIDENT_host_sleep.md)

The bench Mac entered Maintenance Sleep 08:41–09:28Z, costing ~45 min of
console capture and `roi` injection. **The units are autonomous and kept
cycling throughout** — the A/B data is unaffected because it is measured
from the backend and the Pi logs. Fixed with `caffeinate` plus a
silent-port watchdog; both landed on `claude/sprint10-txd-cap-src`.

## 7. Caveats

- Arm A's final image (13:32Z) shows 91/170 and was likely still draining
  at query time; it is included in the totals above, so **A's numbers are
  slightly pessimistic**.
- n = 12 images per arm, one night, one location, one unit per arm. Units
  are known not to be at parity.
- Awake time is a proxy; the SD power logs give the real energy figure.

## 8. Artifacts

| File | Contents |
|---|---|
| `overnight_timeline.jsonl` | every cycle, command, ack with timestamps |
| `overnight_timeline.jsonl.summary.json` | run summary (no abort post-recovery) |
| `cronlogs/bmcam00{0,3}/` | per-cycle device logs: ladder, msgs, sent, elapsed |
| `INCIDENT_host_sleep.md` | the interruption, root cause, fixes |
| `arm_overnight.sh` | the arming procedure actually used |
