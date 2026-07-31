# Sprint11 kickoff prompt (paste into a fresh session)

Copy everything below the line.

---

You are a worker session on the BM camera project (repo `bm_cam_legacy`).
Your job is **Sprint11: phase-aware transmit** — make the fast 1.0 s pacing
land complete images by scheduling transmits around a 5-minute wall-clock
blackout grid, while cutting energy via a shorter bus window.

**Read first, in this order:**
1. `CLAUDE.md` (repo root) — note the Branching Model.
2. `sprints/Sprint11_phase_aware_transmit/SPEC.md` — the plan.
3. `sprints/Sprint11_phase_aware_transmit/DESIGN.md` — D1–D10, the decisions
   and the evidence behind each. Do not re-litigate these from taste.
4. `sprints/Sprint11_phase_aware_transmit/TRACKER.md` — your checklist.
5. Evidence: `runs/sprint10_phaseE_20260728/RESULTS.md` (the blackout model)
   and `runs/sprint10_overnight_20260729/RESULTS.md` (A/B delivery + energy).

**The one-line reason this works:** at 1.0 s a whole image (194 msgs × 1.0 s =
194 s) fits inside one 300 s gap between blackouts, and the cycle already reads
Spotter UTC over the BM bus — so the fix is open-loop, which is required
because field units have no USB and no backend feedback.

## Hardware

- bmcam003 — `pi@100.103.35.24`, Spotter SPOT-33507C, bridge node
  `c3c564b91856226c`, camera node `53171fa3d81a8e6f`
- bmcam000 — `pi@100.119.14.92`, Spotter SPOT-31593C, bridge node
  `0e582dd12c1e1480`, camera node `49cfe4d7cceb2771`

**Both units are still in Sprint10 test configuration** (reference image via
`src=1`, test pacing, armed cron + real `power_halt`, Spotters continuously
cycling). Confirm their actual state before changing anything.

## Build (TRACKER §1–§4)

C1 capture-first · C2 phase-aware scheduling · C3 deferred acks ·
C4 post-transmit listen tail. Land them separately with tests; the fake-clock
tests for C2 matter most, especially the **clock-read-failure fallback** — that
is the path that fails silently in the field.

## Then run the 6 h validation (TRACKER §6)

| | Unit A = bmcam003 | Unit B = bmcam000 |
|---|---|---|
| `image_transmit_delay_seconds` | **1.0 s** | **5.0 s** |
| Listen window | removed | removed |
| C2/C3/C4 | on | off |
| Spotter schedule | **15 on / 15 off** | **20 on / 10 off** |
| size / cap / source | 384 / 195 / `src=1` reef primary | same |

Sweep `roi` 1 → 2 → 3 → 0 → 4 over the Spotter USB console, same value to both
units each cycle — the sweep exists to prove a commanded change takes hold on
the **next** cycle. `tools/overnight_ab_runner.py` already does this.

**Compare exactly three things:**
1. **Complete images** per device (START/END + zero gaps — *never* chunk %, D8).
2. **Measured energy per cycle** per device, from the Spotter SD.
3. **ACKs when expected**, and the `roi` visibly applied the following cycle.

Baseline to beat: **0/12** and **6/12** complete images; **0.1797** and
**0.2256** Wh/cycle.

## Bench gotchas that have already cost time

- **Start `caffeinate -dimsu` before anything else.** The Mac slept mid-run on
  07-29 and stalled all console capture for 45 min.
- **Deploy the whole manifest** (`tools/deploy_rc_runtime.sh`), never individual
  files — a piecemeal copy caused a `media_gid` TypeError mid-cycle.
- **`bm cfg …` fails SILENTLY.** Use `bridge cfg set <bridge_node_id> s u <key>
  <val>` + `bridge cfg commit <node_id> s`, and **always read the value back**.
- **`pkill -f <pattern>` over SSH matches the remote shell's own command line.**
  Use `[r]c_…` bracket patterns or you will SIGTERM your own session.
- **Acks are hex-only on the console** — decode the `[BM_TX] Message:` hex dumps;
  a text grep for `"ok":1` finds nothing.
- **macOS has no `timeout`.** And bash 3.2 has no associative arrays.
- A halted Pi on a continuously-powered bus never comes back (finding 004).
- Energy: use the **bridge** node's addr-65 trace, not the camera node's — the
  two Spotters differ in `transmitAggregations` (1444 vs 13 samples). Use the
  `nereus-spotter-sd-analysis` skill; Nick uploads SD cards to
  `~/Downloads/<date>_SD_Upload/<SPOT-ID>/`.

## Watch for (D4)

`rc_run_capture_cycle.sh` boot settle was cut **30 s → 0.5 s**. If you see UART
open failures, missed time-sync, bridge-not-ready, or first-message loss,
**restore 30 s first** and re-test before investigating anything subtler.

## Stop and ask Nick before

Changing Spotter power config beyond the documented schedules, anything that
leaves a unit halted on a continuously-powered bus, or expanding scope into the
sporadic-blackout mitigations (tail placement, head duplication, split-burst) —
those are recorded in D10 as **next** sprint.

## Deliverables

`runs/sprint11_<date>/RESULTS.md` (the three metrics vs baseline), a DEV_LOG
entry in the same commit as the work, TRACKER boxes ticked only where artifacts
prove them, both units restored to field-normal and verified imaging, and a PR
into `development`.
