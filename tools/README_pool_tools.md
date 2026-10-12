# Pool test host tools (2026-10-13)

Three host-side tools for the Tue 10/13 pool test. They do not touch the cameras' code; the
Pi-side pool sequence + PREVIEW printer is a separate PR (`feature/pool-sequence-20261013`).

| tool | runs on | does |
|---|---|---|
| `tools/pool_preview.py` | nereus000 | tails `/home/pi/spotter_logs/<SPOT>/console_*.log`, rebuilds `PVH/PV/PVE` preview JPEGs into `/home/pi/pool_preview/`, serves `http://<nereus000>:8090/` |
| `tools/pool_cmd.py` | nereus000 | `pool_cmd.py <bmcam003\|bmcam004\|both> <status\|cal\|loop\|mark\|stop\|preview>`: writes `bm pub bmcam/cmd {...} 1 1` to `cmd.txt` with a persisted id (from 90001), waits for `[bmcamNNN] OK/ERR id=N` |
| `tools/pool_pull.py` | Nick's Mac | pulls `/home/pi/pool_runs/<latest>/` from both units in parallel: manifest+logs → loop products (nrjxl + pjpg + sidecars) → calibration → loop natives (JPEG + DNG) → videos → other, rsync `--partial`, sha256 Pi vs Mac, `~/NereusPool/<date>/<unit>/` + `pull_report.json` |

Ports on nereus000: 8095 = rig dashboard (taken), **8090 = pool viewer**.
Mapping (hil/hil.env.example): SPOT-33507C ↔ bmcam003, SPOT-31593C ↔ bmcam004. `pool_cmd.py` refuses any other unit/Spotter.

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

Start the viewer as a transient service (survives ssh logout; stop = `sudo systemctl stop pool-preview`):

```bash
ssh pi@192.168.1.45 'sudo systemd-run --unit pool-preview -p Restart=always -p User=pi /usr/bin/python3 /home/pi/pool_tools/pool_preview.py'
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

`--host bmcam003=192.168.4.23` pins a unit to an IP; `--run bmcam003=<folder>` pins a unit's run folder (default `latest`); `--classes 1,2` pulls only manifest/logs + the loop products (nrjxl + pjpg + sidecars); re-running resumes. A second run of the same unit on the same date needs `--date <name>` (the dest folder is pinned to one run).

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
