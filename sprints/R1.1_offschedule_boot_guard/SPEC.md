# R1.1: off-schedule boot guard (SPEC, draft for review; NO code yet)

Status: draft, 2026-10-02. Author: the Sprint27 session. Decision so far (Nick, via the EM,
2026-10-02):
- G5 runs the frozen R1 code, and stub-window cuts are logged as observed events (option "accept");
- this guard is an R1.1 item.

## 1. Problem

- **The stub window.** When a Spotter powers up unattended, its BM bus comes up for a ~2 min stub
  window before the aligned schedule starts. An unattended power-up is, for example, a
  `mem_fault_reboot` (SPOT-31593C during G1) or a power toggle.
- **What happens to the Pi.** An ARMED per_boot Pi boots inside the stub, starts a ~8 min
  production cycle, and is hard-cut when the stub ends.
- **The cost.**
  - one lost cycle;
  - an SD-corruption risk;
  - during G5 (Sun 08:00 → Mon 08:00 PDT), no human or nereus000 to step in.
- **Today's workaround.** After Nick's power toggle, the TE had to halt both Pis by SSH.

Facts this rests on:

| fact | source |
|---|---|
| the ~2 min stub after a bus-power commit, then aligned 10-min windows (measured on SPOT-31593C and SPOT-33507C) | memory note `bridge-commit-short-first-window` (2026-09-24 bench) |
| bench schedule: `alignmentInterval5Min 1`, period 3600000 ms, on 600000 ms | `runs/s6b_hil_20260930/HANDOFF.md` (restore read-back "1 / 3600000 / 600000") |
| the 2026-09-24 stub cuts did no damage (ext4 orphan cleanup only; sent records intact through atomic writes) | same memory note |
| the next aligned window boots the Pi normally (no reboot loop) | same; the Spotter schedule is independent of the Pi |
| the per_boot path already reads Spotter UTC at boot and has a "skip + `<WS a=skip_win>` + listen tail + halt" branch for the DAILY window | `BM_Devel_Pi/rc_progressive_jpeg.py` schedule gate (~L600–670), `spotter_time_sync.read_spotter_utc` (timeout 60 s) |
| the Pi needs ~38 s to boot | Sprint23 (memory note `sprint23-remote-msg-latency`) |

**Assumption, unverified:** which minute of the hour each Spotter's aligned window starts. The EM
quoted "~1 min after :00". Alignment is to 5-minute boundaries, so the start may be any :x0 / :x5,
and a Spotter reboot or commit may re-roll it. **§3 measures it before anything is built.**

## 2. Design (R1.1)

The guard is one extra check beside the existing daily-window gate, on per_boot units only (stay_on
is not scheduled by the bus). **Default OFF.**

1. **Decide when the Pi booted.** After the Spotter-UTC read (already done for the daily gate):
   `boot_utc = spotter_utc − /proc/uptime`.
2. **Check it against the schedule.** It is ON schedule iff
   `(boot_utc − offset) mod period ∈ [−early_s, +grace_s]`.
   - `period` and `offset` describe the Spotter's aligned schedule.
   - `grace_s` covers the bus-up → Pi-boot delay and clock jitter.
3. **Off schedule:** send one `<WS a=offsched ...>` (it reuses the existing wake-status sender;
   ~1 message) — **or nothing** (Q2). Then skip the cycle, run NO listen tail, and halt through the
   existing `power.halt` path. Target: halted ≤ 60 s after the Spotter UTC arrives, inside the
   ~120 s stub.
4. **Fail-open rule:** no Spotter UTC (timeout), or the guard disabled → run the cycle as today.
   A mis-measured schedule must never silence a unit; a silenced unit is worse than an occasional
   cut.
5. **Telemetry:** the next normal `<WS>` carries a counter `osb=<n>` of off-schedule boots since
   the last report, so the backend and the UI can show them (Q3).

New registry keys:

| key | type | default | note |
|---|---|---|---|
| `power.boot_guard.enabled` | bool | false | |
| `power.boot_guard.period_s` | int | 3600 | must equal the Spotter's `sampleIntervalMs / 1000` |
| `power.boot_guard.offset_s` | int | 0 | window start, seconds after the top of the hour (§3) |
| `power.boot_guard.grace_s` | int | 180 | |
| `power.boot_guard.early_s` | int | 30 | Pi-vs-Spotter clock skew |

All of them are blocked from remote config (`power.*` tier).

Cost: adding keys **changes every unit's config hash** (the hash covers all registry keys) → one
`/refresh` per unit after the deploy, as in F-G3-4.

## 3. Step 0: measure the window start (before any code)

Per bench / field Spotter, desk + bench (TE):

1. **Read the bus schedule.** Pull the Spotter SD `log/` (`nereus-spotter-sd-analysis` skill) for
   ≥ 24 h of scheduled bus power, and list every `Bridge bus power: 1` / `0` time on the Spotter
   clock.
2. **Derive `offset_s`.** It is the window start mod 3600. Check that it is stable across the 24 h,
   and across one deliberate `bridge cfg commit` and one Spotter reset (does a reset re-roll it?).
3. **Measure `grace_s`.** Take bus-up → Pi first log line (`rc_run_capture_cycle.sh` timestamp)
   and Pi Spotter-UTC read time from ≥ 10 boots.
4. **Write it down.** Results go in `runs/r11_window_phase_<date>/` (CSV + RESULTS.md), and the
   values per unit go into each device profile.

**Gate:** if a Spotter reset re-rolls the phase to an arbitrary 5-min boundary, a fixed `offset_s`
is wrong after every reset. The design then changes to Q1(b).

## 4. Test plan

- **Unit tests** (fake clock, fake uptime):
  - on and off schedule;
  - the wrap at the period boundary;
  - fail-open on no UTC;
  - disabled = byte-identical goldens apart from the config hash;
  - the halt path is taken;
  - no listen tail.
- **Goldens:** a new `vectors_supervisor/offsched_boot` scenario. The hash change is re-recorded as
  in F-G3-4 (hash-only diff, checked masked).
- **Bench (TE):** on one unit with `enabled: true`, a bus toggle outside the window
  (`bridgePowerControllerEnabled 0` → `1` commit, cron armed, the 09-24 recipe) must give:
  - an `offsched` `<WS>` (or a log line);
  - halted before the stub ends;
  - no ext4 orphan cleanup on the next boot.
  Then a normal aligned window must give a normal cycle.
- **Soak:** one overnight with the guard on, no false skips (`osb=0` in normal windows).

## 5. Open questions (Nick)

- **Q1.**
  - (a) A fixed `offset_s` per unit (simple; needs §3 to show a stable phase).
  - (b) Infer the window from the bus itself, e.g. "was the bus already up when the Pi booted?".
    The Pi cannot see that today, so it needs a Sofar / bridge cue: ask Sofar whether the stub is
    configurable, or whether the bridge publishes a "scheduled window" flag.
- **Q2.** Off schedule: send one `<WS a=offsched>` (visibility, ~1 message), or send nothing (zero
  radio)?
- **Q3.** Should the UI / Config History show off-schedule boots (`osb` counter)?
- **Q4.** Should field units on `main` get it, or development-only until a release?

Labels: R1.1 (the guard); Future (the bridge-cue variant Q1(b), if Sofar exposes one).
