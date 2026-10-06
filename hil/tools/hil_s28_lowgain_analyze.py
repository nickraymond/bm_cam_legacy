#!/usr/bin/env python3
# filename: hil_s28_lowgain_analyze.py
# description: Sprint28 sunrise low-gain test, after the run (desk): per auto/low_gain pair, shutter + gain vs lux, the noise in a flat patch of the RAW crop, the low_gain rule checks, and a cut sheet of matched pairs.
"""
Input: the pulled folder of hil_s28_lowgain_sunrise.sh (pairs.csv, pairs/<n>_<profile>/
{raw_crop.pgm, raw_crop.json, metadata.json, capture_info.json, stderr.log}, run_manifest.json).

Rule checks on every low_gain capture (cap = max_shutter_us, floor = the sensor's analogue gain
floor, 1.1228 on the IMX708; tolerances below are ASSUMPTIONS, labelled in the output):
  LG1 shutter <= cap x 1.01 (the sensor rounds to lines);
  LG2 gain above the floor (> floor x 1.05) only when the shutter is at the cap (>= cap x 0.95);
  LG3 daylight: when the AUTO member's exposure needs no more than cap at the floor
      (auto ExposureTime x AnalogueGain <= cap x floor x 1.05), low_gain's gain is <= floor x 1.05;
  LG4 the patched tuning file loaded: libcamera's "tuning file" line names exposure_profile/tuning/
      (and auto's does not); capture_info exposure_tuning_file_used true.
Noise (RAW, flat patch): the 64x64-sample window of the AUTO member's green plane with the lowest
  local contrast among windows whose mean is above the 30th percentile (so not the darkest
  corner); the SAME window is used for its low_gain partner. Noise = std(G1 - G2) / sqrt(2) and,
  for red, std(R[x] - R[x+1]) / sqrt(2) (adjacent same-colour samples: scene texture cancels in a
  flat patch, sensor noise does not), in DN above black, and SNR = mean / noise. The low_gain /
  auto ratio of each is the headline.
Cut sheet (lowgain_cutsheet.png): N pairs evenly spaced in time; per pair the auto and low_gain
  crops rendered from the RAW at HALF resolution (R, mean(G1, G2), B planes, no demosaic; camera
  WB x CCM from the capture's metadata; each member scaled so its flat patch has the same display
  level, so noise is compared at equal brightness) and a 2x nearest zoom of the flat patch.
Outputs (--out, default <input>/analysis): lowgain_pairs.csv, lowgain_summary.json,
  lowgain_cutsheet.png.
Example: .venv-dev/bin/python hil/tools/hil_s28_lowgain_analyze.py \\
           runs/s28_lowgain_sunrise_20261006/pulled/bmcam004_lowgain
"""

import argparse
import csv
import json
import math
import os
import sys

FLOOR = 1.1228          # IMX708 analogue gain floor (measured, Sprint28 P0 / R4 57534)
TOL_CAP, TOL_AT_CAP, TOL_FLOOR = 1.01, 0.95, 1.05
WIN = 64


def read_pgm(path):
    import numpy as np
    with open(path, "rb") as fh:
        data = fh.read()
    magic, wh, maxval, body = data.split(b"\n", 3)
    w, h = (int(v) for v in wh.split())
    dt = ">u2" if int(maxval) > 255 else "u1"
    return np.frombuffer(body, dt).reshape(h, w).astype(np.float64)


def planes(mos, cfa):
    out, greens = {}, []
    for i, c in enumerate(cfa):
        dy, dx = i // 2, i % 2
        if c == "G":
            greens.append(mos[dy::2, dx::2])
        else:
            out[c] = mos[dy::2, dx::2]
    out["G1"], out["G2"] = greens
    return out


def flat_window(g, black):
    import numpy as np
    h, w = g.shape
    best, thr = None, np.percentile(g, 30)
    for y in range(0, h - WIN, WIN // 2):
        for x in range(0, w - WIN, WIN // 2):
            p = g[y:y + WIN, x:x + WIN]
            m = p.mean()
            if m <= thr:
                continue
            c = p.std() / max(m - black, 1.0)
            if best is None or c < best[0]:
                best = (c, y, x)
    return (best[1], best[2]) if best else (0, 0)


def noise(pl, black, y, x):
    import numpy as np
    s = np.s_[y:y + WIN, x:x + WIN]
    g1, g2, r = pl["G1"][s], pl["G2"][s], pl["R"][s]
    ng = float(np.std(g1 - g2) / math.sqrt(2))
    nr = float(np.std(r[:, 1:] - r[:, :-1]) / math.sqrt(2))
    mg, mr = float((g1.mean() + g2.mean()) / 2 - black), float(r.mean() - black)
    return {"g_mean_dn": round(mg, 2), "g_noise_dn": round(ng, 3), "g_snr": round(mg / ng, 2) if ng else None,
            "r_mean_dn": round(mr, 2), "r_noise_dn": round(nr, 3), "r_snr": round(mr / nr, 2) if nr else None}


def render_half(pl, meta, black, white, level, y, x):
    import numpy as np
    rgb = np.stack([pl["R"], (pl["G1"] + pl["G2"]) / 2, pl["B"]], -1)
    rgb = np.clip((rgb - black) / (white - black), 0, None)
    gains = meta.get("ColourGains") or [1.0, 1.0]
    rgb = rgb * np.array([gains[0], 1.0, gains[1]]) * float(meta.get("DigitalGain") or 1.0)
    ccm = np.array(meta.get("ColourCorrectionMatrix") or [1, 0, 0, 0, 1, 0, 0, 0, 1]).reshape(3, 3)
    rgb = np.clip(rgb @ ccm.T, 0, None)
    patch = rgb[y:y + WIN, x:x + WIN, 1].mean()
    rgb = np.clip(rgb * (level / max(patch, 1e-9)), 0, 1)       # equal display level
    s = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return (np.clip(s, 0, 1) * 255 + 0.5).astype(np.uint8)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src")
    ap.add_argument("--out")
    ap.add_argument("--sheet-pairs", type=int, default=8)
    args = ap.parse_args(argv)
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        print(f"[lowgain-analyze] needs numpy + Pillow ({exc}). Run it with the repo venv: "
              ".venv-dev/bin/python hil/tools/hil_s28_lowgain_analyze.py <pulled dir> "
              "(or the rig venv ~/Documents/GitHub/nereus-camera-test-rig/.venv/bin/python)",
              file=sys.stderr)
        return 3
    out = args.out or os.path.join(args.src, "analysis")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(args.src, "run_manifest.json")) as fh:
        manifest = json.load(fh)
    cap = float(manifest["low_gain"]["max_shutter_us"])
    with open(os.path.join(args.src, "pairs.csv")) as fh:
        caps = list(csv.DictReader(fh))
    by = {}
    for c in caps:
        by.setdefault(int(c["pair"]), {})[c["profile"]] = c
    rows, sheet_rows = [], []
    f = lambda v: float(v) if v not in (None, "", "None") else None  # noqa: E731
    for n in sorted(by):
        a, lg = by[n].get("auto"), by[n].get("low_gain")
        if not a or not lg or a["ok"] != "True" or lg["ok"] != "True":
            rows.append({"pair": n, "complete": False})
            continue
        ea, ga, el, gl = f(a["exposure_us"]), f(a["analogue_gain"]), f(lg["exposure_us"]), f(lg["analogue_gain"])
        need = ea * ga
        lg1 = el <= cap * TOL_CAP
        lg2 = not (gl > FLOOR * TOL_FLOOR) or el >= cap * TOL_AT_CAP
        daylight = need <= cap * FLOOR * TOL_FLOOR
        lg3 = (gl <= FLOOR * TOL_FLOOR) if daylight else None
        lg4 = ("exposure_profile/tuning" in lg["tuning_file_logged"]
               and "exposure_profile/tuning" not in a["tuning_file_logged"]
               and lg.get("exposure_tuning_file_used") in ("True", True))
        da, dl = (os.path.join(args.src, "pairs", c["dir"]) for c in (a, lg))
        with open(os.path.join(da, "raw_crop.json")) as fh:
            rj = json.load(fh)
        pa, pl = (planes(read_pgm(os.path.join(d, "raw_crop.pgm")), rj["cfa"]) for d in (da, dl))
        y, x = flat_window((pa["G1"] + pa["G2"]) / 2, rj["black"])
        na, nl = noise(pa, rj["black"], y, x), noise(pl, rj["black"], y, x)
        row = {"pair": n, "complete": True, "utc": a["utc"], "lux": f(a["lux"]),
               "auto_exposure_us": ea, "auto_gain": ga, "low_exposure_us": el, "low_gain": gl,
               "auto_total": round(need, 1), "low_total": round(el * gl, 1),
               "daylight": daylight, "LG1_shutter_le_cap": lg1, "LG2_gain_only_at_cap": lg2,
               "LG3_floor_in_daylight": lg3, "LG4_patched_tuning_loaded": lg4,
               "patch_xy_halfres": [int(x), int(y)],
               **{f"auto_{k}": v for k, v in na.items()}, **{f"low_{k}": v for k, v in nl.items()},
               "g_noise_ratio_low_over_auto": round(nl["g_noise_dn"] / na["g_noise_dn"], 3) if na["g_noise_dn"] else None,
               "r_noise_ratio_low_over_auto": round(nl["r_noise_dn"] / na["r_noise_dn"], 3) if na["r_noise_dn"] else None,
               "g_snr_ratio_low_over_auto": round(nl["g_snr"] / na["g_snr"], 3) if na["g_snr"] else None,
               "r_snr_ratio_low_over_auto": round(nl["r_snr"] / na["r_snr"], 3) if na["r_snr"] else None}
        rows.append(row)
        sheet_rows.append((row, da, dl, rj, y, x))
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(os.path.join(out, "lowgain_pairs.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    done = [r for r in rows if r.get("complete")]

    def frac(k):
        vals = [r[k] for r in done if r[k] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "n/a"
    med = lambda k: (sorted(r[k] for r in done if r.get(k) is not None) or [None])[len([r for r in done if r.get(k) is not None]) // 2]  # noqa: E731
    summary = {"src": args.src, "manifest": manifest, "pairs": len(rows), "complete_pairs": len(done),
               "cap_us": cap, "floor_gain": FLOOR,
               "tolerances_ASSUMED": {"cap": TOL_CAP, "at_cap": TOL_AT_CAP, "floor": TOL_FLOOR},
               "LG1": frac("LG1_shutter_le_cap"), "LG2": frac("LG2_gain_only_at_cap"),
               "LG3_daylight_pairs": frac("LG3_floor_in_daylight"), "LG4": frac("LG4_patched_tuning_loaded"),
               "median_g_noise_ratio": med("g_noise_ratio_low_over_auto"),
               "median_r_noise_ratio": med("r_noise_ratio_low_over_auto"),
               "median_r_snr_ratio": med("r_snr_ratio_low_over_auto"),
               "PASS": bool(done) and all(r["LG1_shutter_le_cap"] and r["LG2_gain_only_at_cap"]
                                          and r["LG4_patched_tuning_loaded"]
                                          and r["LG3_floor_in_daylight"] is not False for r in done)}
    with open(os.path.join(out, "lowgain_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)

    # ---- cut sheet -----------------------------------------------------------------------
    if sheet_rows:
        k = max(1, len(sheet_rows) // args.sheet_pairs)
        pick = sheet_rows[::k][:args.sheet_pairs]
        tw, th = 400, 225                     # half-res crop tile; zoom tile = th x th
        sheet = Image.new("RGB", (2 * tw + 2 * th + 40, 40 + len(pick) * (th + 30)), "white")
        dr = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        dr.text((8, 6), f"Sprint28 sunrise low-gain | {manifest.get('host')} | cap {cap:.0f} us, max gain "
                f"{manifest['low_gain']['max_gain']} | RAW still.crop at HALF res (R, G, B planes, no demosaic), "
                "camera WB x CCM; each member scaled to the same flat-patch level (noise at equal brightness); "
                f"zoom = the {WIN}x{WIN} flat patch, nearest", fill=(0, 0, 0), font=font)
        for i, (r, da, dl, rj, y, x) in enumerate(pick):
            top = 34 + i * (th + 30)
            imgs = []
            for d in (da, dl):
                with open(os.path.join(d, "metadata.json")) as fh:
                    meta = json.load(fh)
                imgs.append(render_half(planes(read_pgm(os.path.join(d, "raw_crop.pgm")), rj["cfa"]),
                                        meta, rj["black"], rj["white"], 0.45, y, x))
            for j, (img, who) in enumerate(zip(imgs, ("auto", "low"))):
                sheet.paste(Image.fromarray(img).resize((tw, th)), (8 + j * (tw + 8), top + 14))
                z = Image.fromarray(img[y:y + WIN, x:x + WIN]).resize((th, th), Image.NEAREST)
                sheet.paste(z, (24 + 2 * tw + j * (th + 8), top + 14))
            dr.text((8, top), f"pair {r['pair']} {r['utc']} lux {r['lux']} | auto {r['auto_exposure_us']:.0f} us x "
                    f"{r['auto_gain']:.2f} | low_gain {r['low_exposure_us']:.0f} us x {r['low_gain']:.2f} | "
                    f"R noise x{r['r_noise_ratio_low_over_auto']} G noise x{r['g_noise_ratio_low_over_auto']} | "
                    f"LG1 {r['LG1_shutter_le_cap']} LG2 {r['LG2_gain_only_at_cap']} LG3 {r['LG3_floor_in_daylight']} "
                    f"LG4 {r['LG4_patched_tuning_loaded']}", fill=(0, 0, 0), font=font)
        sheet.save(os.path.join(out, "lowgain_cutsheet.png"))
    print(json.dumps({k: summary[k] for k in ("pairs", "complete_pairs", "LG1", "LG2", "LG3_daylight_pairs",
                                              "LG4", "median_g_noise_ratio", "median_r_noise_ratio",
                                              "median_r_snr_ratio", "PASS")}, indent=1))
    return 0 if summary["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
