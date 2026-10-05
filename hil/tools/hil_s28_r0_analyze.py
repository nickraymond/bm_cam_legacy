#!/usr/bin/env python3
# filename: hil_s28_r0_analyze.py
# description: Sprint28 R0 — turn hil_s28_r0_probe.sh's pulled files into the R0.1-R0.4 criteria rows (PASS/FAIL per unit) + the predicted-wake budget.
"""
Inputs:  $1 run folder (runs/s28_ladder_<date>), $2 host; reads pulled/<host>_r0/
         {captures.csv, cma_samples.csv, encodes.csv, env.txt, dmesg_tail.txt}.
Outputs: analysis/r0_capture_<host>.csv, analysis/r0_encode_<host>.csv,
         analysis/r0_budget_<host>.csv, analysis/r0_verdict_<host>.json; prints the
         criteria table rows (LADDER.md R0.1-R0.4).
Criteria (sprints/Sprint28_raw_jxl/LADDER.md, from SPEC r4 §7.2):
  R0.1  10/10 --raw captures give DNG + JPEG, no capture error in dmesg, and NO failed CMA
        allocation during the --raw captures: the delta of /sys/kernel/mm/cma/*/alloc_pages_fail
        over the --raw phase == 0 (EM 2026-10-05). CmaFree is info only: the kernel parks
        movable pages (page cache) in the CMA area and migrates them out on demand, so a
        long-running unit shows low CmaFree with full headroom (bmcam004 6.3 MB, 10/10, 0
        errors). Without CMA sysfs counters (bmcam004's rpi trixie kernel has none): "counter
        unavailable -> dmesg rule used" = no cma_alloc / alloc failed / camera buffer
        allocation line in the kernel log of the --raw phase AND none in rpicam's stderr of
        any --raw capture. Neither counters nor a readable kernel log: NOT MEASURED (exit 2).
        debugfs /sys/kernel/debug/cma is info, only if readable without root.
  R0.2  median(--raw) - median(no --raw) capture time <= 3 s
  R0.3  per preset: median rung <= 20 s, peak RSS <= 120 MB, 0 kills / failures, and the
        predicted wake (capture + 2 rungs + 50 kB burst + 150 s tail, from process start)
        fits the 480 s cycle budget. The largest PASS sets the default crop / RAW_MAX_PX.
  R0.4  cjxl version, numpy import, SD free recorded; PASS when cjxl and numpy are present AND
        the encoder guard killed a 400 MB allocation (kind "mem")
Assumptions (labelled in the budget CSV): process start -> capture start 9 s (Sprint26 §3:
         main ~+21 s, Spotter read ~+23..+30 s), JPEG prep + ladder 2.5 s (Sprint08), the
         50 kB burst = 176 msgs at 1.3 s/msg, tail 150 s.
Example: hil/tools/hil_s28_r0_analyze.py runs/s28_ladder_20261005 bmcam003
Exit codes: 0 all PASS, 1 a criterion FAILs, 2 NOT MEASURED: an empty / -1-only CMA log or
         no --raw capture rows in it (no verdict written, re-run the probe), or R0.1 with
         neither CMA counters nor a readable kernel log (verdict "NOT MEASURED").
Known limits: dmesg "error" matching is a keyword scan (camera / unicam / cma / alloc).
"""

import csv
import json
import os
import re
import statistics
import sys

PRE_CAPTURE_S = 9.0          # ASSUMPTION: process start -> capture start (Sprint26 §3)
PREP_S = 2.5                 # Sprint08: JPEG prep + ladder
BURST_MSGS, PACE_S, TAIL_S, BUDGET_S = 176, 1.3, 150.0, 480.0
DMESG_WORDS = ("unicam", "imx708", "cma", "dma", "alloc", "pisp", "v4l2", "error")


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# The kernel's own CMA allocation failure lines (mm/cma.c "cma_alloc: ... alloc failed",
# alloc_contig_range) and camera buffer failures in the kernel log.
CMA_DMESG = re.compile(r"cma_alloc|cma:.*fail|alloc_contig_range|"
                       r"(unicam|rp1-cfe|bcm2835-isp|pisp|v4l2|dma.?heap).*(alloc|nomem|-12)",
                       re.IGNORECASE)
# libcamera / V4L2 / dma-heap buffer allocation errors in rpicam-still's own stderr.
ALLOC_STDERR = re.compile(r"failed to allocate|cannot allocate|out of memory|\benomem\b|"
                          r"dma.?heap.*(fail|error)|vidioc_reqbufs.*(fail|error)|"
                          r"unable to (allocate|import)|buffer.*alloc.*fail", re.IGNORECASE)


def _read(path):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _cma_evidence(p, dmesg_tail):
    """R0.1's CMA decision from what the unit could give (EM 2026-10-05: bmcam004's rpi
    trixie kernel has NO /sys/kernel/mm/cma/*/alloc_pages_fail).
    -> {ok: True/False/None (None = NOT MEASURED), source, counters, dmesg_failures,
        stderr_errors, debugfs}"""
    counters = _cma_counter_deltas(os.path.join(p, "cma_counters.csv"))
    stderr = _read(os.path.join(p, "raw_cap_stderr.txt"))
    err_scope = (" + rpicam stderr of every --raw capture" if stderr is not None
                 else " (no rpicam stderr file: older probe)")
    stderr = stderr or ""
    stderr_errors = [ln.strip() for ln in stderr.splitlines() if ALLOC_STDERR.search(ln)]
    status = (_read(os.path.join(p, "dmesg_status.txt")) or "").strip()
    phase = _read(os.path.join(p, "dmesg_raw_phase.txt"))
    if phase is not None:
        log, scope = phase, "the kernel log of the --raw phase"
    else:                                   # a pull from before 2026-10-05: the last 60 lines
        log, scope = dmesg_tail, "the last 60 kernel-log lines (older probe: not phase-scoped)"
    readable = status == "dmesg=readable" or (not status and bool(dmesg_tail.strip()))
    dmesg_failures = [ln.strip() for ln in log.splitlines() if CMA_DMESG.search(ln)]
    debugfs = [ln.strip() for ln in (_read(os.path.join(p, "cma_debugfs.txt")) or "").splitlines()]
    out = {"counters": counters, "dmesg_failures": dmesg_failures if readable else [],
           "stderr_errors": stderr_errors, "debugfs": debugfs or "not readable without root"}
    if counters is not None:
        out["ok"] = counters["raw"]["fail"] == 0 and not stderr_errors
        out["source"] = f"sysfs alloc_pages_fail delta over the --raw phase{err_scope}"
    elif readable:
        out["ok"] = not dmesg_failures and not stderr_errors
        out["source"] = f"counter unavailable -> dmesg rule used ({scope}{err_scope})"
    else:
        out["ok"] = None
        out["source"] = ("counter unavailable AND the kernel log unreadable -> NOT MEASURED "
                         "(never a silent PASS); rpicam stderr alone is not enough")
    return out


def _cma_counter_deltas(path):
    """cma_counters.csv (label, area, alloc_pages_success, alloc_pages_fail, summed over
    areas) -> {"plain": {"success", "fail"}, "raw": {...}, "encode": {...}} or None when the
    file is absent / has no numeric rows (no CMA sysfs on this kernel)."""
    if not os.path.exists(path):
        return None
    totals = {}
    for r in read_csv(path):
        try:
            s, f = int(r["alloc_pages_success"]), int(r["alloc_pages_fail"])
        except (TypeError, ValueError):
            continue
        t = totals.setdefault(r["label"], [0, 0])
        t[0] += s
        t[1] += f
    order = ("start", "after_plain", "after_raw", "after_encode")
    if not all(k in totals for k in order[:3]):
        return None
    out = {}
    for name, (a, b) in (("plain", order[:2]), ("raw", order[1:3]), ("encode", order[2:4])):
        if a in totals and b in totals:
            out[name] = {"success": totals[b][0] - totals[a][0], "fail": totals[b][1] - totals[a][1]}
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    run, host = argv[0], argv[1]
    p = os.path.join(run, "pulled", f"{host}_r0")
    out = os.path.join(run, "analysis")
    os.makedirs(out, exist_ok=True)
    caps = read_csv(os.path.join(p, "captures.csv"))
    cma = read_csv(os.path.join(p, "cma_samples.csv"))
    measured = [r for r in cma if r.get("cma_free_kb") not in (None, "", "-1")]
    if len(measured) < 10 or not any(r["label"].startswith("cap_raw_") for r in measured):
        # Not a unit FAIL: R0.1 was never measured (e.g. the sampler ran an empty program,
        # bm #120 bug fixed 2026-10-03). Refuse to score it.
        print(f"[R0][FATAL] {host}: cma_samples.csv has {len(measured)} usable row(s) and "
              f"{'no' if not any(r['label'].startswith('cap_raw_') for r in measured) else 'some'} "
              "--raw capture rows: the CMA sampler did not run. R0.1 is NOT measured; re-run "
              "hil_s28_r0_probe.sh. No verdict written.", file=sys.stderr)
        return 2
    encs = read_csv(os.path.join(p, "encodes.csv")) if os.path.exists(
        os.path.join(p, "encodes.csv")) else []
    env = open(os.path.join(p, "env.txt")).read()
    dmesg = open(os.path.join(p, "dmesg_tail.txt")).read().lower() \
        if os.path.exists(os.path.join(p, "dmesg_tail.txt")) else ""

    def cma_min(prefix):
        vals = [int(r["cma_free_kb"]) for r in cma if r["label"].startswith(prefix)
                and int(r["cma_free_kb"]) >= 0]
        return min(vals) if vals else None

    rows = []
    for c in caps:
        rows.append(dict(c, cma_free_min_kb=cma_min(f"cap_{c['mode']}_{c['i']}")))
    with open(os.path.join(out, f"r0_capture_{host}.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    raw = [r for r in rows if r["mode"] == "raw"]
    plain = [r for r in rows if r["mode"] == "plain"]
    ok_raw = sum(1 for r in raw if r["rc"] == "0" and int(r["jpeg_bytes"]) > 0
                 and int(r["dng_bytes"]) > 0)
    raw_cma = min((r["cma_free_min_kb"] for r in raw if r["cma_free_min_kb"] is not None),
                  default=None)
    plain_cma = min((r["cma_free_min_kb"] for r in plain if r["cma_free_min_kb"] is not None),
                    default=None)
    phase_log = _read(os.path.join(p, "dmesg_raw_phase.txt"))
    scan = phase_log.lower() if phase_log is not None else dmesg   # the --raw phase if known
    dmesg_hits = [ln for ln in scan.splitlines() if any(w in ln for w in DMESG_WORDS)
                  and ("error" in ln or "fail" in ln)]
    cev = _cma_evidence(p, dmesg)
    print(f"[R0][{host}] R0.1 CMA check: {cev['source']}")
    r01 = (None if cev["ok"] is None else
           ok_raw == len(raw) and len(raw) >= 10 and not dmesg_hits and cev["ok"])
    med_raw = statistics.median(float(r["elapsed_s"]) for r in raw) if raw else None
    med_plain = statistics.median(float(r["elapsed_s"]) for r in plain) if plain else None
    delta = None if med_raw is None or med_plain is None else med_raw - med_plain
    r02 = delta is not None and delta <= 3.0

    enc_rows, budget_rows, presets = [], [], []
    for preset in dict.fromkeys(e["preset"] for e in encs):
        es = [e for e in encs if e["preset"] == preset]
        good = [e for e in es if e["rc"] == "0" and not e["rfb"]]
        rung = statistics.median(float(e["cjxl_s_sum"]) for e in good) if good else None
        rss = max((int(e["peak_rss_kb"]) for e in good if e["peak_rss_kb"]), default=None)
        kills = len(es) - len(good)
        enc_cma = cma_min(f"enc_{preset}")
        wake = (None if rung is None or med_raw is None else
                PRE_CAPTURE_S + med_raw + PREP_S + 1.0 + 2 * rung + (BURST_MSGS + 2) * PACE_S
                + TAIL_S)
        ok = (rung is not None and rung <= 20.0 and rss is not None and rss <= 120 * 1024
              and kills == 0 and len(es) >= 10 and wake is not None and wake <= BUDGET_S
              and (enc_cma is None or enc_cma >= 1024))
        enc_rows.append({"preset": preset, "runs": len(es), "failed_or_killed": kills,
                         "median_rung_s": rung, "peak_rss_kb": rss,
                         "median_bytes": statistics.median(int(e["bytes"]) for e in good) if good else None,
                         "distance": es[0]["distance"] if es else None,
                         "cma_free_min_kb": enc_cma, "verdict": "PASS" if ok else "FAIL"})
        budget_rows.append({"preset": preset, "pre_capture_s (ASSUMPTION)": PRE_CAPTURE_S,
                            "capture_raw_median_s": med_raw, "prep_s (Sprint08)": PREP_S,
                            "dng_planes_s (ESTIMATE)": 1.0, "two_rungs_s": None if rung is None else 2 * rung,
                            "burst_s (176+2 msgs x 1.3)": (BURST_MSGS + 2) * PACE_S,
                            "tail_s": TAIL_S, "predicted_end_s": wake, "budget_s": BUDGET_S,
                            "fits": wake is not None and wake <= BUDGET_S})
        presets.append((preset, ok))
    for name, rows_ in ((f"r0_encode_{host}.csv", enc_rows), (f"r0_budget_{host}.csv", budget_rows)):
        if rows_:
            with open(os.path.join(out, name), "w", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(rows_[0]))
                wr.writeheader()
                wr.writerows(rows_)
    guard = next((ln for ln in env.splitlines() if ln.startswith("guard ")), "")
    try:
        guard_kind = json.loads(guard[len("guard "):]).get("kind")
    except ValueError:
        guard_kind = None
    r04 = "cjxl: /" in env and "numpy " in env and guard_kind == "mem"
    largest = None
    for preset, ok in presets:                      # probed smallest first
        if ok:
            largest = preset
        else:
            break
    verdict = {"host": host,
               "R0.1": {"verdict": "NOT MEASURED" if r01 is None else "PASS" if r01 else "FAIL",
                        "raw_ok": f"{ok_raw}/{len(raw)}",
                        "cma_alloc_check": cev["source"], "cma_counter_deltas": cev["counters"],
                        "cma_dmesg_failures": cev["dmesg_failures"][:10],
                        "raw_capture_alloc_errors": cev["stderr_errors"][:10],
                        "info_cma_debugfs": cev["debugfs"],
                        "info_cma_free_min_kb_raw": raw_cma,
                        "info_cma_free_min_kb_plain": plain_cma,
                        "dmesg_errors": dmesg_hits[:10]},
               "R0.2": {"verdict": "PASS" if r02 else "FAIL", "median_raw_s": med_raw,
                        "median_plain_s": med_plain, "delta_s": delta},
               "R0.3": {"verdict": "PASS" if presets and presets[0][1] else "FAIL",
                        "presets": dict(presets), "largest_passing_preset": largest},
               "R0.4": {"verdict": "PASS" if r04 else "FAIL",
                        "env": [ln for ln in env.splitlines() if ln.startswith(("cjxl", "numpy"))],
                        "memory_guard_kind": guard_kind}}
    with open(os.path.join(out, f"r0_verdict_{host}.json"), "w") as fh:
        json.dump(verdict, fh, indent=1)
    for k in ("R0.1", "R0.2", "R0.3", "R0.4"):
        print(f"| {k} | {host} | {verdict[k]['verdict']} | "
              + json.dumps({a: b for a, b in verdict[k].items() if a != 'verdict'}) + " |")
    verdicts = [verdict[k]["verdict"] for k in ("R0.1", "R0.2", "R0.3", "R0.4")]
    if "NOT MEASURED" in verdicts:
        return 2
    return 0 if all(v == "PASS" for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
