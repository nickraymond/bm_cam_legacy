# Sprint11 — Phase-aware transmit: fast pacing, low energy, complete images

**Status:** proposed 2026-07-29 (Nick-directed, following Sprint10 Phase E +
the overnight A/B). **Branch:** `feature/sprint11-phase-aware-transmit`.
**Base:** `development`.

---

## 1. The problem, stated from measurement

Sprint10 established that image-chunk loss is **not** random link loss. It is a
**periodic, wall-clock-scheduled event**:

- Blackouts land on an **absolute 5-minute grid** — 83 % of measured gaps start
  within 30 s of a 5-minute boundary, median offset **+1 s**.
- Each blackout lasts **D ≈ 9 s** (median; 16.5 s at the 75th pct, 24 s at the
  90th). During it the Spotter's **2-slot** cellular queue rejects every submit,
  silently: the Pi logs `sent=N/N complete=True` every time.
- Loss follows `lost per boundary = max(0, D / delay − 2)`. Cross-validated:
  modal loss 7 @ 1.0 s and 4 @ 1.5 s both imply D = 9.0 s.

The overnight A/B (2026-07-29, 12 images per arm) showed what that costs:

| | arm B — 1.0 s | arm A — 5.0 s |
|---|---|---|
| Complete images at backend | **0 / 12** | **6 / 12** |
| Chunk delivery | 95.80 % | 95.07 % |
| First-gap position | 65.5 % in (range 10–86 %) | 76.4 % in |
| Energy (measured) | **0.1797 Wh/cycle** | 0.2256 Wh/cycle |

**Chunk-delivery % is a trap.** The arms look identical on it, but these are
*progressive* JPEGs — a stream is only usable to its first gap. Arm B never
produced one complete image in twelve attempts.

### The insight this sprint is built on

**At 1.0 s an entire image fits between two boundaries.** 194 messages × 1.0 s
= 194 s; the grid gives 300 s. We do not need to survive the blackout — we need
to *miss* it. And we can, **open-loop**: the cycle already reads UTC from the
Spotter over the BM bus (`[CMD] spotter UTC decoded: …`), so grid phase is just
`epoch mod 300`. No USB, no backend, no feedback path.

### Why today's timing is the worst possible phase

The bus window opens *on* a grid boundary (`alignmentInterval5Min: 1`). Then
boot ~55 s + **90 s listen** + capture/encode ~5 s puts transmit start at
**~:03:10**, so a 194 s burst runs straight through **:05:00** — landing the cut
around 62 % into the image. Measured first-gap mean was 65.5 %. The 90 s listen
window, chosen arbitrarily, is what pushes every image onto the boundary.

---

## 2. What changes (four separable pieces)

### C1 — Capture-first ordering; delete the pre-capture listen window
Boot → time-sync → **capture** → encode → transmit. Commands are applied on the
**next** boot, from cached settings. This is already the house pattern (`win`
takes effect next cycle; D15 acks on persist), so it is a simplification, not a
new contract.

Measured effect (n = 119 real camera cycles: `native ready` median **94.6 s**,
of which 90 s is the listen and ~3 s time-sync — **capture + encode is ~5 s**):

| | today | capture-first |
|---|---|---|
| Transmit starts | :03:10 | **:01:00** |
| Transmit ends (194 @ 1.0 s) | :06:24 | **:04:14** |
| :05:00 boundary | hits at ~62 % | **never reached, 46 s clear** |

Degradation is graceful: +60 s of unexpected delay still puts the boundary at
**92 %** into the stream — a tail nick, not a mid-image cut.

### C2 — Phase-aware transmit scheduling
Before transmitting, compute time to the next boundary. Transmit only inside
**`[boundary + 30 s, next boundary − 20 s]`** — a 250 s clean lane (guards sized
from the measured D distribution; 30 s covers ~90 % of the tail). If the burst
does not fit the remaining lane, wait for the next lane rather than start into a
boundary.

Feasibility, which doubles as the config rule:

| Pacing | Messages that fit one lane |
|---|---|
| **1.0 s** | **250** |
| 1.25 s | 200 |
| 1.5 s | 166 |
| 2.0 s | 125 |

At 170–194 messages, **only 1.0 s (and 1.25 s) fit**. This is the argument
against splitting the difference on pacing.

If the clock read fails, **fall back to today's behaviour** — a wrong phase is
no worse than now; silently trusting a bad clock would be.

### C3 — Deferred acks
An ack is an uplink message entering the same 2-slot queue. Acking mid-transmit
adds exactly the collisions we are pacing to avoid (incident 001; the "scattered
singles" mode). Queue acks during the burst, flush after the image completes.

### C4 — Bounded post-transmit listen tail
Finding 006 is specific: the mailbox drain is triggered by the sync **our own
transmit** initiates, and fires **1–4 min after the cycle ends** — which is why
bmcam000 took 0/10 commands during the soak. A 194 s transmit covers only the
front of that window. Add a **bounded 120–180 s listen tail before the halt**.
Cost ≈ 0.017 Wh at ~0.5 W.

The pre-capture window was listening at the one time commands never arrive.

---

## 3. Bus-window resize (config, ships with this sprint)

The bus stays powered for the whole window regardless of when the Pi halts, so
the **halted-Pi baseline of 0.424 W is 79 % of arm B's entire cycle energy**.
Right-sizing the window is a bigger lever than pacing ever was — and it costs no
delivery, because the Pi is already halted through that span.

Measured by integrating only the first N minutes of arm B's twelve real
on-windows:

| Arm B bus window | Wh/cycle | vs 20 min | vs arm A (5.0 s) | Wh/day (48 cycles) |
|---|---|---|---|---|
| 20 min (today) | 0.1797 | — | −20.4 % | 8.63 |
| **15 min** | **0.1443** | −19.7 % | **−36.0 %** | 6.93 |
| 10 min | 0.1090 | −39.4 % | −51.7 % | 5.23 |
| 8 min | 0.0948 | −47.2 % | −58.0 % | 4.55 |

With C1+C2 a 1.0 s cycle needs ~5–7 min awake, so **15 min leaves ~8 min of
margin** — safe, with room to tighten to 10 min later once the timing is
confirmed in the field.

---

## 4. The validation test (6 h, both units, over USB)

Same reef reference image (`src=1`, primary) and the same `roi` sweep
(1 → 2 → 3 → 0 → 4, repeating) on both units, injected over the Spotter USB
console — the sweep exists to prove a commanded change **takes hold on the next
cycle**, which is the whole point of the capture-first reordering.

| | **Unit A — the candidate** | **Unit B — production-ish control** |
|---|---|---|
| `image_transmit_delay_seconds` | **1.0 s** | **5.0 s** |
| Listen window | **removed** (C1) | **removed** (C1) |
| Phase-aware scheduling (C2) | **on** | off |
| Deferred acks (C3) | **on** | off |
| Post-transmit listen tail (C4) | **on**, 150 s | off |
| Spotter schedule | **15 on / 15 off** | **20 on / 10 off** |
| `image_buffer_size` / `message_cap` | 384 / 195 | 384 / 195 |
| Boot settle | 0.5 s | 0.5 s |

Unit B removes the 90 s listen as well — that value was an error, and keeping it
in the control would only preserve the bug.

### What we compare (Nick, 2026-07-29)

1. **How many complete images** per device (START/END + zero gaps — *not* chunk %).
2. **Actual energy per cycle** per device, integrated from the Spotter SD
   (`bm/<bridge-node>/*_power.log`, addr-65, 60 s means — see the
   `nereus-spotter-sd-analysis` skill).
3. **Did ACKs arrive when expected** — and did the commanded `roi` take effect on
   the following cycle.

### Success criteria

- **Unit A delivers materially more complete images than 0/12.** The model
  predicts the periodic loss goes to zero; the residual is the sporadic
  population, so the honest target is *most* cycles clean, not all.
- **Unit A uses less energy per cycle than Unit B** — predicted ~0.144 Wh vs
  ~0.226 Wh (−36 %).
- **Every commanded `roi` is acked and visibly applied on the next cycle.**
- No regression in device-side completion (`sent=N/N complete=True`).

---

## 5. What this does NOT fix

Phase alignment removes the **periodic** population only. The **sporadic**
sync-session blackouts (17 % of gaps, unaligned, 6–36 s) remain — and **1.0 s is
more exposed to them than 5.0 s**: a 20 s sporadic event costs ~18 chunks at
1.0 s versus ~2 at 5.0 s. Arm A's six imperfect images were all this population.

Open-loop mitigations, in order of value per effort — **not in scope for this
sprint**, listed so the next one has them:

1. **Put the risky time at the tail** — deliberately place the base layer deep
   inside the clean lane so any damage lands where progressive JPEG tolerates it.
2. **Duplicate the head** — re-send the first ~15 base-layer chunks at the end;
   a few percent of messages makes head-truncation (the image-destroying case)
   essentially impossible.
3. **Split-burst with a boundary pause** for images that cannot fit one lane:
   send to boundary − 20 s, pause ~40 s, resume. Fixed ~40 s per crossing versus
   losing 7+ chunks.

---

## 6. The structural ask (Sofar / Blues — DEV_LOG Q12)

Any one of these dissolves the problem and makes everything above unnecessary:

1. Can the 5-minute sync/alignment event be **pinned, deferred, or disabled** so
   it never lands inside a transmit?
2. Is the **2-slot cellular queue depth configurable**? `max(0, D/delay − slots)`
   goes to zero at 1.0 s if slots go 2 → 8.
3. Is there a **backpressure signal** the Pi can read? Drops are silent to the
   sender today.
4. Why does blackout duration vary 9 → 36 s, and does it track signal strength?
