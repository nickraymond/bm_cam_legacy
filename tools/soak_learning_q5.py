#!/usr/bin/env python3
# filename: soak_learning_q5.py
# description: c_soak_learning Q5 — did Spotter HDR off (hrpm 0) cut first-send chunk drops by >= 20 %? (stdlib only)
"""
Q5 of the soak-learning spec (SPEC_c_soak_learning.md amendment rev 2, R1-R7), with every
choice pre-registered in EXPECTATIONS.md §5 (lock v1, sha256 in MANIFEST.md).

Purpose
  Per unit: drop rate = sum(M - first-send received) / sum(M) over production wakes, in equal
  windows before/after the Spotter's HDR-off restart (T_off). R = 1 - rate_after / rate_before.
  PASS needs BOTH one-sided 95 % lower bounds >= 20 %: a 6 h block bootstrap (10 000 draws,
  seed 0, percentile) and a negative-binomial profile-likelihood bound. Plus a Poisson
  conditional-binomial bound (extra line only) and, for FAIL / INCONCLUSIVE, a simulated
  wakes-per-side for 80 % power to detect a true 20 % reduction.

Inputs
  <run-dir>/ledger.csv (tools/soak_learning_ledger.py), EXPECTATIONS constants below.

Outputs
  <run-dir>/q5_results.json, <run-dir>/q5_wakes.csv (every wake used or excluded, with reason),
  printed summary. Exit 0 always (a "not testable" verdict is a result, not an error).

Example
  python3 tools/soak_learning_q5.py

Known limitations
  - 3 blocks per side (the windows allow ~19 h): the bootstrap has 27 distinct resamples per
    side, so its percentile bound is coarse. That is the locked method, reported as is.
  - Pure-Python Nelder-Mead for the NB fit; checked against the Poisson limit when alpha -> 0.
"""

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
DEFAULT_RUN = Path.home() / "NereusData/bm_cam_legacy/2026-10-16_soak_learning"
BAR = 0.20
CHI2_1_090 = 2.705543454095404      # one-sided 95 % profile-likelihood cut
N_BOOT, SEED = 10_000, 0


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def iso(d):
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# EXPECTATIONS.md §5 (lock v1)
Q5 = {
    "BMCAM_003": {"spot": "SPOT-31593C", "t_off": T("2026-10-15T16:47:38Z"), "before_start": T("2026-10-14T21:40:24Z"),
                  "exclusions": [("HDR-off cfg-save stub wake (power cycle)", T("2026-10-15T16:47:38Z"), T("2026-10-15T16:52:38Z"))]},
    "BMCAM_004": {"spot": "SPOT-33507C", "t_off": T("2026-10-15T16:37:13Z"), "before_start": T("2026-10-14T21:44:54Z"),
                  "exclusions": [("HDR-off cfg-save stub wake (power cycle)", T("2026-10-15T16:37:13Z"), T("2026-10-15T16:42:13Z")),
                                 ("THERMAL_FAULT (console OK->THERMAL_FAULT 2026-10-14T17:42:55Z; next clearing evidence: "
                                  "post OK 2026-10-17T18:41:19Z)", T("2026-10-14T17:42:55Z"), T("2026-10-17T18:41:19Z"))]},
}


# ---------- data ----------
NONSCHED_S = 20 * 60          # EXPECTATIONS §5: a non-scheduled wake excludes its image + 20 min
SLOT_TOL_S = 10 * 60          # "bus-on not within :00 ± 10 min of a scheduled slot"


def rule_exclusions(run_dir, dev, cfg):
    """EXPECTATIONS §5 mechanical rules, applied from the ledger outputs:
    (a) every non-scheduled wake = power cycle: [t, t + 20 min) from its bus-on (console) and from its
        image's capture (ledger kind stub / forced / unscheduled), whichever exist;
    (b) a scheduled slot with no wake (wakes.csv wake_seen False) excludes [slot, slot + 1 h)."""
    out = []
    for r in csv.DictReader(open(run_dir / "ledger.csv")):
        if r["unit"] == dev and r["kind"] in ("stub", "forced", "unscheduled"):
            t = T(r["capture_utc"])
            out.append((f"non-scheduled wake {r['media_key']} ({r['kind']}) image + 20 min", t, t + timedelta(seconds=NONSCHED_S)))
    for b in csv.DictReader(open(run_dir / "bus_windows.csv")):
        if b["unit"] != dev:
            continue
        t = T(b["bus_on"])
        off_slot = min(abs((t - t.replace(minute=0, second=0)).total_seconds()),
                       abs((t.replace(minute=0, second=0) + timedelta(hours=1) - t).total_seconds()))
        if off_slot > SLOT_TOL_S:
            out.append((f"non-scheduled bus-on {b['bus_on']} (power cycle) + 20 min", t, t + timedelta(seconds=NONSCHED_S)))
    for w in csv.DictReader(open(run_dir / "wakes.csv")):
        if w["unit"] == dev and w["wake_seen"] != "True":
            t = T(w["slot_utc"])
            out.append((f"missed scheduled wake {w['slot_utc']}", t, t + timedelta(hours=1)))
    return out


def load_wakes(run_dir, dev, cfg):
    t_off, b0 = cfg["t_off"], cfg["before_start"]
    D = t_off - b0
    rows = [r for r in csv.DictReader(open(run_dir / "ledger.csv")) if r["unit"] == dev]
    out = []
    for r in rows:
        cap = T(r["capture_utc"])
        if not (b0 <= cap < t_off + D):
            continue
        side = "after" if cap >= t_off else "before"
        dist = (cap - t_off).total_seconds() if side == "after" else (t_off - cap).total_seconds()
        block = min(int(dist // (6 * 3600)), 2)          # remainder merges into the outermost block
        reason = ""
        if r["kind"] != "production":
            reason = f"not a scheduled production wake ({r['kind']})"
        for name, a, b in cfg["exclusions"]:
            if a <= cap < b and not reason:
                reason = f"excluded: {name}"
        M = int(r["chunks_expected"] or 0)
        if not reason and M == 0:
            reason = "M = 0 (R1)"
        out.append({"unit": dev, "media_key": r["media_key"], "capture_utc": r["capture_utc"], "side": side,
                    "block": block, "M": M, "lost": int(r["first_send_lost"] or 0),
                    "qfull": r["wake_qfull"], "hdr_adds": r["wake_hdr_adds"], "stall_runs": r["first_send_stall_runs"],
                    "used": not reason, "reason": reason})
    return out, D


def excluded_fraction(cfg, D):
    """Fraction of each side's window (by time) covered by exclusion intervals."""
    t_off = cfg["t_off"]
    sides = {"before": (t_off - D, t_off), "after": (t_off, t_off + D)}
    res = {}
    for s, (a, b) in sides.items():
        cov = []
        for _, x, y in cfg["exclusions"]:
            lo, hi = max(a, x), min(b, y)
            if lo < hi:
                cov.append((lo, hi))
        cov.sort()
        tot, cur = 0.0, None
        for lo, hi in cov:
            if cur and lo <= cur[1]:
                cur = (cur[0], max(cur[1], hi))
            else:
                if cur:
                    tot += (cur[1] - cur[0]).total_seconds()
                cur = (lo, hi)
        if cur:
            tot += (cur[1] - cur[0]).total_seconds()
        res[s] = tot / (b - a).total_seconds()
    return res


# ---------- statistics ----------
def rate(ws):
    m = sum(w["M"] for w in ws)
    return (sum(w["lost"] for w in ws) / m) if m else float("nan")


def bootstrap(blocks_b, blocks_a):
    rng = random.Random(SEED)
    R = []
    for _ in range(N_BOOT):
        bb = [blocks_b[rng.randrange(len(blocks_b))] for _ in range(len(blocks_b))]
        aa = [blocks_a[rng.randrange(len(blocks_a))] for _ in range(len(blocks_a))]
        lb, mb = sum(x[0] for x in bb), sum(x[1] for x in bb)
        la, ma = sum(x[0] for x in aa), sum(x[1] for x in aa)
        if lb == 0:
            R.append(-math.inf)
        else:
            R.append(1 - (la / ma) / (lb / mb))
    R.sort()
    return R[int(0.05 * N_BOOT)], R


def nb_ll(params, y, M, x):
    b0, b1, la = params
    a = math.exp(la)
    r = 1.0 / a
    ll = 0.0
    for yi, Mi, xi in zip(y, M, x):
        mu = Mi * math.exp(b0 + b1 * xi)
        ll += (math.lgamma(yi + r) - math.lgamma(r) - math.lgamma(yi + 1)
               + r * math.log(r / (r + mu)) + yi * math.log(mu / (r + mu)))
    return ll


def nelder_mead(f, x0, step=0.5, iters=4000, tol=1e-10):
    n = len(x0)
    pts = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        c = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        xr = [c[j] + (c[j] - pts[-1][j]) for j in range(n)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c[j] + 2 * (c[j] - pts[-1][j]) for j in range(n)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = [c[j] + 0.5 * (pts[-1][j] - c[j]) for j in range(n)]
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [[pts[0][j] + 0.5 * (p[j] - pts[0][j]) for j in range(n)] for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = min(range(n + 1), key=lambda k: vals[k])
    return pts[i], vals[i]


def nb_fit(ws):
    y = [w["lost"] for w in ws]
    M = [w["M"] for w in ws]
    x = [1 if w["side"] == "after" else 0 for w in ws]
    clamp = lambda la: min(max(la, -18.0), 6.0)   # noqa: E731  alpha in [1.5e-8, 403]

    def neg(p):
        return -nb_ll((p[0], p[1], clamp(p[2])), y, M, x)
    rb = max(rate([w for w in ws if w["side"] == "before"]), 1e-6)
    ra = max(rate([w for w in ws if w["side"] == "after"]), 1e-6)
    best = None
    for la0 in (-6.0, -2.0, 0.0):
        p, v = nelder_mead(neg, [math.log(rb), math.log(ra / rb), la0])
        p, v = nelder_mead(neg, p, step=0.05)
        if best is None or v < best[1]:
            best = (p, v)
    (b0, b1, la), v = best
    ll_hat = -v

    def prof(b1f):
        f = lambda q: -nb_ll((q[0], b1f, clamp(q[1])), y, M, x)   # noqa: E731
        q, vq = nelder_mead(f, [b0, la], step=0.3)
        q, vq = nelder_mead(f, q, step=0.03)
        return -vq
    lo, hi = b1, b1 + 0.25
    while 2 * (ll_hat - prof(hi)) < CHI2_1_090:
        hi += 0.25
        if hi > b1 + 20:
            break
    for _ in range(50):
        mid = (lo + hi) / 2
        if 2 * (ll_hat - prof(mid)) < CHI2_1_090:
            lo = mid
        else:
            hi = mid
    b1_up = (lo + hi) / 2
    return {"beta0": b0, "beta1": b1, "alpha": math.exp(clamp(la)), "loglik": ll_hat,
            "beta1_upper_95_onesided": b1_up, "R_hat": 1 - math.exp(b1), "R_lower": 1 - math.exp(b1_up)}


def binom_cdf(k, n, p):
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    s, lp, lq = 0.0, math.log(p), math.log1p(-p)
    for i in range(0, k + 1):
        s += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(s, 1.0)


def poisson_rr_lower(lb, Mb, la, Ma):
    """Conditional-binomial (exact) one-sided 95 % lower bound on R = 1 - RR (extra line only)."""
    n = la + lb
    if n == 0:
        return float("nan")
    lo, hi = 0.0, 1.0           # upper bound on p = P(after) with P(X <= la) = 0.05
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(la, n, mid) > 0.05:
            lo = mid
        else:
            hi = mid
    pu = (lo + hi) / 2
    rr_up = (pu / (1 - pu)) * (Mb / Ma)
    return 1 - rr_up


def poisson_draw(rng, lam):
    if lam <= 0:
        return 0
    if lam > 500:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def power_n(rate_b, alpha, Ms, reps=2000):
    """Wakes per side for 80 % power, one-sided alpha 0.05, H1: R = 0.20 vs H0: R = 0 (EXPECTATIONS §5)."""
    def power(n):
        rng = random.Random(SEED)
        hits = 0
        for _ in range(reps):
            sides = []
            for r_ in (rate_b, rate_b * (1 - BAR)):
                M = [Ms[rng.randrange(len(Ms))] for _ in range(n)]
                y = []
                for m in M:
                    mu = m * r_
                    lam = rng.gammavariate(1 / alpha, alpha * mu) if alpha > 1e-8 else mu
                    y.append(poisson_draw(rng, lam))
                sides.append((y, M))
            est = []
            for y, M in sides:
                ry = sum(y) / sum(M)
                est.append((y, M, ry))
            num = sum((yy - ry * mm) ** 2 - ry * mm for (y, M, ry) in est for yy, mm in zip(y, M))
            den = sum((ry * mm) ** 2 for (y, M, ry) in est for mm in M)
            a_hat = max(0.0, num / den) if den else 0.0
            if est[0][2] == 0 or est[1][2] == 0:
                continue
            var = 0.0
            for y, M, ry in est:
                mus = [ry * mm for mm in M]
                var += (sum(mus) + a_hat * sum(m * m for m in mus)) / (sum(y) ** 2)
            z = (math.log(est[0][2]) - math.log(est[1][2])) / math.sqrt(var)
            hits += z > 1.6448536269514722
        return hits / reps
    n, last = 10, None
    while n <= 20480:
        p = power(n)
        if p >= 0.80:
            break
        last, n = n, n * 2
    else:
        return None, None
    lo, hi = (last or 5), n
    while hi - lo > 5:
        mid = int(round((lo + hi) / 2 / 5) * 5)
        if mid in (lo, hi):
            break
        if power(mid) >= 0.80:
            hi = mid
        else:
            lo = mid
    return hi, power(hi)


def analyse(dev, cfg, run_dir):
    # rule exclusions first, the named ones (HDR-off stub, THERMAL_FAULT) after, so a wake's reason names
    # the most specific rule; every interval counts toward the 25 % test either way
    cfg = dict(cfg, exclusions=rule_exclusions(run_dir, dev, cfg) + cfg["exclusions"])
    wakes, D = load_wakes(run_dir, dev, cfg)
    fr = excluded_fraction(cfg, D)
    used = [w for w in wakes if w["used"]]
    b = [w for w in used if w["side"] == "before"]
    a = [w for w in used if w["side"] == "after"]
    blocks = {s: [] for s in ("before", "after")}
    for s, ws in (("before", b), ("after", a)):
        for k in range(3):
            bw = [w for w in ws if w["block"] == k]
            if bw:
                blocks[s].append((sum(w["lost"] for w in bw), sum(w["M"] for w in bw), len(bw)))
    res = {"unit": dev, "spotter": cfg["spot"], "t_off": iso(cfg["t_off"]), "D_h": round(D.total_seconds() / 3600, 3),
           "window_before": [iso(cfg["t_off"] - D), iso(cfg["t_off"])], "window_after": [iso(cfg["t_off"]), iso(cfg["t_off"] + D)],
           "excluded_fraction_by_time": fr, "exclusions": [[n, iso(x), iso(y)] for n, x, y in cfg["exclusions"]],
           "wakes_used": {"before": len(b), "after": len(a)},
           "blocks": {s: [{"lost": x[0], "M": x[1], "wakes": x[2]} for x in v] for s, v in blocks.items()},
           "lost": {"before": sum(w["lost"] for w in b), "after": sum(w["lost"] for w in a)},
           "M": {"before": sum(w["M"] for w in b), "after": sum(w["M"] for w in a)}}
    res["rate_before"], res["rate_after"] = rate(b), rate(a)
    not_testable = []
    if max(fr.values()) > 0.25:
        not_testable.append(f"exclusions remove {max(fr.values()):.0%} of a window (> 25 %)")
    if min(len(blocks["before"]), len(blocks["after"])) < 3:
        not_testable.append("fewer than 3 blocks on a side")
    if res["rate_before"] == 0:
        not_testable.append("rate_before = 0")
    # numbers are computed even when not testable, as a labelled sensitivity line (EXPECTATIONS §5)
    # sensitivity line = the locked rules minus ONLY the THERMAL_FAULT interval
    thermal = [x for x in cfg["exclusions"] if x[0].startswith("THERMAL_FAULT")]
    other = [x for x in cfg["exclusions"] if not x[0].startswith("THERMAL_FAULT")]

    def kept(w):
        cap = T(w["capture_utc"])
        return (w["reason"] == "" or (thermal and w["reason"].startswith("excluded: THERMAL"))) and \
            not any(a <= cap < b for _, a, b in other)
    sens_b = [w for w in wakes if w["side"] == "before" and kept(w)]
    sens_a = [w for w in wakes if w["side"] == "after" and kept(w)]
    calc_b, calc_a = (b, a) if not not_testable else (sens_b, sens_a)
    cb = {s: [] for s in ("before", "after")}
    for s, ws in (("before", calc_b), ("after", calc_a)):
        for k in range(3):
            bw = [w for w in ws if w["block"] == k]
            if bw:
                cb[s].append((sum(w["lost"] for w in bw), sum(w["M"] for w in bw), len(bw)))
    rb, ra = rate(calc_b), rate(calc_a)
    stats = {"basis": "locked rules" if not not_testable else "SENSITIVITY: outside the locked rules, report only",
             "wakes": {"before": len(calc_b), "after": len(calc_a)},
             "lost": {"before": sum(w["lost"] for w in calc_b), "after": sum(w["lost"] for w in calc_a)},
             "M": {"before": sum(w["M"] for w in calc_b), "after": sum(w["M"] for w in calc_a)},
             "rate_before": rb, "rate_after": ra}
    if rb and rb > 0 and len(cb["before"]) >= 3 and len(cb["after"]) >= 3:
        R_obs = 1 - ra / rb
        boot_lo, _ = bootstrap(cb["before"], cb["after"])
        nb = nb_fit(calc_b + calc_a)
        pois = poisson_rr_lower(stats["lost"]["before"], stats["M"]["before"], stats["lost"]["after"], stats["M"]["after"])
        stats.update(R_observed=R_obs, R_lower_bootstrap=boot_lo, nb=nb, R_lower_poisson_extra=pois)
        if R_obs <= BAR:
            verdict = "FAIL (bar not met)"
        elif boot_lo >= BAR and nb["R_lower"] >= BAR:
            verdict = "PASS"
        else:
            verdict = "INCONCLUSIVE"
        stats["verdict_if_testable"] = verdict
        if verdict != "PASS":
            Ms = [round(sum(w["M"] for w in calc_b) / len(calc_b))]     # EXPECTATIONS §5: "mean M per wake"
            n, pw = power_n(rb, max(nb["alpha"], 1e-9), Ms)
            stats["power"] = {"wakes_per_side_80pct": n, "power_at_n": pw, "before_wakes_existing": len(calc_b),
                              "reachable": (n is not None and n <= len(calc_b)),
                              "note": ("unreachable without HDR back on (Nick's call)"
                                       if (n is None or n > len(calc_b)) else "reachable with existing before-data")}
    res["stats"] = stats
    res["verdict"] = ("not testable: " + "; ".join(not_testable)) if not_testable else stats.get("verdict_if_testable", "not testable")
    return res, wakes


def main(argv=None):
    ap = argparse.ArgumentParser(description="Q5: HDR off vs first-send chunk drops (locked rules)")
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    a = ap.parse_args(argv)
    run_dir = a.run_dir.expanduser()
    h = hashlib.sha256((run_dir / "EXPECTATIONS.md").read_bytes()).hexdigest()
    if h not in (run_dir / "MANIFEST.md").read_text():
        raise SystemExit(f"FAIL (A5): EXPECTATIONS.md sha256 {h} not in MANIFEST.md")
    out, allw = {"expectations_sha256": h, "units": {}}, []
    for dev, cfg in Q5.items():
        res, wakes = analyse(dev, cfg, run_dir)
        out["units"][dev] = res
        allw += wakes
    testable = [d for d, r in out["units"].items() if not r["verdict"].startswith("not testable")]
    if len(testable) == 0:
        overall = "not testable"
    elif len(testable) == 1:
        overall = f"report only (n = 1 unit: {testable[0]} {out['units'][testable[0]]['verdict']}); no recommendation (R7, TEAM_RULES §9)"
    else:
        order = {"FAIL (bar not met)": 0, "INCONCLUSIVE": 1, "PASS": 2}
        worst = min(testable, key=lambda d: order[out["units"][d]["verdict"]])
        overall = f"{out['units'][worst]['verdict']} (worse unit {worst} decides)"
    out["overall"] = overall
    (run_dir / "q5_results.json").write_text(json.dumps(out, indent=1, default=str))
    with (run_dir / "q5_wakes.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(allw[0].keys()))
        w.writeheader()
        w.writerows(allw)
    for dev, r in out["units"].items():
        s = r["stats"]
        print(f"{dev}: verdict = {r['verdict']}; basis = {s['basis']}")
        print(f"   wakes b/a {s['wakes']}; lost {s['lost']}; M {s['M']}; rate_b {s['rate_before']:.4f} rate_a {s['rate_after']:.4f}")
        if "R_observed" in s:
            print(f"   R_obs {s['R_observed']:.3f}; boot_lo {s['R_lower_bootstrap']:.3f}; NB_lo {s['nb']['R_lower']:.3f} "
                  f"(alpha {s['nb']['alpha']:.3g}); poisson_lo(extra) {s['R_lower_poisson_extra']:.3f}")
        if "power" in s:
            print(f"   power: {s['power']}")
    print("OVERALL:", overall)
    return 0


if __name__ == "__main__":
    sys.exit(main())
