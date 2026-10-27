# Release bmcam-rel-20261027 (bmcam002 for NOAA)

Spec: `~/NereusData/ops/pe_specs/SPEC_e4_noaa_release.md` rev 13.8 (sha256 31edce5380c5), must dos M13-1 to M13-3.
The tag is pushed by the Lead after QE PASS on the merged development sha (M13-1). A pushed tag never moves: a fix gets
`bmcam-rel-20261027.1` on its new sha, a rebuilt manifest, and the 5/5 rehearsal count restarts (rev 13.3 N5).

## What is pinned (`release_manifest.json`)
- **sha:** the commit the tag points to. A file can't name its own commit, so the installer checks
  `git rev-parse <tag>^{commit}` = `--sha` (the sha the Lead records at push, named in the runbook; SPEC_e4 rev 13.8
  item 2) and that the staged manifest equals the tag's copy byte for byte.
- **runtime_files:** sha256 of the 60 files `tools/rc_runtime_manifest.txt` installs.
  `tests/test_install_release.py` fails if the tree drifts from them; rebuild with the command below.
- **profile:** `device_profiles/bmcam002/noaa_20261027/camera_config.yaml`, sha256 412bcafe00cd…, config hash
  **fe87abab**, runtime supervisor.
- **target:** Debian trixie, arm64, Python 3.13 only. cjxl ≥ 0.11 from the OS's own suite (staged: 0.11.2-0.1~deb13u3).
  Bookworm is not supported: Debian's bookworm libjxl-tools is 0.7.0 (rev 13.3 N1), so on bookworm the installer stops
  (F13). bmcam002's OS and arch are **unknown** until step 1 reads them on update day.
- **offline .deb set:** 55 debs, each with its sha256; the files are outputs and live in
  `~/NereusData/bm_cam_legacy/2026-10-26_release_debs/trixie_arm64/debs/` (MANIFEST.md there).

## Rebuild the manifest (Mac, repo root)
```
python3 tools/release_manifest.py build --tag bmcam-rel-20261027 \
  --profile device_profiles/bmcam002/noaa_20261027/camera_config.yaml \
  --target ~/NereusData/bm_cam_legacy/2026-10-26_release_debs/trixie_arm64 \
  --out releases/bmcam-rel-20261027/release_manifest.json
python3 tools/release_manifest.py check-repo --manifest releases/bmcam-rel-20261027/release_manifest.json
```

## Install (on the unit, after the catch has disarmed it; the runbook owns the order)
Stage `tools/install_release.sh`, `tools/release_manifest.py`, this `release_manifest.json` and the `debs/` folder in
`/tmp/release/` (scp), check their sha256, then:
```
bash /tmp/release/install_release.sh --tag bmcam-rel-20261027 --sha <the sha the Lead recorded> \
  --manifest /tmp/release/release_manifest.json --debs /tmp/release/debs
```
Want the last line `[INSTALL] PASS tag=bmcam-rel-20261027 sha=<12> cfg=fe87abab runtime=supervisor …`.
Exit 1 = FAIL (a step before 4 leaves the runtime untouched), 3 = STOP and ask Nick. The unit is left disarmed;
re-arm with the crontab file the catch saved (RUNBOOK A1). Log: `/home/pi/release_logs/install_release_<UTC>.log`.
