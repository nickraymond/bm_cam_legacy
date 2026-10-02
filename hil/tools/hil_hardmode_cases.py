#!/usr/bin/env python3
"""hil_hardmode_cases.py — generate the G3 "API hard mode" edge-case table from the config catalog.

Purpose
  G3 criterion "every bad value is refused before send" needs a case list that is derived
  from the catalog (the single source of truth, SPEC §3.2), not typed by hand. This script
  turns docs/bmcam_config_catalog.json into one row per (key, value) with the EXPECTED
  backend outcome, so hil_plan_check.py can score /remote-config/plan against it.

Inputs
  --catalog PATH   docs/bmcam_config_catalog.json (default)
  --out DIR        writes cases.json + cases.csv there (default .)
  --keys REGEX     only keys whose path matches (default: all)
  --invalid-per-key N  selftest vectors that are INVALID kept per control key (default 6,
                   same-type-looking values first). The nvd unit tests already run every
                   selftest vector through the ported check_value (SPEC §3.2); HIL samples them
                   end to end. --invalid-per-key 0 drops them, 999 keeps all.

Outputs
  cases.json  {"catalog_sha256", "registry_version", "generated_utc", "cases": [...]}
  cases.csv   id, kind, key, value_json, expect, expect_reason, source, why
  expect is one of
    accept   /plan must return 200 (warnings allowed)
    refuse   /plan must return 422 (expect_reason = the reason we predict; scored separately)
    depends  outcome needs the device's reported state (cross-key rules, SPEC §3.3): recorded,
             reviewed by hand, never scored PASS/FAIL automatically

Sources of the expectation (in order; the first refusal wins)
  1. tier != control                          -> refuse not_writable
  2. catalog selftest vector says invalid     -> refuse bad_value  (the unit's own check_value)
  3. limits.max / max_each / max_items        -> refuse over_limit
  1b. catalog blocked_values (e.g. mode.output save_local, "" enums) -> refuse blocked_value
  4. backend-only single-key rules (SPEC §3.3): video.send.size even and >= 16 px; interval/heartbeat
     1..59 s                                  -> refuse cross_key/bad_value
  5. keys in a cross-key rule (video geometry, video.send.size vs record output, mode.run,
     mode.media, white_balance.mode manual, crops vs native frame) -> depends
  plus structural cases (empty, mixed, unknown key, too_big, reset > 4 keys, reset of a group).

Example
  python3 hil/tools/hil_hardmode_cases.py --out runs/g3_hardmode_20261003/analysis

Limitations
  - The expectation model is a READING of SPEC r2 §2.3/§3; a mismatch found by hil_plan_check
    is a finding to review (spec, backend or this model), not automatically a backend bug.
  - `depends` rows are not scored. The device-side refusal (`e:xk`) is G3's console part.
  - Assumes catalog format 1 (keys[], selftest{path: [[value, valid], ...]}).
"""

import argparse
import csv
import datetime
import json
import os
import re

CROSS_KEY_DEPENDS = {
    "mode.run": "stay_on needs power.bus_always_on REPORTED true + commands.enabled",
    "mode.media": "video needs video.send.message_cap >= 80 (reported)",
    "video.record.framing": "video geometry must resolve (reported geometry)",
    "video.record.crop": "video geometry; framing-less crop xor output refused",
    "video.record.output": "video geometry; framing-less crop xor output refused",
    "video.record.sensor_mode": "video geometry must resolve",
    "video.record.fps": "video geometry (fps30 block above N pixels)",
    "video.send.size": "send size <= resolved record output (geometry known)",
    "video.send.fps": "warn when > resolved record fps",
    "camera.white_balance.mode": "manual needs gains (reported)",
    "still.crop": "inside the native frame when native size is known",
}
INTERVAL_KEYS = ("mode.interval_s", "mode.heartbeat_s")


def wxh_backend_ok(v):
    """SPEC §3.3: video.send.size even and >= 16 px (always). Returns None when not wxh text."""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"(\d+)x(\d+)", v)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    return w % 2 == 0 and h % 2 == 0 and w >= 16 and h >= 16


def boundary_values(k):
    """Range / enum boundary values the selftest vectors may not cover."""
    t, rng, out = k["type"], k.get("range"), []
    if rng and t in ("int", "float"):
        lo, hi = rng
        if t == "int":
            out += [lo, hi, lo - 1, hi + 1, float(lo) + 0.5]   # x.5 = not a JSON int
        else:
            d = max((hi - lo) * 1e-3, 1e-3)
            out += [lo, hi, round(lo - d, 6), round(hi + d, 6)]
        out += [str(lo), True]                                 # wrong JSON types
    if t == "enum":
        out += list(k.get("enum") or []) + ["bogus", "AUTO", 1]
    if t == "bool":
        out += [True, False, 0, 1, "true"]
    if t == "wxh":
        out += ["16x16", "14x16", "480x270", "482x272", "481x271", "640x360", "1280x720", "4608x2592", "640X360", "640x"]
    if t == "gains":
        out += [[8.0, 8.0], [8.01, 1.0], [1.0, 8.01], [0.0, 1.0], [1.8], [1.8, 1.6, 1.0]]
    if t == "ladder":
        out += [[90], [20, 15, 11, 9], [20, 15, 11, 9, 7], [9, 11], [101, 50], [0]]
    if t == "crop":
        out += [[0, 0, 4608, 2592], [1904, 1071, 800, 450], [4000, 0, 800, 450], [0, 0, 4609, 2592], [0, 0, 0, 10]]
    if t == "hhmm":
        out += ["00:00", "23:59", "24:00", "7:00", "12:60"]
    if t == "tz":
        out += ["UTC", "America/Los_Angeles", "Not/AZone", ""]
    return out


def basic_check(k, v):
    """Type / range / enum check for values the selftest vectors do not cover (SPEC A8: an INT
    key takes a JSON int only). Returns True / False, or None when the type is not modelled."""
    t, rng = k["type"], k.get("range")
    if v is None:
        return bool(k.get("nullable"))
    if t == "int":
        if isinstance(v, bool) or not isinstance(v, int):
            return False
    elif t == "float":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    elif t == "bool":
        return isinstance(v, bool)
    elif t == "enum":
        return isinstance(v, str) and v in (k.get("enum") or [])
    elif t in ("wxh", "hhmm", "tz", "str"):
        if not isinstance(v, str):
            return False
        return None
    else:
        return None
    if rng:
        return rng[0] <= v <= rng[1]
    return True


def expect_for(k, value, selftest_valid):
    """(expect, reason, why) for a set of one key to one value."""
    path, tier, lim = k["path"], k["tier"], k.get("limits") or {}
    if tier != "control":
        return "refuse", "not_writable", f"tier={tier}"
    for b in k.get("blocked_values") or []:
        if b.get("value") == value and type(b.get("value")) is type(value):
            return "refuse", "blocked_value", f"catalog blocked_values: {str(b.get('why'))[:60]}"
    if selftest_valid is None:
        selftest_valid = basic_check(k, value)
        src = "type/range model"
    else:
        src = "catalog selftest: check_value"
    if selftest_valid is False:
        return "refuse", "bad_value", f"{src} invalid"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "max" in lim and value > lim["max"]:
        return "refuse", "over_limit", f"limits.max {lim['max']}"
    if isinstance(value, list):
        if "max_each" in lim and any(isinstance(x, (int, float)) and x > lim["max_each"] for x in value):
            return "refuse", "over_limit", f"limits.max_each {lim['max_each']}"
        if "max_items" in lim and len(value) > lim["max_items"]:
            return "refuse", "over_limit", f"limits.max_items {lim['max_items']}"
    if path == "video.send.size" and wxh_backend_ok(value) is False:
        return "refuse", "cross_key", "SPEC §3.3 wxh even and >= 16 px"
    if path in INTERVAL_KEYS and isinstance(value, int) and not isinstance(value, bool) and 0 < value < 60:
        return "refuse", "cross_key", "SPEC §3.3 interval/heartbeat 0 or >= 60"
    if selftest_valid is None:
        return "depends", "", "value not in the selftest vectors: unit check_value unknown here"
    if path in CROSS_KEY_DEPENDS:
        return "depends", "", CROSS_KEY_DEPENDS[path]
    return "accept", "", "valid per check_value, inside limits"


def structural_cases():
    long_tz = "America/Argentina/ComodRivadavia"
    return [
        ("empty_set", {"set": {}}, "refuse", "empty"),
        ("empty_reset", {"reset": []}, "refuse", "empty"),
        ("mixed", {"set": {"camera.exposure.ev": 0.0}, "reset": ["camera.exposure.ev"]}, "refuse", "mixed"),
        ("unknown_key", {"set": {"camera.exposure.evv": 0.0}}, "refuse", "unknown_key"),
        ("group_prefix_set", {"set": {"camera.exposure": {"ev": 0.0}}}, "refuse", "unknown_key"),
        ("reset_group_prefix", {"reset": ["camera.exposure"]}, "refuse", "unknown_key"),
        ("reset_5_keys", {"reset": ["camera.exposure.ev", "camera.exposure.shutter_us", "camera.exposure.analogue_gain",
                                    "camera.exposure.enabled", "camera.controls_enabled"]}, "refuse", "too_big"),
        ("reset_4_keys", {"reset": ["camera.exposure.ev", "camera.exposure.shutter_us", "camera.exposure.analogue_gain",
                                    "camera.exposure.enabled"]}, "accept", ""),
        ("exposure_5_keys_fits", {"set": {"camera.controls_enabled": True, "camera.exposure.enabled": True,
                                          "camera.exposure.ev": -1.0, "camera.exposure.shutter_us": 10000,
                                          "camera.exposure.analogue_gain": 2.0}}, "accept", ""),
        ("exposure_plus_wb_too_big", {"set": {"camera.controls_enabled": True, "camera.exposure.enabled": True,
                                              "camera.exposure.ev": -1.0, "camera.exposure.shutter_us": 10000,
                                              "camera.exposure.analogue_gain": 2.0, "camera.white_balance.enabled": True,
                                              "camera.white_balance.mode": "manual",
                                              "camera.white_balance.gains": [1.8, 1.6]}}, "refuse", "too_big"),
        ("tz_long_plus_window", {"set": {"schedule.timezone": long_tz, "schedule.window.enabled": True,
                                         "schedule.window.start": "06:00", "schedule.window.end": "18:00",
                                         "still.message_cap": 200, "still.budget_min": 10}}, "depends",
         ""),
        ("int_as_float", {"set": {"still.message_cap": 200.0}}, "refuse", "bad_value"),
        ("blocked_key_locked", {"set": {"commands.runtime": "x"}}, "refuse", "not_writable"),
        ("reset_engineering_key", {"reset": ["still.save.quality"]}, "refuse", "not_writable"),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", default="docs/bmcam_config_catalog.json")
    ap.add_argument("--out", default=".")
    ap.add_argument("--keys", default="")
    ap.add_argument("--invalid-per-key", type=int, default=6)
    a = ap.parse_args()
    cat = json.load(open(a.catalog))
    if cat.get("format") != 1:
        raise SystemExit(f"[cases] catalog format {cat.get('format')} != 1: update this generator")
    selftest = cat["selftest"]
    rx = re.compile(a.keys) if a.keys else None
    cases, n = [], 0

    def add(kind, key, body, expect, reason, source, why):
        nonlocal n
        n += 1
        cases.append({"id": f"c{n:04d}", "kind": kind, "key": key, "body": body, "expect": expect,
                      "expect_reason": reason, "source": source, "why": why})

    for k in cat["keys"]:
        p = k["path"]
        if rx and not rx.search(p):
            continue
        vec = {json.dumps(v, sort_keys=True): ok for v, ok in selftest.get(p, [])}
        seen = set()
        if k["tier"] != "control":
            # one set (the default or a valid vector) + the reset below: refused by tier
            v = k.get("default")
            add("set", p, {"set": {p: v}}, "refuse", "not_writable", "tier", f"tier={k['tier']}")
            add("reset", p, {"reset": [p]}, "refuse", "not_writable", "reset", f"tier={k['tier']}")
            continue
        vectors = selftest.get(p, [])
        valid = [v for v, ok in vectors if ok]
        invalid = [v for v, ok in vectors if not ok]
        # same-type-looking invalid values first (numbers for numeric keys, strings for text...)
        def score(v):
            t = k["type"]
            num = isinstance(v, (int, float)) and not isinstance(v, bool)
            return 0 if ((t in ("int", "float") and num) or (t in ("enum", "wxh", "hhmm", "tz", "str") and isinstance(v, str))
                         or (t in ("crop", "gains", "ladder") and isinstance(v, list))) else 1
        invalid = sorted(invalid, key=score)[:max(0, a.invalid_per_key)]
        for v in valid + invalid:
            ok = vec[json.dumps(v, sort_keys=True)]
            e, r, w = expect_for(k, v, ok)
            add("set", p, {"set": {p: v}}, e, r, "selftest", w)
            seen.add(json.dumps(v, sort_keys=True))
        for v in boundary_values(k):
            j = json.dumps(v, sort_keys=True)
            if j in seen:
                continue
            seen.add(j)
            e, r, w = expect_for(k, v, vec.get(j))
            add("set", p, {"set": {p: v}}, e, r, "boundary", w)
        add("reset", p, {"reset": [p]}, "accept", "", "reset", "reset of a control key")
    if not rx:
        for name, body, e, r in structural_cases():
            add("structural", name, body, e, r, "structural", "SPEC §2.3/§3.1")

    os.makedirs(a.out, exist_ok=True)
    doc = {"catalog": a.catalog, "catalog_sha256": cat.get("sha256"), "registry_version": cat.get("registry_version"),
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "cases": cases}
    json.dump(doc, open(os.path.join(a.out, "cases.json"), "w"), indent=1)
    with open(os.path.join(a.out, "cases.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "kind", "key", "body_json", "expect", "expect_reason", "source", "why"])
        for c in cases:
            w.writerow([c["id"], c["kind"], c["key"], json.dumps(c["body"]), c["expect"], c["expect_reason"],
                        c["source"], c["why"]])
    cnt = {}
    for c in cases:
        cnt[c["expect"]] = cnt.get(c["expect"], 0) + 1
    print(f"[cases] {len(cases)} cases from {a.catalog} (sha256 {str(cat.get('sha256'))[:12]}): {cnt}")
    print(f"[cases] wrote {a.out}/cases.json and cases.csv")


if __name__ == "__main__":
    main()
