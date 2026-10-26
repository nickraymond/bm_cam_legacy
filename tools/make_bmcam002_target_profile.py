#!/usr/bin/env python3
# filename: make_bmcam002_target_profile.py
# description: Build device_profiles/bmcam002/noaa_20261027/camera_config.yaml from the bench NOAA soak profile d95aa41d (SPEC_e4 rev 12.2 must do 2).
"""
Rebuild bmcam002's v2 target profile from the soak unit's base + overlay, so the
profile is the soak's values plus must do 2's six changes (README beside the profile).

Inputs:  BM_DEVEL_PI   runtime dir whose registry renders the file (613fb2c1's BM_Devel_Pi)
         BASE_YAML     bmcam003 backup_pre/camera_config.yaml (RC soak run folder)
         STATE_JSON    bmcam003 backup_pre/bm_command_state_v2.json (its overlay)
         OUT_YAML      where to write the target
Outputs: OUT_YAML; prints each changed key and the soak + target hashes.
Exit:    0 OK; AssertionError if the soak inputs don't hash to d95aa41d or the file doesn't round-trip.
Example (Mac; PyYAML needed, e.g. the rig repo's .venv):
  python3 tools/make_bmcam002_target_profile.py BM_Devel_Pi \
    ~/NereusData/bm_cam_legacy/2026-10-23_rc_soak_s3/backup_pre/bmcam003/camera_config.yaml \
    ~/NereusData/bm_cam_legacy/2026-10-23_rc_soak_s3/backup_pre/bmcam003/bm_command_state_v2.json \
    device_profiles/bmcam002/noaa_20261027/camera_config.yaml
Known limitations: one-off for the 10/27 update; the six changes are hard-coded on purpose.
"""
import json, sys
app, base_yaml, state_json, out = sys.argv[1:5]
sys.path.insert(0, app)
import config_v2 as C, config_migrate as M, config_registry as R
cfg = C.load_config(base_yaml, strict=True)
vals = dict(cfg.base)
vals.update(json.load(open(state_json))["overlay"])
vals["schedule.window.start"], vals["schedule.window.end"] = "08:00", "18:00"
soak = C.config_hash(vals)
assert soak == "d95aa41d", soak
changes = {
    "schedule.timezone": "America/New_York",
    "schedule.window.enabled": True,
    "schedule.window.start": "10:00",
    "schedule.window.end": "15:00",
    "time.allow_system_fallback": False,
    "network.default": "none",
}
for k, v in changes.items():
    print(f"{k}: {vals[k]!r} -> {v!r}")
    vals[k] = v
text = M.render_config_text(vals, header_lines=(
    f"camera_config.yaml - config v2 (schema {R.SCHEMA_VERSION}, registry v{R.REGISTRY_VERSION}).",
    "bmcam002 NOAA on-land target profile (SPEC_e4_noaa_release must do 2), for the 10/27 update.",
    "= the bench NOAA soak profile d95aa41d (bmcam003/004 base + overlay, RC soak 10/22-10/26) with",
    "  America/New_York, window 10:00-15:00, allow_system_fallback false, network.default none.",
    "Installed whole after the v1 -> v2 migration; never edited on the unit (DESIGN_supervisor.md section 5).",
))
open(out, "w").write(text)
chk = C.load_config(out, strict=True)
assert chk.base == vals, "round trip differs"
print("soak", soak, "target base hash", C.config_hash(chk.base))
