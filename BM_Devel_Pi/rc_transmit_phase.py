#!/usr/bin/env python3
# filename: rc_transmit_phase.py
# description: Sprint11 C2 — phase-aware transmit scheduling against the 5-minute UTC blackout grid.
"""
Sprint11 C2 — schedule the image burst to MISS the blackout, not survive it.

WHY THIS EXISTS
Sprint10 Phase E measured that image-chunk loss is not random link loss: it
is a periodic, wall-clock event. 83 % of measured gaps start within 30 s of
an absolute 5-minute UTC boundary (median offset +1 s), each lasting D ~ 9 s
(16.5 s at the 75th pct, 24 s at the 90th). During a blackout the Spotter's
2-slot cellular queue rejects every submit SILENTLY -- the Pi still logs
`sent=N/N complete=True`, so the device cannot detect it.

At the 1.0 s pacing a whole image is 194 messages = 194 s, and the grid
gives 300 s between boundaries. So the image FITS BETWEEN two blackouts.
This module computes where we are on that grid and how long to wait so the
burst lands entirely inside one clean lane.

    |<--30s-->|<---------- 250 s usable lane ---------->|<--20s-->|
    B         lane_start                        lane_end          B+300
    ^ boundary (blackout starts here, ~9 s typical, 24 s at p90)

Guards are sized from the MEASURED distribution (DESIGN D3), not guessed:
post-boundary 30 s covers ~90 % of the blackout tail; pre-boundary 20 s
keeps the last chunk clear of the next one.

COORDINATE SYSTEM (this matters -- CLAUDE.md rule 12)
Everything here is in ABSOLUTE UTC EPOCH SECONDS, never cycle-relative
time (DESIGN D1). "phase" always means `epoch mod grid_seconds`, i.e.
seconds since the last 5-minute boundary. A scheme phrased as "wait N
seconds after capture" drifts against the grid and eventually lands on it.

OPEN-LOOP BY DESIGN
The only input is UTC, which the cycle already reads from the Spotter over
the BM bus (`[CMD] spotter UTC decoded: ...`). No USB, no backend, no
per-chunk feedback -- field units have none of those.

FAILURE MODE (DESIGN D1 -- the path that fails silently in the field)
If the clock read fails, `plan_from_clock` returns reason="no_clock" and
wait_s=0: we transmit IMMEDIATELY, exactly as the pre-Sprint11 cycle did.
A wrong phase is no worse than the status quo; silently trusting a bad
clock would be worse than both.

Inputs:  UTC epoch seconds, burst duration in seconds, guard config.
Outputs: a plan dict -- how long to wait and why (see plan_transmit).
Assumptions: the blackout grid is absolute UTC and 300 s (measured).
Known limitation: this removes the PERIODIC blackout population only. The
sporadic sync-session events (17 % of gaps, unaligned, 6-36 s) remain and
are explicitly out of scope (DESIGN D10).

Example:
    plan = plan_transmit(now_epoch=1785349000.0, burst_seconds=194.0)
    if plan["wait_s"] > 0:
        sleep(plan["wait_s"])
"""

import time

# The measured blackout grid. 300 s, absolute UTC (Phase E).
DEFAULT_GRID_SECONDS = 300.0
# Post-boundary guard: covers ~90 % of the measured blackout tail (D3).
DEFAULT_POST_GUARD_S = 30.0
# Pre-boundary guard: keeps the final chunk clear of the next boundary (D3).
DEFAULT_PRE_GUARD_S = 20.0
# Never park the cycle longer than this waiting for a lane. One full grid
# period is the worst case a correct plan can ever ask for; anything more
# means the inputs are wrong, and a stuck cycle burns the bus window.
DEFAULT_MAX_WAIT_S = 300.0

DEFAULT_TRANSMIT_PHASE_CONFIG = {
    "enabled": False,          # absent island == pre-Sprint11 behaviour
    "grid_seconds": DEFAULT_GRID_SECONDS,
    "post_boundary_guard_s": DEFAULT_POST_GUARD_S,
    "pre_boundary_guard_s": DEFAULT_PRE_GUARD_S,
    "max_wait_s": DEFAULT_MAX_WAIT_S,
}


def usable_lane_seconds(grid_seconds=DEFAULT_GRID_SECONDS,
                        post_guard_s=DEFAULT_POST_GUARD_S,
                        pre_guard_s=DEFAULT_PRE_GUARD_S):
    """Seconds of clean transmit time between two blackouts.

    With the shipped guards: 300 - 30 - 20 = 250 s. That is the config rule
    `delay x message_cap <= 250 s`, i.e. at 194 messages only 1.0 s and
    1.25 s pacing fit one lane (DESIGN D3).
    """
    return float(grid_seconds) - float(post_guard_s) - float(pre_guard_s)


def plan_transmit(now_epoch, burst_seconds,
                  grid_seconds=DEFAULT_GRID_SECONDS,
                  post_guard_s=DEFAULT_POST_GUARD_S,
                  pre_guard_s=DEFAULT_PRE_GUARD_S,
                  max_wait_s=DEFAULT_MAX_WAIT_S):
    """Return the transmit plan for a burst of `burst_seconds` at `now_epoch`.

    now_epoch: absolute UTC epoch seconds (NOT monotonic, NOT cycle-relative).
    burst_seconds: wall-clock length of the whole burst, START..END inclusive.

    Returns a dict:
      wait_s        seconds to sleep before the first message (>= 0)
      reason        one of:
                      in_lane            -- already inside a clean lane, go now
                      wait_post_guard    -- inside the blackout guard, wait it out
                      wait_next_lane     -- too late in this lane, take the next
                      burst_exceeds_lane -- cannot fit ANY lane; go now, loudly
      phase_s       seconds since the last boundary at now_epoch
      start_phase_s phase the burst will actually start at
      end_phase_s   phase the burst will end at (may exceed grid if it cannot fit)
      lane_s        usable lane length
      fits_lane     whether the burst fits a lane at all
      crosses_boundary  True if the burst will still cross a boundary

    burst_exceeds_lane is NOT an error and NOT a wait: if the image is
    longer than a lane, no amount of waiting helps and parking the cycle
    would only burn the bus window. We start at the top of a lane anyway
    (the best available phase) and report it so the caller can log loudly.
    Split-burst handling for this case is deliberately out of scope (D10).
    """
    grid = float(grid_seconds)
    post = float(post_guard_s)
    pre = float(pre_guard_s)
    burst = max(0.0, float(burst_seconds))
    lane = usable_lane_seconds(grid, post, pre)

    if grid <= 0 or lane <= 0:
        raise ValueError(
            f"unusable grid config: grid={grid} post_guard={post} "
            f"pre_guard={pre} -> lane={lane}s"
        )

    phase = float(now_epoch) % grid
    lane_end = grid - pre  # phase at which the lane closes

    if burst > lane:
        # No lane can hold it. Start at the top of the nearest lane anyway
        # so the damage lands as late as possible in the stream.
        wait = 0.0 if phase >= post else post - phase
        fits = False
        reason = "burst_exceeds_lane"
    elif phase < post:
        wait = post - phase
        fits = True
        reason = "wait_post_guard"
    elif phase + burst <= lane_end:
        wait = 0.0
        fits = True
        reason = "in_lane"
    else:
        wait = (grid - phase) + post
        fits = True
        reason = "wait_next_lane"

    wait = min(wait, float(max_wait_s))
    start_phase = (phase + wait) % grid
    end_phase = start_phase + burst

    return {
        "wait_s": wait,
        "reason": reason,
        "phase_s": phase,
        "start_phase_s": start_phase,
        "end_phase_s": end_phase,
        "lane_s": lane,
        "fits_lane": fits,
        "crosses_boundary": end_phase > lane_end,
    }


class GridClock:
    """UTC epoch extrapolated from ONE Spotter time read.

    The Spotter read is expensive (a BM subscribe + wait) and happens early
    in the cycle; the transmit decision happens after capture + encode. We
    pin the read to a monotonic timestamp and extrapolate, rather than
    re-reading or trusting the system clock -- `set_system_clock_from_spotter`
    is config-optional, so `time.time()` may be years off on a unit with no
    RTC and a failed sync.
    """

    def __init__(self, utc_epoch, mono_at_read, clock=time.monotonic,
                 source="spotter"):
        self.utc_epoch = float(utc_epoch)
        self.mono_at_read = float(mono_at_read)
        self.clock = clock
        self.source = source

    def epoch_now(self):
        return self.utc_epoch + (self.clock() - self.mono_at_read)

    def age_s(self):
        """How long ago the underlying read happened (drift proxy)."""
        return self.clock() - self.mono_at_read


def plan_from_clock(grid_clock, burst_seconds, cfg=None):
    """plan_transmit against a GridClock, with the D1 no-clock fallback.

    grid_clock None (the clock read failed) -> wait_s=0, reason="no_clock":
    transmit immediately, i.e. exactly the pre-Sprint11 behaviour. This is
    the branch that must never silently do something clever.
    """
    cfg = cfg or DEFAULT_TRANSMIT_PHASE_CONFIG
    if grid_clock is None:
        return {
            "wait_s": 0.0,
            "reason": "no_clock",
            "phase_s": None,
            "start_phase_s": None,
            "end_phase_s": None,
            "lane_s": usable_lane_seconds(
                cfg.get("grid_seconds", DEFAULT_GRID_SECONDS),
                cfg.get("post_boundary_guard_s", DEFAULT_POST_GUARD_S),
                cfg.get("pre_boundary_guard_s", DEFAULT_PRE_GUARD_S),
            ),
            "fits_lane": None,
            "crosses_boundary": None,
        }
    plan = plan_transmit(
        grid_clock.epoch_now(),
        burst_seconds,
        grid_seconds=cfg.get("grid_seconds", DEFAULT_GRID_SECONDS),
        post_guard_s=cfg.get("post_boundary_guard_s", DEFAULT_POST_GUARD_S),
        pre_guard_s=cfg.get("pre_boundary_guard_s", DEFAULT_PRE_GUARD_S),
        max_wait_s=cfg.get("max_wait_s", DEFAULT_MAX_WAIT_S),
    )
    plan["clock_source"] = grid_clock.source
    plan["clock_age_s"] = grid_clock.age_s()
    return plan


# Wall-clock slack the paced loop costs beyond its sleeps: the START/END
# writes, the per-chunk base64 slicing, and UART write time. Measured
# overhead is well under a second at 384-char chunks; 2 s is a deliberate
# over-estimate because erring long only starts the burst earlier in the lane.
BURST_OVERHEAD_S = 2.0


def burst_seconds_for(message_count, delay_seconds, incomplete=False,
                      overhead_s=BURST_OVERHEAD_S):
    """Wall-clock length of the whole burst, START..END inclusive.

    The transmit loop sleeps `delay_seconds` after START and after every
    chunk, but NOT after END -- so the paced slots are `message_count + 1`.
    A bounded (incomplete) send emits one extra paced `a=inc` message first.

    NOT counted: command acks riding pacing slots. Under C3 those are
    deferred out of the burst entirely (DESIGN D5), which is one more
    reason deferring matters -- an un-deferred ack silently lengthens the
    burst and can push its tail onto a boundary.
    """
    slots = int(message_count) + 1 + (1 if incomplete else 0)
    return slots * float(delay_seconds) + float(overhead_s)


# Time sources we will schedule against. 'system' is explicitly NOT here:
# in should_transmit_now_from_schedule it means the Spotter read FAILED and
# the gate fell back to the Pi's own clock, which on a unit with no RTC can
# be years off. Scheduling against that would place the burst at a
# confidently-wrong phase -- worse than not scheduling at all (DESIGN D1).
TRUSTED_TIME_SOURCES = ("spotter", "rtc")


def grid_clock_from_gate_info(info, mono_at_read, clock=time.monotonic):
    """Build a GridClock from should_transmit_now_from_schedule's info dict.

    Returns None when the gate did not produce a trusted absolute time
    (source 'system' = spotter read failed, 'skipped' = window check off).
    Reusing the gate's read costs nothing: the cycle already pays for it.
    """
    import datetime as _dt

    if not info:
        return None
    source = info.get("source_time")
    if source not in TRUSTED_TIME_SOURCES:
        return None
    raw = info.get("utc_time")
    if not raw:
        return None
    try:
        utc_dt = _dt.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=_dt.timezone.utc)
    return GridClock(utc_dt.timestamp(), mono_at_read, clock=clock,
                     source=source)


def acquire_grid_clock(gate_info, gate_mono, daemon=None,
                       clock=time.monotonic, timeout_s=20.0):
    """Best available grid clock, or None -- never raises.

    Order: (1) the schedule gate's own Spotter/RTC read, which the cycle has
    already paid for; (2) an explicit read over the daemon's shared port,
    for cycles that skipped the gate (enforce_time_window off on the bench).
    Anything else -> None -> the D1 unscheduled fallback.
    """
    grid_clock = grid_clock_from_gate_info(gate_info, gate_mono, clock=clock)
    if grid_clock is not None:
        return grid_clock
    if daemon is None:
        return None
    try:
        mono = clock()
        utc_dt = daemon.wait_for_spotter_utc(timeout_s)
        return GridClock(utc_dt.timestamp(), mono, clock=clock,
                         source="spotter_explicit")
    except Exception as exc:
        print(f"[PHASE][WARN] explicit Spotter time read failed: {exc}")
        return None


def describe_plan(plan, burst_seconds):
    """One loud log line per the fail-visibly rule (CLAUDE.md rule 9)."""
    if plan["reason"] == "no_clock":
        return ("[PHASE] no Spotter clock -> transmitting UNSCHEDULED "
                "(pre-Sprint11 behaviour; D1 fallback)")
    head = (f"[PHASE] phase={plan['phase_s']:.1f}s burst={burst_seconds:.0f}s "
            f"lane={plan['lane_s']:.0f}s -> {plan['reason']} "
            f"wait={plan['wait_s']:.1f}s "
            f"(start@+{plan['start_phase_s']:.0f}s end@+{plan['end_phase_s']:.0f}s)")
    if not plan["fits_lane"]:
        head += ("\n[PHASE][WARN] burst LONGER than the clean lane — it WILL "
                 "cross a blackout. Reduce message_cap or pacing so "
                 "delay x cap <= lane (DESIGN D3).")
    elif plan["crosses_boundary"]:
        head += "\n[PHASE][WARN] plan still crosses the pre-boundary guard"
    return head


# ---------------------------------------------------------------------------
# Config island
# ---------------------------------------------------------------------------

def load_transmit_phase_config(config_path):
    """Read the `transmit_phase:` island from camera_schedule.yaml.

    Same tolerant flat-island convention as bm_commands / media_gid (dev
    Macs may lack PyYAML; the Pi has it). Absent island == disabled ==
    pre-Sprint11 unscheduled transmit. Bad values fall back per key, loudly
    -- a config typo must not be able to park a field cycle.
    """
    cfg = dict(DEFAULT_TRANSMIT_PHASE_CONFIG)
    island = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith((" ", "\t")):
                    in_island = line.strip() == "transmit_phase:"
                    continue
                if not in_island or ":" not in line:
                    continue
                key, _, value = line.strip().partition(":")
                island[key.strip()] = value.strip().strip("'\"")
    except OSError:
        return cfg

    if "enabled" in island:
        cfg["enabled"] = island["enabled"].lower() in ("true", "1", "yes", "on")
    for key in ("grid_seconds", "post_boundary_guard_s",
                "pre_boundary_guard_s", "max_wait_s"):
        if key not in island:
            continue
        try:
            value = float(island[key])
            if value < 0:
                raise ValueError("negative")
            cfg[key] = value
        except (TypeError, ValueError) as exc:
            print(f"[PHASE][WARN] transmit_phase.{key}={island[key]!r} "
                  f"invalid ({exc}); using {cfg[key]}")

    if usable_lane_seconds(cfg["grid_seconds"], cfg["post_boundary_guard_s"],
                           cfg["pre_boundary_guard_s"]) <= 0:
        print("[PHASE][WARN] guards consume the whole grid; phase scheduling "
              "DISABLED for this cycle")
        cfg["enabled"] = False
    return cfg
