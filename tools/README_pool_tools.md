# Pool test host tools (2026-10-13)

Three host-side tools for the Tue 10/13 pool test. They do not touch the cameras' code; the
Pi-side pool sequence + PREVIEW printer is a separate PR (`feature/pool-sequence-20261013`).

| tool | runs on | does |
|---|---|---|
| `tools/pool_preview.py` | nereus000 | tails `/home/pi/spotter_logs/<SPOT>/console_*.log`, rebuilds `PVH/PV/PVE` preview JPEGs into `/home/pi/pool_preview/`, serves `http://<nereus000>:8090/` |
| `tools/pool_cmd.py` | nereus000 | `pool_cmd.py <bmcam003\|bmcam004\|both> <status\|cal\|loop\|mark\|stop\|preview>`: writes `bm pub bmcam/cmd {...} 1 1` to `cmd.txt` with a persisted id (from 90001), waits for `[bmcamNNN] OK/ERR id=N` |
| `tools/pool_pull.py` | Nick's Mac | pulls `/home/pi/pool_runs/<latest>/` from both units in parallel: manifest+logs → loop products (nrjxl + pjpg + sidecars) → calibration JPEGs → videos → loop native JPEGs → other. **DNGs stay on the units** unless `--include-dng` (Friday pickup); `pull_report.json` lists every DNG left behind with its Pi sha256. rsync `--partial`, sha256 Pi vs Mac, `~/NereusPool/<date>/<unit>/` + `pull_report.json` |

Ports on nereus000: 8095 = rig dashboard (taken), **8090 = pool viewer**.
Mapping since **Tue 10/13** (Nick swapped the cameras, intentional): **bmcam003 ↔ SPOT-31593C, bmcam004 ↔ SPOT-33507C**. It lives in `tools/rig_map.json`, deployed next to `pool_cmd.py` on nereus000; `--map` overrides it, and the map in use is printed at the top of every run. `pool_cmd.py` refuses any other unit/Spotter.

## Wire format (agreed with the Pi-side session 2026-10-12)

```
[bmcam003] PVH bmcam003 <img_id> n=<lines> bytes=<jpeg_bytes> sha256=<hex> w=<w> h=<h> q=<q>
[bmcam003] PV  bmcam003 <img_id> <seq>/<total> <crc32 hex8 of the base64> <base64>
[bmcam003] PVE bmcam003 <img_id>
{"id":N,"c":"pool","v":"status|cal|loop|mark|stop|preview", "note":"card_2m", "lines":"3-7,12",
 "duration_s":..,"interval_s":.. (cal), "iterations":..,"period_s":.. (loop)}     <= 248 B
```

## TE commands

Deploy to nereus000 (from the Mac repo root; use nereus000's IP on the pool Wi-Fi):

```bash
ssh pi@192.168.1.45 'mkdir -p /home/pi/pool_tools /home/pi/pool_preview'
```
```bash
scp tools/pool_preview.py tools/pool_cmd.py pi@192.168.1.45:/home/pi/pool_tools/
```

The viewer is an **enabled systemd service** on nereus000 since Tue 10/13 (TE2, Nick OK). Unit file: [`tools/systemd/pool-preview.service`](systemd/pool-preview.service), byte-identical to nereus000 `/etc/systemd/system/pool-preview.service` (sha256 `a6a92ef6ffb290269f764c48657d57dfbe046377f7322811f23e602d81924177`). It runs as User pi with Restart=always, starts after spotter-monitor, and starts on boot.

Install, or re-install (from the Mac repo root):

```bash
scp tools/systemd/pool-preview.service pi@192.168.1.45:/tmp/pool-preview.service && ssh pi@192.168.1.45 'sudo install -m 644 /tmp/pool-preview.service /etc/systemd/system/pool-preview.service && sudo systemctl daemon-reload && sudo systemctl enable --now pool-preview && systemctl is-active pool-preview'
```

Check that the box matches the repo:

```bash
ssh pi@192.168.1.45 'sha256sum /etc/systemd/system/pool-preview.service; systemctl is-enabled pool-preview'
```

After a redeploy of `pool_preview.py`, restart it:

```bash
ssh pi@192.168.1.45 'sudo systemctl restart pool-preview && sleep 2 && systemctl is-active pool-preview'
```

On start it shows the previews already in `/home/pi/pool_preview/`, then reads the previous and the current UTC-day console log, and it follows the 00:00 UTC (5 PM PDT) rollover. Undo the service:

```bash
ssh pi@192.168.1.45 'sudo systemctl disable --now pool-preview && sudo rm /etc/systemd/system/pool-preview.service && sudo systemctl daemon-reload'
```

Check it: `ssh pi@192.168.1.45 'journalctl -u pool-preview -n 20 --no-pager'`; Nick opens `http://192.168.1.45:8090/`.

Send a preview / any verb (ids persist in `/home/pi/spotter_logs/pool_cmd_id`; log in `pool_cmd.log`):

```bash
ssh pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both preview'
```
```bash
ssh pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py bmcam003 preview --lines 20-21,35'
```
```bash
ssh pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both mark "card 2m"'
```

The viewer prints the exact `--lines` resend command for every PARTIAL preview (also on the page). A `lines` value is at most **186 chars**, the one number both tools enforce (`max_lines_chars()`). It is set by the 256 B console line; the 248 B JSON and the Pi's 200-char `RE_LINES` are looser. More gaps produce several resend commands.
Exit codes: 0 OK, 1 ERR, 3 cmd.txt not consumed (spotter-monitor down), 4 no answer in `--wait` (20 s).

Bus hold (Mac repo checkout only; it ssh's to the monitor AND the camera; unit must be HALTED; Nick's OK):

```bash
python3 tools/pool_cmd.py bmcam003 bus-hold on --dry-run
```

Pull (Mac; key ssh to pi@bmcam00x needed):

```bash
python3 tools/pool_pull.py --dry-run
```
```bash
python3 tools/pool_pull.py
```

`--host bmcam003=192.168.4.23` pins a unit to an IP; `--run bmcam003=<folder>` pins a unit's run folder (default `latest`); `--classes 1,2` pulls only manifest/logs + the loop products (nrjxl + pjpg + sidecars); re-running resumes. `--include-dng` adds every DNG as the last class. Speed knobs (defaults unchanged until `tools/pool_link_ab.py` data says otherwise): `--cipher`, `--streams N`, `--whole-file`. A second run of the same unit on the same date needs `--date <name>` (the dest folder is pinned to one run).

## Tests

```bash
.venv-dev/bin/python -m pytest -q tests/test_pool_preview.py tests/test_pool_cmd.py tests/test_pool_pull.py
```

`test_pool_pull.py` runs the real local rsync against a fake `ssh` (no network). `test_pool_preview.py` needs Pillow (requirements-dev).

## Known limits

- Not run against hardware here: the console path, the Pi's preview lines, and Pi-side rsync are untested until the TE runs them.
- `pool_preview.py` backfills only the last 4 MB of each console log on start; use `--file <log> --once` for older previews.
- A preview without PVE is finalised after 20 s of silence; a re-used img_id with a new sha256 restarts that image.
- `pool_pull.py` assumes no spaces in filenames and that the sequence has stopped (a growing file fails sha256; re-run).
- The `--dry-run` rate is a 16 MB `/dev/zero` probe over ssh (both units at once); real JPEG/DNG rsync is usually a little slower.

## Focus sweep (2026-10-13)

- `pool_cmd.py <unit|both> sweep [--lp 0,0.2,...,1.82 | --lo 0 --hi 1.8 --step 0.2]` sends the lens list. It allows ≤ 12 positions, each 0–15, and validates them like the Pi. The default answer wait is 180 s.
- `pool_pull.py --sweep [--sweep-name HHMMSS]` pulls only `<run>/sweep/**`: JPEG, `sweep_manifest.json` and logs. DNGs are pulled only with `--include-dng`. The report goes to `pull_report_sweep.json`.
- `focus_sweep_sheet.py --unit NAME=PATH (every sweep, as repeats at --board-distance-m) | --sweep-dir NAME=[TAG:]PATH (explicit, repeatable; TAG = distance or label = the repeat group) --medium in-air|underwater`. Each sweep is scored on its own board region. The median and spread are computed only within a repeat group, and different distances sit side by side. It writes a per-unit cut sheet (a scaled full frame and a 1:1 board crop per lens position), a metric-vs-LensPosition plot with both units overlaid, a CSV and a JSON. Board metrics: `lapvar_norm`, `tenengrad_norm` and ChArUco corners. PASS/WARN/FAIL per frame and per unit.


## Underwater calibration pipeline (2026-10-14)

`pool_calib_pipeline.py` replaces the in-air `pool_calib_check.py` for real calibration. It reuses that
tool's board and layout code. One command covers one pool session and writes one timestamped run folder:

```bash
.venv/bin/python tools/pool_calib_pipeline.py bmcam003=<run>/calibration bmcam004=<run>/calibration --out-root ~/NereusPool/<date>_CALIB --tag <tag>
```

What it does, per camera:
1. Screens every frame. It gives PASS/WARN/FAIL with reasons: corners, blur, clipping, motion, and
   near-duplicate poses.
2. Measures coverage: an 8×5 grid with the 4 corner cells, plus the gap from each edge to the nearest
   corner.
3. Fits the standard, rational and fisheye models. It picks one by **held-out** error (5-fold by view),
   then runs a bootstrap.
4. Writes intrinsics for the full 4608×2592 frame and for the production 1600×900 crop.
5. Writes before/after images: a calibration frame corrected by the model that never saw it, and the v3
   card frame (auto-found in `../loop/`).
6. Writes a one-page `verdict.png`.

Bars come from `docs/pool_calib_prereg_20261014.md`. Exit codes:

| code | meaning |
|---|---|
| 0 | all full-frame bars pass |
| 3 | the run completed but a bar failed |
| 1 | the pipeline failed |
| 2 | arguments were refused |

`--screen-override KEY=VALUE` is for sensitivity runs only. It labels the run NON-PREREG everywhere.

**OpenMV raw input (nereus002 N6 / AE3).** Pass a glob of `*.bayer` files. Each needs a `<stem>.json`
next to it with `width`, `height`, `cfa`, `bits` (8 only), `black_level` and `white_level`, as written
by the nereus002 rig. Raw bypasses the board ISP, so prefer it over the board JPEGs:

```bash
.venv/bin/python tools/pool_calib_pipeline.py 'n6=<run>/exp_*_pool/captures/openmv_n6/openmv_n6_raw_*.bayer' 'ae3=<run>/exp_*_pool/captures/openmv_ae3/openmv_ae3_raw_*.bayer' --out-root ~/NereusPool/<date>_CALIB --tag <tag>
```

Results for 10/14 are in `runs/pool_calib_20261014/RESULTS.md`. Tests:
`.venv/bin/python -m pytest -q tests/test_pool_calib_pipeline.py`. They run on synthetic renders with a
known K and distortion, take ~13 s, and need the main `.venv` (opencv-contrib, scipy, matplotlib).
