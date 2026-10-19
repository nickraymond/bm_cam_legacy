#!/usr/bin/env python3
"""gen_matrix.py: spec must do 1, matrix.csv draft for c_cmd_coverage (TE3, Mon 10/19).

Inputs:  config_registry.py @ the worktree (== S2 2cc1afdb for registry/command_v9/command_guards), catalog_v14.json
         (GET /remote-config/catalog on nereus000, 2026-10-19, sha256 prefix fb560f88ebcc), OVERRIDES below (hand-picked
         test/invalid values and effects for rows where a mechanical choice would be unsafe or unobservable).
Outputs: matrix.csv: one row per verb (10) and per key (115): tier (1/2), catalog tier + reason, type, default, enum/range,
         guard + guard_when, apply, media, test value, invalid value, expected observable effect, how observed, batch,
         spec section (3 / 3b / 4), plus empty verdict/evidence columns filled during the run.
Rules:   Tier 1 = the 10 verbs + every key the backend can send (catalog tier control/engineering) + the crop keys.
         locked = refusal row only; service = unsigned refusal only; guarded = must do 3b rows (no cfm on command-path keys).
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/eloquent-haibt-613335/BM_Devel_Pi")
import config_registry as R  # noqa: E402

CAT = {k["path"]: k for k in json.load(open(os.path.join(HERE, "catalog_v14.json")))["keys"]}
CROP_KEYS = {"still.crop", "video.record.crop", "still.output_width"}

# hand-picked rows: (test value, invalid value, expected effect / how observed, batch)
OVERRIDES = {
    "schedule.window.enabled": (False, "maybe", "boots outside 07:00-12:00 capture (<WS a=cap>, not skip_win); FIRST row, kept for the run", "B00-first"),
    "schedule.window.start": ("08:00", "25:61", "<WS ... ws=0800>; capture gate moves (checked with enabled true, last batch)", "B90-window"),
    "schedule.window.end": ("11:00", "11:5", "<WS ... we=1100>", "B90-window"),
    "schedule.timezone": ("America/New_York", "Mars/Base", "KNOWN: dropped at boot ([CFG][ERR] tzdata, c_tz_overlay_boot) -> expected LIMITED", "B91-tz"),
    "still.crop": ([1804, 1014, 1000, 562], [0, 0, 4608, 2592], "[RAW] crop=[1804,1014,1000,562]; image 1000x562; invalid refused (> RAW_MAX_PX)", "B10-geometry"),
    "still.output_width": (800, 5000, "pjpg path only: sent width 800 (observable only with still.format pjpg; batch B11)", "B11-pjpg"),
    "still.format": ("pjpg", "png", "image sent as progressive JPEG (no [RAW] nrjxl lines; START fmt)", "B11-pjpg"),
    "still.quality_ladder": ([20, 15, 11, 9], [200], "pjpg path: JPEG qualities tried (log); with still.format pjpg", "B11-pjpg"),
    "still.message_cap": (150, 0, "[RAW] search room <= 150 msgs; msgs <= 150", "B20-msgcap"),
    "still.budget_min": (12, 31, "cycle budget 12 min in [RC] budget line", "B21-budget"),
    "still.raw.rgb_encode_max_s": (5, 121, "ANTI-BRICK (must do 5): rfb=time JPEG fallback on that wake + next 2; ping + reset OK; JPEG XL after reset", "B30-antibrick"),
    "still.raw.distances": ([3.0, 4.0, 5.0, 7.0], [0.0], "[RAW] fallback ladder uses 3.0..7.0 (visible only if the search fails; else NOT OBSERVABLE)", "B31-raw-a"),
    "still.raw.target_fill": (0.8, 1.5, "[RAW] search target ~0.8 x room: fewer msgs (~155)", "B32-raw-b"),
    "still.raw.d_max": (2.0, 16.0, "[RAW] floor d<=2.0: search can't go coarser (rfb=floor if it must)", "B33-raw-c"),
    "still.raw.progressive": (False, "maybe", "[RAW][cjxl] argv without -p", "B34-raw-d"),
    "still.raw.layout": ("bayer4", "rgb4", "[RAW] planes (bayer4, cjxl -m 1) instead of rgb (B3a)", "B35-raw-e"),
    "still.raw.encode_max_s": (20, 121, "bayer4-only cap: NOT OBSERVABLE under layout rgb (stated)", "B36-raw-f"),
    "still.raw.keep_crop": (True, "maybe", "a *_raw_crop.pgm kept in images/ (SD check C4)", "B37-raw-g"),
    "still.raw.effort": (4, 8, "[RAW][cjxl] -e 4; search seconds change", "B38-raw-h"),
    "camera.focus.lens_position": (1.0, 33.0, "capture metadata LensPosition 1.0", "B40-focus"),
    "camera.focus.mode": ("manual", "zoom", "already manual in the soak overlay: re-set is a no-op -> expected NOT OBSERVABLE unless reset first", "B40-focus"),
    "camera.exposure.max_shutter_us": (40000, 70000, "capture ExposureTime <= 40000 us", "B50-exposure"),
    "camera.exposure.max_gain": (2.0, 17.0, "capture AnalogueGain <= 2.0", "B50-exposure"),
    "camera.exposure.profile": ("auto", "night", "profile auto (soak overlay low_gain): AE limits back to defaults", "B51-exposure-profile"),
    "camera.image_processing.contrast": (1.2, 2.5, "rpicam --contrast 1.2 in the capture argv/metadata", "B60-ip"),
    "camera.image_processing.saturation": (0.8, 3.0, "rpicam --saturation 0.8", "B60-ip"),
    "camera.image_processing.brightness": (0.1, 0.5, "rpicam --brightness 0.1", "B60-ip"),
    "camera.image_processing.sharpness": (2.0, 17.0, "rpicam --sharpness 2.0", "B60-ip"),
    "mode.run": ("stay_on", "always", "next boot stays on (supervisor stay_on); reset -> per_boot; wake-time row on its own", "B70-run"),
    "mode.interval_s": (900, 90000, "stay_on interval (only with mode.run stay_on; batch B70)", "B70-run"),
    "mode.heartbeat_s": (600, -1, "stay_on heartbeat (only with stay_on; B70)", "B70-run"),
    "mode.media": ("video", "video_logger", "next boot records + sends a video clip; video_logger refused by the backend (blocked value); camera: check", "B80-video"),
    "mode.output": ("save_local", "tape", "must do 3b guarded_revert: set + cfm -> saves locally, no transmit; survives boot3_2h; and the no-cfm revert row", "B3b-output"),
    "camera.native.jpeg_quality": (90, 101, "native JPEG saved at q90 (file size drops)", "B41-native"),
    "camera.exposure.shutter_us": (20000, 120000001, "capture ExposureTime 20000 us (needs exposure.enabled true, same batch)", "B52-exposure-manual"),
    "camera.exposure.ev": (-1.0, 9.0, "capture ExposureValue -1.0 / darker image (needs exposure.enabled)", "B52-exposure-manual"),
    "camera.exposure.analogue_gain": (2.0, 70.0, "capture AnalogueGain 2.0 (needs exposure.enabled)", "B52-exposure-manual"),
    "camera.exposure.enabled": (True, "maybe", "manual exposure group applied (shutter/ev/gain rows ride on it)", "B52-exposure-manual"),
    "camera.exposure.mode": ("normal", 5, "reported as END rem only; builds no flag (help) -> expected NOT OBSERVABLE beyond END", "B52-exposure-manual"),
    "video.record.framing": ("wide_720p_lean", "zz_preset", "video clip geometry = wide_720p_lean preset (with mode.media video, B80)", "B80-video"),
    "video.record.sensor_mode": ("2304:1296", "zz", "rpicam-vid --mode 2304:1296 in the video argv (B80)", "B81-video-geom"),
    "video.record.crop": ([0, 0, 4608, 2592], [1, 1, 3, 3], "video ROI full sensor (B81)", "B81-video-geom"),
    "video.send.duration_s": (4.0, 31.0, "clip 4 s (5 s is the only ladder-validated value; reset after)", "B80-video"),
    "video.send.budget_min": (15, 31, "video cycle budget 15 min", "B80-video"),
    "uplink.media_key.heal_offwin_max": (20, 41, "off-window [HEAL] cap=20 (needs window enabled; batch B90)", "B90-window"),
}


def default_test(k, cat):
    blocked = {json.dumps(b["value"]) for b in (cat.get("blocked_values") or [])} if cat else set()
    t, d = k.type, k.default
    if t == "bool":
        return (not d) if d is not None else True, "maybe"
    if t in ("enum", "str") and k.enum:
        for v in k.enum:
            if v != d and json.dumps(v) not in blocked:
                return v, "zz_invalid"
    if t == "bool_or_str":
        return "off", "maybe"
    if t in ("int", "float") and k.range:
        lo, hi = k.range
        base = d if d is not None else lo
        step = (hi - lo) * 0.05
        v = base + step if base + step <= hi else base - step
        v = int(round(v)) if t == "int" else round(v, 2)
        if v == d:
            v = lo if lo != d else hi
        bad = (hi + 1) if t == "int" else round(hi + abs(hi - lo) * 0.1 + 0.5, 2)
        return v, bad
    if t == "hhmm":
        return "08:00", "25:61"
    if t == "wxh":
        return "640x360", "0x0"
    if t == "gains":
        return [2.0, 1.5], [9.0, 1.0]
    if t in ("ladder",):
        return [20, 15, 11, 9], [200]
    if t == "dladder":
        return [3.0, 4.0, 5.0, 7.0], [0.0]
    return None, "zz_invalid"


def main():
    rows = []
    for v in ["ping", "help", "get", "set", "reset", "cfm", "trg", "hld", "rsd", "wap"]:
        rows.append({"row": f"verb:{v}", "tier": 1, "section": "4", "batch": "V-verbs", "kind": "verb",
                     "test": {"ping": "ping", "help": "help", "get": "get key / short / group / journal",
                              "set": "covered by every key row + all-or-none pair (1 valid + 1 invalid)",
                              "reset": "key, group, {all:1} once then restore", "cfm": "on power.halt.enabled (3b)",
                              "trg": "v 0..4", "hld": "v 5 then 0", "rsd": "valid 0-39; 41 refused; 9 pairs refused; same-key replace; x:1",
                              "wap": "0..2, WiFi off after"}[v],
                     "invalid": {"rsd": "41 chunks / 9 pairs", "trg": "v 5", "hld": "v -1", "wap": "v 3"}.get(v, "unknown verb 'zz'"),
                     "expected": "ack per command_v9.render_help; spec must do 4"})
    for k in R.KEYS:
        cat = CAT.get(k.path, {})
        ctier = cat.get("tier", "?")
        tier = 1 if ctier in ("control", "engineering") or k.path in CROP_KEYS else 2
        if k.path in OVERRIDES:
            tv, bad, eff, batch = OVERRIDES[k.path]
        else:
            tv, bad = default_test(k, cat)
            eff = "per help: " + (k.help.split(". ")[0][:110] if k.help else "")
            grp = k.path.split(".")
            batch = "B-" + ".".join(grp[:2])
        section = "3"
        if k.guard == R.LOCKED if hasattr(R, "LOCKED") else k.guard == "locked":
            section, tv, eff, batch = "3b-locked", None, "refused (locked: deploy only)", "B-refusals"
        elif k.guard == "service":
            section, tv, eff, batch = "3b-service", None, "unsigned set refused", "B-refusals"
        elif k.guard in ("guarded_stage", "guarded_revert") and k.path not in ("mode.output",):
            section = "3b-" + k.guard
            if k.path in ("commands.enabled", "commands.topic", "uplink.uart.port", "uplink.uart.baudrate"):
                eff = "NO cfm on the bench: applies, then reverts by itself within tx2_boot3; ping + reset after (UNSAFE-by-design if confirmed)"
            else:
                eff = "staged; NOT applied before cfm; applied after cfm"
            batch = "B3b-" + k.path
        rows.append({"row": k.path, "tier": tier, "section": section, "batch": batch, "kind": "key",
                     "short": k.short or "", "type": k.type, "default": json.dumps(k.default),
                     "enum": json.dumps(list(k.enum)) if k.enum else "", "range": json.dumps(list(k.range)) if k.range else "",
                     "nullable": k.nullable, "guard": k.guard, "guard_when": json.dumps(k.guard_when), "apply": k.apply,
                     "media": k.validate_when or "", "catalog_tier": ctier, "catalog_reason": cat.get("tier_reason") or "",
                     "blocked_values": json.dumps([b["value"] for b in (cat.get("blocked_values") or [])]),
                     "test": json.dumps(tv), "invalid": json.dumps(bad), "expected": eff})
    cols = ["row", "tier", "section", "batch", "kind", "short", "type", "default", "enum", "range", "nullable", "guard",
            "guard_when", "apply", "media", "catalog_tier", "catalog_reason", "blocked_values", "test", "invalid",
            "expected", "verdict", "evidence"]
    with open(os.path.join(HERE, "matrix.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    t1 = [r for r in rows if r["tier"] == 1]
    print(f"rows {len(rows)}: tier1 {len(t1)} (verbs 10, keys {len(t1) - 10}), tier2 {len(rows) - len(t1)}")


if __name__ == "__main__":
    main()
