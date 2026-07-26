#!/usr/bin/env python3
# filename: rc_quality_selector.py
# description: Sprint08 M3 — adaptive quality selector (ladder step-down) for the RC.
"""
Sprint08 M3 — adaptive quality selector (sprint spec section 2).

Walks the quality ladder from q_max down, encoding via M2 and asking M1
before every step, until the estimated transmit fits. "Intelligent quality
sampling" with zero hidden state:

  a rung FITS iff
    message_count <= message_cap                      (195 field-tested cap;
                                                       chunk count only, as S07 counted it)
    AND budget.messages_fit(message_count + 2)        (+2 = START/END overhead —
                                                       caller-owned per M1's pure design)

First fitting rung wins (ladder descends, so first fit = highest quality
that fits). If even the floor fails, the FLOOR's encoded bytes are returned
with fits=False — M5's bounded partial send transmits as much of them as
time allows (S07 P4: tail-cut progressive renders from ~25% received).

P3 decisions (Nick-approved):
  - Before each encode, consult M1: has_time_for(ENCODE_ATTEMPT_ALLOWANCE_S).
    1.0 s is an ASSUMPTION chosen ~15x above the S07-measured worst attempt
    (<= 0.063 s on the Pi) — generous, and refusing to encode with under a
    second left is always correct. If no encode was possible at all, the
    result has attempts=0 and encode=None.
  - Pure module: no config loading, no hardware, no clocks of its own.

Inputs:  prepared source image (M2 prepare_source), CycleBudget (M1),
         explicit ladder/cap/chunk settings from the resolved config.
Outputs: result dict — quality, attempts, fits, reason, encode (M2 dict of
         the returned quality), attempt_log (light per-attempt records for
         M4 telemetry; no image bytes).
"""

from rc_jpeg_encoder import encode_progressive

# START + END are one unchunked BM message each; every fit decision charges them.
TRANSMIT_OVERHEAD_MSGS = 2

# ASSUMPTION (not S07-measured): per-attempt time allowance consulted via M1
# before every encode. S07 measured <= 0.063 s/attempt on the Pi; 1.0 s is a
# ~15x conservative bound. See P3 findings log.
ENCODE_ATTEMPT_ALLOWANCE_S = 1.0


def parse_ladder_spec(text):
    """Parse an explicit YAML ladder spec like "90,80,70,60,50,40,30,25,20,15,13,11,9".

    Comma/space separated ints, strictly descending, each 1..95. This is the
    field-tunable multi-segment ladder (P7 follow-up, Nick 2026-07-25); when
    set it overrides q_max/q_min/step.
    """
    parts = [p for chunk in str(text).split(",") for p in chunk.split()]
    if not parts:
        raise ValueError("quality.ladder is empty")
    try:
        ladder = [int(p) for p in parts]
    except Exception:
        raise ValueError(f"quality.ladder must be integers, got {text!r}")
    for q in ladder:
        if not 1 <= q <= 95:
            raise ValueError(f"quality.ladder values must be 1..95, got {q}")
    for higher, lower in zip(ladder, ladder[1:]):
        if lower >= higher:
            raise ValueError(f"quality.ladder must be strictly descending, got {ladder}")
    return ladder


def compute_quality_ladder(q_max, q_min, step):
    """Return the descending encode ladder from q_max to q_min inclusive.

    Steps down by `step` and always terminates exactly at q_min, even when
    (q_max - q_min) is not a multiple of step, so the configured floor is
    always the last attempt. Defaults 15/9/2 -> [15, 13, 11, 9].
    """
    q_max, q_min, step = int(q_max), int(q_min), int(step)
    if q_min > q_max:
        raise ValueError(f"quality ladder requires q_min <= q_max, got q_min={q_min} q_max={q_max}")
    if step < 1:
        raise ValueError(f"quality ladder requires step >= 1, got step={step}")
    ladder = list(range(q_max, q_min, -step))
    ladder.append(q_min)
    return ladder


def select_quality(source_img, budget, *, ladder, message_cap, chunk_b64_chars):
    """Adaptive ladder step-down over an explicit descending ladder list.

    reason values:
      fit                 a rung fit both cap and budget (fits=True)
      no_fit_cap          floor still exceeds message_cap
      no_fit_budget       floor's messages don't fit the remaining budget
      no_time_for_encode  M1 refused another encode attempt (ladder cut short)
    """
    message_cap = int(message_cap)
    if message_cap < 1:
        raise ValueError(f"message_cap must be >= 1, got {message_cap}")

    ladder = [int(q) for q in ladder]
    if not ladder:
        raise ValueError("ladder must not be empty")
    for higher, lower in zip(ladder, ladder[1:]):
        if lower >= higher:
            raise ValueError(f"ladder must be strictly descending, got {ladder}")

    attempt_log = []
    last_encode = None
    last_quality = None
    ladder_cut_short = False

    for quality in ladder:
        if not budget.has_time_for(ENCODE_ATTEMPT_ALLOWANCE_S):
            ladder_cut_short = True
            break

        encode = encode_progressive(source_img, quality, chunk_b64_chars)
        over_cap = encode["message_count"] > message_cap
        budget_fit = budget.messages_fit(encode["message_count"] + TRANSMIT_OVERHEAD_MSGS)

        attempt_log.append({
            "quality": quality,
            "jpeg_bytes": encode["jpeg_bytes"],
            "message_count": encode["message_count"],
            "over_cap": over_cap,
            "budget_fit": budget_fit,
        })

        if not over_cap and budget_fit:
            return {
                "quality": quality,
                "attempts": len(attempt_log),
                "fits": True,
                "reason": "fit",
                "encode": encode,
                "attempt_log": attempt_log,
            }

        last_encode = encode
        last_quality = quality

    if ladder_cut_short:
        reason = "no_time_for_encode"
    elif attempt_log and attempt_log[-1]["over_cap"]:
        # Cap is absolute; report it first when both cap and budget fail.
        reason = "no_fit_cap"
    else:
        reason = "no_fit_budget"

    return {
        "quality": last_quality,
        "attempts": len(attempt_log),
        "fits": False,
        "reason": reason,
        "encode": last_encode,
        "attempt_log": attempt_log,
    }
