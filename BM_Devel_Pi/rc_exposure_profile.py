#!/usr/bin/env python3
# filename: rc_exposure_profile.py
# description: Sprint28 low-gain exposure for stills: a per-unit copy of the IMX708 libcamera tuning file whose AGC "normal" mode holds the analogue gain at the sensor floor until the shutter reaches still-exposure cap, then raises gain (Nick's rule).
"""
camera.exposure.profile = low_gain (Nick, 2026-10-05, RELEASE_PLAN §2c; via the EM):
  analogue gain at the sensor floor; AE varies the SHUTTER up to a cap
  (camera.exposure.max_shutter_us, default 16667 = 1/60 s); gain rises only once the shutter
  is capped (up to camera.exposure.max_gain); past that the RAW is darker, never a longer
  shutter. Why: colour correction must not amplify red-channel noise from analogue gain.

How (rpicam-apps 1.12 / libcamera 0.7.1, IMX708; facts from the bmcam004 P0 probe:
`--exposure` offers only normal|sport, `--tuning-file` exists, ExposureTime max 66666 us):
  `--gain` + `--shutter 0` alone fixes the gain forever and cannot cap the shutter, so instead
  the still capture gets `--tuning-file <copy>`: the system tuning JSON with every rpi.agc
  channel's exposure_modes["normal"] replaced by
      {"shutter": [100, max_shutter_us], "gain": [1.0, max_gain]}
  The RPi AGC divides an exposure stage by stage: shutter up to shutter[i] at gain[i-1], then
  gain up to gain[i]. So it raises the shutter to the cap at gain 1.0 (the sensor clamps to
  its floor, 1.1228 measured on the IMX708), then the gain to max_gain; the rest goes to the
  ISP's digital gain (the JPEG), not to the RAW.
  ASSUMPTION until the bench (Tue, nereus002): the AGC honours the patched "normal" mode in a
  --tuning-file copy; the TE reads back ExposureTime / AnalogueGain from --metadata.

Never costs a capture: if the sensor model, the system tuning file or the patch is not
available, the capture runs as today (no --tuning-file) and the reason is logged and put in
the capture info (exposure_profile_applied false + why).

Config: the flat `exposure_profile:` island of the rendered camera_schedule.yaml
(config_v2 writes it only when camera.exposure.profile is not auto; absent = auto = today's
command, byte for byte). It is NOT under image_pipeline.camera_controls: the master /
exposure switches do not gate it and the v8 `exp` command (which replaces that block)
cannot drop it. A fixed shutter_us / analogue_gain overrides the AGC, so config_validate
refuses it together with low_gain.

Inputs:  the island (load_profile_config), a state dir (default <app dir>/exposure_profile).
Outputs: (extra rpicam args, info dict; every info key starts with "exposure_" and goes into
         the capture sidecar). The generated file: <state dir>/tuning/
         <model>_lowgain_s<cap>_g<gain>.json (rebuilt when the system file changes).
Example: python3 rc_exposure_profile.py --config camera_schedule.yaml   (prints the args)
Known limitations: stills only (rpicam-vid also takes --tuning-file; video is out of scope);
the sensor model is read once with `rpicam-hello --list-cameras` (else `rpicam-still
--list-cameras`) and cached.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys

import config_registry as R

PROFILES = R.BY_PATH["camera.exposure.profile"].enum
DEFAULT_CONFIG = {"profile": R.BY_PATH["camera.exposure.profile"].default,
                  "max_shutter_us": R.BY_PATH["camera.exposure.max_shutter_us"].default,
                  "max_gain": R.BY_PATH["camera.exposure.max_gain"].default}
SHUTTER_RANGE = R.BY_PATH["camera.exposure.max_shutter_us"].range
GAIN_RANGE = R.BY_PATH["camera.exposure.max_gain"].range
SYSTEM_TUNING_DIRS = ("/usr/share/libcamera/ipa/rpi/vc4", "/usr/local/share/libcamera/ipa/rpi/vc4",
                      "/usr/share/libcamera/ipa/rpi/pisp")
DEFAULT_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exposure_profile")
MODEL_RE = re.compile(r"^\s*\d+\s*:\s*([A-Za-z0-9_]+)\s*\[", re.MULTILINE)
SHUTTER_FLOOR_US = 100


class ProfileUnavailable(Exception):
    pass


def load_profile_config(config_path):
    """Read the `exposure_profile:` island (flat line parser, the still_raw pattern: no
    PyYAML). Absent island = auto. Raises ValueError naming the key on a bad value (the
    caller captures as today and logs it: never a lost still)."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith(" "):
                    in_island = line.strip() == "exposure_profile:"
                    continue
                if not in_island or ":" not in line:
                    continue
                k, v = (x.strip() for x in line.split(":", 1))
                v = v.strip('"')
                if k == "profile":
                    if v not in PROFILES:
                        raise ValueError(f"exposure_profile.profile must be "
                                         f"{'|'.join(PROFILES)}, got {v!r}")
                    cfg["profile"] = v
                elif k == "max_shutter_us":
                    lo, hi = SHUTTER_RANGE
                    if not v.isdigit() or not lo <= int(v) <= hi:
                        raise ValueError(f"exposure_profile.max_shutter_us must be {lo}..{hi}, "
                                         f"got {v!r}")
                    cfg["max_shutter_us"] = int(v)
                elif k == "max_gain":
                    lo, hi = GAIN_RANGE
                    try:
                        num = float(v)
                    except ValueError:
                        raise ValueError(f"exposure_profile.max_gain must be a number, got {v!r}")
                    if not math.isfinite(num) or not lo <= num <= hi:
                        raise ValueError(f"exposure_profile.max_gain must be {lo}..{hi}, "
                                         f"got {v!r}")
                    cfg["max_gain"] = num
    except OSError:
        pass
    return cfg


def detect_model(state_dir, run=None, timeout_s=15):
    """The libcamera sensor model (e.g. "imx708_wide"; the tuning file is <model>.json).
    Cached in <state_dir>/sensor_model.txt (the module does not change without a rebuild;
    delete the file to re-detect)."""
    cache = os.path.join(state_dir, "sensor_model.txt")
    try:
        with open(cache) as fh:
            model = fh.read().strip()
        if model:
            return model
    except OSError:
        pass
    run = run or subprocess.run
    tried = []
    m = None
    # rpicam-hello first (no capture); rpicam-still, which every unit has, lists the same
    for app in ("rpicam-hello", "rpicam-still"):
        try:
            r = run([app, "--list-cameras"], capture_output=True, text=True,
                    timeout=timeout_s, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            tried.append(f"{app} --list-cameras failed: {exc}")
            continue
        m = MODEL_RE.search((r.stdout or "") + (r.stderr or ""))
        if m:
            break
        tried.append(f"no camera model in {app} --list-cameras")
    if not m:
        raise ProfileUnavailable("; ".join(tried))
    model = m.group(1)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(cache, "w") as fh:
            fh.write(model + "\n")
    except OSError:
        pass
    return model


def system_tuning_path(model, dirs=None):
    dirs = dirs or SYSTEM_TUNING_DIRS
    for d in dirs:
        path = os.path.join(d, f"{model}.json")
        if os.path.isfile(path):
            return path
    raise ProfileUnavailable(f"no system tuning file {model}.json in {list(dirs)}")


def _agc_blocks(doc):
    """Every rpi.agc parameter block: the block itself (old format) or each of its
    "channels" (libcamera >= 0.2 multi-channel AGC)."""
    algos = doc.get("algorithms") if isinstance(doc, dict) else None
    if not isinstance(algos, list):
        raise ProfileUnavailable("tuning file has no 'algorithms' list")
    blocks = []
    for entry in algos:
        if isinstance(entry, dict) and "rpi.agc" in entry:
            agc = entry["rpi.agc"]
            chans = agc.get("channels") if isinstance(agc, dict) else None
            blocks.extend(chans if isinstance(chans, list) else [agc])
    if not blocks:
        raise ProfileUnavailable("tuning file has no rpi.agc algorithm")
    return blocks


def patch_low_gain(doc, max_shutter_us, max_gain):
    """In place: every AGC block's exposure_modes['normal'] -> the low-gain stages. -> count."""
    n = 0
    for block in _agc_blocks(doc):
        modes = block.get("exposure_modes")
        if not isinstance(modes, dict) or "normal" not in modes:
            raise ProfileUnavailable("an rpi.agc block has no exposure_modes.normal")
        modes["normal"] = {"shutter": [SHUTTER_FLOOR_US, int(max_shutter_us)],
                           "gain": [1.0, float(max_gain)]}
        n += 1
    return n


def build_tuning(model, system_path, out_dir, max_shutter_us, max_gain):
    """Write (once) the patched copy; -> its path. Rebuilt when the system file is newer."""
    name = f"{model}_lowgain_s{int(max_shutter_us)}_g{float(max_gain):g}.json"
    out = os.path.join(out_dir, name)
    try:
        if os.path.getmtime(out) >= os.path.getmtime(system_path):
            return out
    except OSError:
        pass
    try:
        with open(system_path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ProfileUnavailable(f"system tuning file unreadable: {exc}")
    patch_low_gain(doc, max_shutter_us, max_gain)
    os.makedirs(out_dir, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, out)
    return out


def still_args(exposure, state_dir=None, run=None, tuning_dirs=None, log=print):
    """-> (extra rpicam-still args, info). auto (or absent) -> ([], {}): today's command.
    state_dir / run / tuning_dirs default to the module values AT CALL TIME (the golden
    harness points them at its fake world)."""
    profile = str((exposure or {}).get("profile") or "auto").strip().lower()
    if profile != "low_gain":
        return [], {}
    state_dir = state_dir or DEFAULT_STATE_DIR
    run = run or subprocess.run
    tuning_dirs = tuning_dirs or SYSTEM_TUNING_DIRS
    cap = int(float(exposure.get("max_shutter_us") or DEFAULT_CONFIG["max_shutter_us"]))
    gain = float(exposure.get("max_gain") or DEFAULT_CONFIG["max_gain"])
    info = {"exposure_profile": "low_gain", "exposure_max_shutter_us": cap,
            "exposure_max_gain": gain}
    try:
        model = detect_model(state_dir, run=run)
        path = build_tuning(model, system_tuning_path(model, tuning_dirs),
                            os.path.join(state_dir, "tuning"), cap, gain)
    except ProfileUnavailable as exc:
        log(f"[EXP][WARN] low_gain profile unavailable ({exc}): capturing with today's "
            "auto exposure")
        info.update(exposure_profile_applied=False, exposure_profile_why=str(exc))
        return [], info
    info.update(exposure_profile_applied=True, exposure_tuning_file=path,
                exposure_sensor_model=model)
    log(f"[EXP] low_gain: shutter up to {cap} us at the gain floor, then gain up to {gain:g} "
        f"({os.path.basename(path)})")
    return ["--tuning-file", path], info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print the rpicam-still args the low-gain "
                                 "exposure profile adds for this config (builds the copy).")
    ap.add_argument("--config", required=True, help="rendered camera_schedule.yaml")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    args = ap.parse_args(argv)
    cfg = load_profile_config(args.config)
    extra, info = still_args(cfg, state_dir=args.state_dir)
    print(json.dumps({"config": cfg, "args": extra, "info": info}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
