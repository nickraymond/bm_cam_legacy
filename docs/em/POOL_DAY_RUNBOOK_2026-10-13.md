# Pool day runbook, Tue 10/13 (for Nick, poolside)

Rev 2.2, Mon 10/12 (EM edits: pool-day card, no Wi-Fi password in argv; hotspot clones the home Wi-Fi; QE fixes: §9.3 re-arm command, pull classes). T-LAND numbers come in rev 3.
Times are Pacific, AM/PM. Plan: `docs/em/POOL_PLAN_2026-10-13.md`.

## Pool-day card (one screen; details in §0–§10 below)

Mac Terminal, repo root. Set the shortcuts first (§1.3). After that, `pc` = pool command on both consoles.

1. **10:00 AM** arrive. Phone hotspot ON (name/password = home Wi-Fi, **Maximize Compatibility**). Mac, nereus000 and the units join on their own. `export N=pi@<nereus000 IP>`, define `pc` (§1.3). Viewer: `http://<nereus000 IP>:8090`
2. **10:15** `pc both status` → both `phase=idle cal=0/0 loop=0/0`. Write down `run=` and `ip=`. Battery: §2.4
3. **10:30** `pc both cal` → `OK ... start frames=120`. Dive 20 min; it stops on its own (~10:50)
4. **10:55** card at **1 m**. `pc both preview` → both cards **OK** in the viewer (PARTIAL: run the resend shown on the card)
5. **11:00** `pc both mark card_1m`, then `pc both loop` → `OK ... iterations=18` (auto-starts ~11:20 if not sent)
6. **12:30 PM** after `loop=9/18`: card to **2 m**, then `pc both mark card_2m`
7. **2:00 PM** loop ends (`loop=18/18`). To end early: `pc both stop`
8. **2:05** units out of the water. `pc both status` → new `ip=`; `run=` as noted
9. Pull dry run: `python3 tools/pool_pull.py --host bmcam003=<ip> --host bmcam004=<ip> --run bmcam003=<run> --run bmcam004=<run> --dry-run`
10. **2:15** the same command without `--dry-run` → `[pool_pull] OK` (re-run to resume). Report: `~/NereusPool/<date>/pull_report.json`
11. **~3:30 PM** restore (TE, §9): `pool_mode.sh stop` → `off` → check ARMED → halt → `hil/tools/hil_restore_schedule.sh <SPOT>`
12. Next :00 → a new image from each unit on the dashboard. Trouble at any step: §10

---

Run every command on the **Mac**, in Terminal, from the repo root.
Each step has a **See:** line. If you don't see it, go to the table in §10.

| | |
|---|---|
| Units | bmcam003 (SPOT-33507C) and bmcam004 (SPOT-31593C) |
| Console host | nereus000. Viewer at `:8090`, rig dashboard at `:8095` |
| Code | `development` with #151 (host tools), #152 (Pi pool sequence), #153 (calib check) and #154 (this runbook + `tools/pi_wifi_profile.sh`) merged. TE confirms Mon evening. |
| Pi tools | `/home/pi/pool_mode.sh`, `/home/pi/BM_Devel_Pi/pool_sequence.py` |
| nereus000 tools | `/home/pi/pool_tools/pool_cmd.py` and `pool_preview.py` |
| Session defaults | calibration 20 min, one frame every 10 s (120 frames). Loop 18 iterations, one every 10 min (3 h). Loop auto-starts 30 min after calibration ends. |

> **No hourly production images from Mon evening until the restore on Tue PM.** Both units sit in pool mode the whole time, so the dashboard gap is expected.

> **Battery.** The Spotters run on their own battery from the moment they leave the hub (Tue AM) until the download ends: about 6 h, Pi idle and then busy. T-LAND measures the drain rate (number added here Mon evening). **If the pool deck has power, bring a USB charger or battery pack for the Spotters.** Check the level with step 2.4.

---

## 0. Monday evening (TE, after T-LAND)

1. **Download T-LAND first**, then restart the pool process on both units. This gives Tuesday a fresh, empty run folder; without the restart, Tuesday's frames are added to T-LAND's folder.
   ```bash
   ssh pi@bmcam003 'bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh start'
   ```
   ```bash
   ssh pi@bmcam004 'bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh start'
   ```
   See: `SIGTERM sent ...`, then `[POOL-MODE] started pid ...` on each unit. `stop` returns at once, but the process finishes its current step first (up to ~2 min); the loop waits up to 3 min for it to exit.

2. **Leave the bus held ON overnight** (EM decision A). Both Pis idle in pool mode; the Spotters stay on the nereus000 hub charger.
   - Do NOT restore the hourly schedule. With pool mode on, every hourly window would boot the Pi into pool mode and the bus would cut it 10 min later.
   - See: `pool_cmd.py both status` shows `phase=idle cal=0/0 loop=0/0` for both units.

3. **Hotspot = home Wi-Fi: verify only** (Nick + TE). Nick's phone hotspot uses the SAME SSID and password as the home Wi-Fi the units, nereus000 and the Mac already know. Nothing needs to be added to the units.
   - On the phone: the hotspot name (Settings → General → About → Name) matches the home Wi-Fi SSID byte for byte, including case and punctuation. iOS turns a typed `'` into a curly quote. Hotspot password = home Wi-Fi password. **Maximize Compatibility ON**.
   - On each host, check there is a saved profile for the home SSID with autoconnect on. Pool mode has no boot Wi-Fi apply; NetworkManager autoconnect alone decides what a rebooted unit joins.
   ```bash
   for h in bmcam003 bmcam004 nereus000.local; do echo "== $h"; ssh pi@$h 'nmcli -g NAME,TYPE connection show | grep wireless | cut -d: -f1 | while IFS= read -r c; do echo "$c | ssid=$(nmcli -g 802-11-wireless.ssid connection show "$c") | autoconnect=$(nmcli -g connection.autoconnect connection show "$c")"; done'; done
   ```
   See: on every host, at least one line with `ssid=<home Wi-Fi>` and `autoconnect=yes`. `bmcam-ap ... autoconnect=no` is normal.
   No such line on a host (most likely nereus000): the TE adds one with `tools/pi_wifi_profile.sh home-wifi pi@<host>`. It asks for the SSID and password with a silent prompt, so the password stays out of argv and shell history.
   - Side effect: the hotspot looks like HQ. During a poolside restore (§9), the production boot Wi-Fi apply treats it as HQ and joins it, so there is no open-AP fallback.

4. **Pack:**
   - Mac with the repo on the merged `development`, plus `pip install opencv-contrib-python-headless rawpy` for step 8.5
   - phone
   - USB charger / battery pack for the Spotters
   - power for nereus000 and its hub
   - the ChArUco board and the v3 card

---

## 1. Arrive, about 10:00 AM: network + viewer (about 15 min)

1. **Phone hotspot ON** (same name and password as the home Wi-Fi, step 0.3). Settings → Personal Hotspot → **Maximize Compatibility ON** (2.4 GHz). Stay on that screen, phone awake, near the units.
   See: the phone's connection count rises as the Mac, nereus000, bmcam003 and bmcam004 join on their own. Keep other devices off the hotspot.

2. **Mac joins the hotspot.** Then find nereus000's IP:
   ```bash
   ssh -o ConnectTimeout=8 pi@nereus000.local 'hostname -I'
   ```
   See: an address like `172.20.10.x`. If `.local` fails, scan the hotspot subnet (172.20.10.1–14):
   ```bash
   for i in $(seq 2 14); do ping -c1 -W300 172.20.10.$i >/dev/null & done; wait; arp -a | grep 172.20.10
   ```

3. **Set two shortcuts in this Terminal window.** Redo this after opening a new window.
   ```bash
   export N=pi@172.20.10.X
   ```
   ```bash
   pc() { ssh -o ConnectTimeout=8 "$N" python3 /home/pi/pool_tools/pool_cmd.py "$@"; }
   ```
   See: no output. `pc both status` (step 2.2) now works.

4. **Viewer.** Open `http://172.20.10.X:8090/` in a browser on the Mac or phone.
   See: two cards, **bmcam003** and **bmcam004**. They say "no preview yet", or show last night's preview.
   Page doesn't load (nereus000 rebooted in transit)? Restart the viewer:
   ```bash
   ssh $N 'sudo systemctl reset-failed pool-preview 2>/dev/null; sudo systemd-run --unit pool-preview -p Restart=always -p User=pi /usr/bin/python3 /home/pi/pool_tools/pool_preview.py'
   ```
   See: `Running as unit: pool-preview.service`. "already exists" means it is already running.

5. **Pool Wi-Fi profile** (optional, fallback for the download only). Run it over the hotspot with the IPs from step 2.2, nereus000 included. Priority -10 means a reboot still prefers the hotspot. Adding a profile does NOT switch networks; everything stays on the hotspot.
   ```bash
   tools/pi_wifi_profile.sh --priority -10 pool-wifi pi@<bmcam003 ip> pi@<bmcam004 ip> pi@<nereus000 ip>
   ```
   See: `OK pi@<ip> pool-wifi autoconnect=yes priority=-10 powersave=off uuid=...` three times. Re-running keeps the same uuid, which updates the profile in place.

---

## 2. Verify: bus held, units idle, consoles alive (about 5 min)

1. **Consoles alive.** Check the newest console line per Spotter:
   ```bash
   ssh $N 'for s in SPOT-33507C SPOT-31593C; do echo "$s $(tail -n1 /home/pi/spotter_logs/$s/console_$(date -u +%Y%m%d).log | cut -c1-90)"; done'
   ```
   See: one line per Spotter, timestamp within the last ~15 s (the console prints power lines every 10 s).

2. **Status, both units:**
   ```bash
   pc both status
   ```
   See, for each unit:
   ```
   [bmcam003] OK id=9xxxx pool status phase=idle cal=0/0 loop=0/0 run=<YYYYMMDD_HHMMSS>_bmcam003 free=<MB> marks=0 err=none ip=172.20.10.y ssid=<home Wi-Fi name> up=<s>
   ```
   Write `ip=` and `run=` for both units into §11; step 8 needs them.

3. **Read the status line:**
   - **Bus held:** `up=` (pool process uptime) is bigger than 600 s while the clock is NOT between :00 and :10. A small `up=` means the Spotter or Pi reset in transit. That is expected and gives a new, empty run folder (new `run=`).
   - **Fresh run:** `cal=0/0 loop=0/0`. Any other count: restart that unit's pool process (step 0.1 command, using the unit's IP).
   - **Space:** `free=` is at least 6000 MB. Calibration ≈ 3.8 GB, loop ≈ 0.7 GB, plus a 1 GB margin. The `pool ready ... need~<MB>` console line has the exact figure.

4. **Spotter battery:**
   ```bash
   for s in SPOT-33507C SPOT-31593C; do ssh $N "echo sensors > /home/pi/spotter_logs/$s/cmd.txt; sleep 4; tail -n 80 /home/pi/spotter_logs/$s/console_\$(date -u +%Y%m%d).log | grep -E 'BATT_(V|I)' | tail -2" | sed "s/^/$s /"; done
   ```
   See: `BATT_V` about 4.0–4.2 V (healthy bench reading). Repeat at each step. "Low" is set from T-LAND's measured drain; until then, treat **< 3.7 V (assumed)** as plug-in-now.

---

## 3. Watch the consoles (optional, a second Terminal window)

```bash
ssh pi@172.20.10.X 'tail -F /home/pi/spotter_logs/SPOT-33507C/console_$(date -u +%Y%m%d).log /home/pi/spotter_logs/SPOT-31593C/console_$(date -u +%Y%m%d).log | grep --line-buffered "\[bmcam" | grep --line-buffered -v " PV "'
```

See:
- unit lines such as `pool cal done 120/120`, `pool loop 3/18 71s pjpg=... nrjxl=... mp4=...`, and answers `OK id=` / `ERR id=`;
- preview data lines are hidden.

---

## 4. Calibration, about 10:30 AM (20 min, auto-stop)

1. Cameras mounted in the water; Nick ready with the board and a 20 min timer.
2. Start both units:
   ```bash
   pc both cal
   ```
   See: `[bmcam003] OK id=N pool cal start frames=120 every=10s dur=1200s free=...MB`, and the same for bmcam004. Then `pool_cmd` prints `OK` for both and exits 0.
3. Nick dives and moves the board through 15–20 poses: hands on the edge, tilt it, cover the corners of the frame.
4. About 20 min later, both units stop by themselves.
   See (console): `pool cal done 120/120 skipped=0 stopped=0 free=...MB`. Or run `pc both status` and look for `phase=idle cal=120/120 ... autostart_in=<s>`.

---

## 5. Card at 1 m → preview → loop, about 10:55 AM

The loop **auto-starts 30 min after calibration ends**, about 11:20 AM, with the default 18 × 10 min. Need more time? Send `pc both stop` while idle. That only cancels the auto-start; then send `loop` yourself when ready.

1. Place the v3 card at **1 m** in front of both cameras.
2. Preview both:
   ```bash
   pc both preview
   ```
   See: `OK id=N pool preview img=<HHMMSS> n=<lines> bytes=<B> q=<q> eta=<s>s` per unit. Within about 30 s the viewer shows both images, marked **OK** with lines n/n. The image is the production 1600×900 crop, shown at 640×360.
3. **PARTIAL** on a card? The card shows the exact resend command. Run it, for example:
   ```bash
   pc bmcam003 preview --lines 20-21,35
   ```
   See: the card turns **OK**. Lines lost twice? Send a fresh `pc bmcam003 preview`.
4. Adjust the card and preview again until the framing is right on both units.
5. Mark the position (optional), then start the loop:
   ```bash
   pc both mark card_1m
   ```
   ```bash
   pc both loop
   ```
   See: `OK id=N pool mark #1 card_1m ...`, then `OK id=N pool loop start iterations=18 every=600s free=...MB`. If the loop already auto-started, `loop` answers `ERR ... busy phase=loop`; that is fine, it is running.

---

## 6. At 1.5 h: move the card to 2 m, about 12:30 PM

1. Wait for the console line `pool loop 9/18 ...`, or for `pc both status` to show `loop=9/18`. Each iteration's capture and 30 s clip take about the first 70 s of its 10 min slot. Move the card in the quiet minutes after a `pool loop n/18` line.
2. Move the card to **2 m**, then:
   ```bash
   pc both mark card_2m
   ```
   See: `OK id=N pool mark #2 card_2m phase=loop t=<UTC>`.
3. Optional framing check. During the loop it can wait up to about 35 s for the camera (`waiting for camera` line), hence the longer wait:
   ```bash
   pc both preview --wait 60
   ```

---

## 7. Loop ends, about 2:00 PM (3 h after loop start)

See: the console shows `pool loop done 18/18 ...`, and `pc both status` shows `phase=idle loop=18/18`.

To end early:
```bash
pc both stop
```
See: `OK id=N pool stop phase=loop stopping after the current step`.

---

## 8. Units on land → download, about 2:05 PM (≤ 1 h)

The Pis stay up in pool mode (bus held) until the restore, so the data is safe on the SD card. Wi-Fi only works once the units are out of the water.

1. **Units out of the water, near the phone.** Run `pc both status`.
   See: `ip=172.20.10.y ssid=<home Wi-Fi name>` for both units. Use these IPs below. A `172.20.10.x` address confirms it's the hotspot, not a real home network.

2. **Run folder per unit** = the `run=` in that status line, the same one you wrote down in step 2.2. Cross-check that no second Tuesday folder exists (a reset mid-session):
   ```bash
   for ip in 172.20.10.A 172.20.10.B; do ssh pi@$ip 'hostname; du -sh /home/pi/pool_runs/*/ | tail -4'; done
   ```
   See: the session folder (e.g. `20261013_170512_bmcam003`) at about 4.5 GB. A second Tuesday folder on one unit means pulling both; see §10.

3. **Dry run:**
   ```bash
   python3 tools/pool_pull.py --host bmcam003=172.20.10.A --host bmcam004=172.20.10.B --run bmcam003=<folder3> --run bmcam004=<folder4> --dry-run
   ```
   See, per unit:
   - 6 classes with sizes, pulled in this order. Roughly:
     1. manifest+logs: KB
     2. loop products, i.e. nrjxl + pjpg + sidecars: a few MB
     3. calibration: ~3.8 GB
     4. loop natives, i.e. the still JPEG + DNG: ~0.6 GB
     5. videos: ~135 MB
     6. other: ~0
   - a measured MB/s
   - `projected wall time`

   Projected time > 1 h? Pull classes 1–2 now (`--classes 1,2`: every nrjxl + pjpg, a few MB) and the rest at HQ.

4. **Real pull** (same command without `--dry-run`). It pulls in priority order: manifest/logs → loop products (nrjxl + pjpg) → calibration → loop natives (JPEG + DNG) → videos → other.
   See:
   - progress lines every 5 s, e.g. `[bmcam003] class 3 calibration: 1.2 GB/3.0 GB files 40/120 | 3.10 MB/s`
   - at the end, `SUMMARY COMPLETE: ok=<n>` per unit and `[pool_pull] OK`
   - exit 0
   - files in `~/NereusPool/<run date>/<unit>/`. `<run date>` is the first 8 digits of the run folder name (UTC), e.g. `20261013`.
   - Interrupted? Run the same command again; it resumes.

5. **Read `~/NereusPool/<run date>/pull_report.json`:**
   - `units.<unit>.complete: true` means every file was checked with sha256, Pi against Mac.
   - `counts` should be only `ok`. `missing` or `sha_mismatch` means re-run the pull. `skipped` means a class you left out with `--classes`.
   - Then the in-air-style calibration check, for a quick look:
     ```bash
     python3 tools/pool_calib_check.py ~/NereusPool/<run date>/bmcam003/calibration
     ```
     See: `VERDICT PASS` (≥ 15 distinct poses, RMS < 1 px) and a `contact_sheet.jpg` next to the folder. Underwater frames go through a flat port, so treat it as an engineering check.

---

## 9. Restore to production, by about 3:30 PM (TE)

Poolside or back at HQ. The hil tools reach the units by name (`pi@bmcam003`, via Tailscale), and `hil/hil.env` `HIL_MONITOR` must be nereus000's **current** IP: the hotspot IP poolside, 192.168.1.45 on the bench. If the units don't resolve by name poolside, do this at HQ; they stay safe in pool mode on the held bus until then.
Per unit, in this order: bmcam003 / SPOT-33507C, then bmcam004 / SPOT-31593C.

1. **Stop pool:**
   ```bash
   ssh pi@bmcam003 'bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh status'
   ```
   See: `SIGTERM sent`, then (within ~2 min) a status with no `pool_sequence.py` process listed.

2. **Pool mode off** (crontab + config sha check):
   ```bash
   ssh pi@bmcam003 'bash /home/pi/pool_mode.sh off 2>&1 | tee ~/pool_mode_off.txt; exit ${PIPESTATUS[0]}'
   ```
   See:
   - `crontab restored and verified byte-identical`
   - `config/state files bit-identical to pool-mode entry`
   - `PRODUCTION MODE`
   - exit 0

   Other exit codes (#152 @ a23f4d6):
   - **Exit 4**, `[ERROR] restored crontab is DISARMED ... re-arm with: crontab /home/pi/hil_backup/<TS>/crontab_ARMED.txt`. The bus hold ran before pool mode went on; re-arm in step 3, which uses exactly that path (saved in `~/pool_mode_off.txt` on the Pi).
   - **Exit 5**, `[ERROR] a config/state file changed`: stop and tell the EM.

3. **Check the crontab is ARMED** (always; required after exit 4):
   ```bash
   ssh pi@bmcam003 "crontab -l | grep -E '^@reboot.*rc_run_capture_cycle'"
   ```
   See: exactly one uncommented `@reboot ... rc_run_capture_cycle.sh` line.
   Nothing printed (`off` exited 4): re-arm from the path `off` printed on its `re-arm with:` line. `pool_mode.sh` lists only backups that hold an armed line.
   - The command takes that path from `~/pool_mode_off.txt`.
   - It installs the file only if it has the uncommented RC line and no `POOL_MODE_LINE`.
   - It first saves the current crontab to `~/crontab_before_rearm_<UTC>.txt` (restore: `crontab <that file>`).
   - After installing, it checks again. If any check fails, nothing is installed.
   ```bash
   ssh pi@bmcam003 'set -e; B=$(sed -n "s/.*re-arm with: crontab //p" ~/pool_mode_off.txt | head -1); [ -n "$B" ] || { echo "NO re-arm path in ~/pool_mode_off.txt"; exit 1; }; echo "using $B"; grep -E "^@reboot.*rc_run_capture_cycle" "$B"; if grep -q POOL_MODE_LINE "$B"; then echo "REFUSED: $B has the pool line"; exit 1; fi; crontab -l > ~/crontab_before_rearm_$(date -u +%Y%m%dT%H%M%SZ).txt; crontab "$B"; echo installed; crontab -l | grep -E "^@reboot.*rc_run_capture_cycle"'
   ```
   See: `using /home/pi/hil_backup/<TS>/crontab_ARMED.txt` (the same path `off` printed), the RC line, `installed`, then the RC line again from the live crontab. `NO re-arm path`, `REFUSED` or no RC line: stop and tell the EM; `off` found no armed backup.

4. **Halt the Pi** (the bus stays held, so it just goes dark):
   ```bash
   ssh pi@bmcam003 'nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null &'
   ```
   See: about 20 s later, `ssh -o ConnectTimeout=5 pi@bmcam003 true` fails.

5. **Production bus schedule** (1 h / 10 min):
   ```bash
   hil/tools/hil_restore_schedule.sh SPOT-33507C
   ```
   See: `[restore] cron ARMED`, `bmcam003 dark`, `PRODUCTION schedule confirmed`, exit 0.

6. Repeat steps 1–5 for **bmcam004 / SPOT-31593C**.

7. **One production wake:** at the next :00, open https://nereus-vision-staging.onrender.com/devices/.
   See: a new JPEG XL from each unit around :05–:10, with the RC2 cfg hash. Hourly images resume.

---

## 10. If X goes wrong

| Symptom | Likely cause | Do this |
|---|---|---|
| `pool_cmd` exit 4: `NO ANSWER within 20s` | Pi rebooting (Spotter reset), the command is still queued, or the BM link is down | Run step 2.1: is the console fresh? Wait 60 s, then send `pc <unit> status` again (new id). `up=` small means it rebooted: new run folder, carry on. Still silent after 3 tries: the unit may be unpowered. Check its Spotter battery (step 2.4) and the BM cable. |
| `pool_cmd` exit 3: `NOT-CONSUMED` | spotter-monitor down on nereus000, or a Spotter USB cable/hub is out | `ssh $N 'sudo systemctl restart spotter-monitor'`, check the hub's power, then repeat the command. |
| `ERR id=N pool ...` | The unit refused: busy, out of space, or bad lines | Read the reason after the verb. `busy phase=...`: wait or `stop`. `space ...`: tell the EM, don't delete anything. |
| Preview **PARTIAL** | Console lines dropped | Run the resend command shown on the card (`pc <unit> preview --lines ...`). Twice PARTIAL: send a fresh `pc <unit> preview`. |
| Preview never appears | Camera busy (a loop clip), or the viewer is down | Wait 35 s. Then check that `http://<nereus000-ip>:8090/previews.json` loads; if not, restart the viewer (step 1.4). |
| Unit not on the hotspot (`ssid=none` in status) | 5 GHz hotspot, phone asleep, hotspot name/password not exactly the home Wi-Fi's, or the unit is still in the water | Maximize Compatibility ON; stay on the hotspot screen; check the phone name against the home SSID (curly `'`?); unit out of the water and close to the phone. Still nothing: **leave it**. The data is safe on the SD card and the Pi stays up; pull at HQ on the bench Wi-Fi. |
| Spotter battery low (< 3.7 V, assumed) | about 6 h on battery | Plug the Spotter into deck power or the battery pack. If it can't be charged and the loop is near the end, `pc both stop` gives a clean finish rather than a power cut mid-file. |
| Pull slow (dry run > 1 h) | Hotspot or Wi-Fi throughput | `--classes 1,2` (the nrjxl + pjpg, a few MB) now and the rest at HQ (re-run without `--classes`; it resumes). Or move everyone to `pool-wifi` (step 1.5): `ssh pi@<ip> 'nohup sudo nmcli connection up pool-wifi &'` per unit, then the Mac. The new IP shows in `pc both status`; nereus000 must move too. Pool networks may block device-to-device traffic. |
| `pool_pull` REFUSED: dest holds a different run | A second run folder for the same unit and date | Pull it with its own `--date`, e.g. `--date 20261013_run2 --run bmcam003=<folder>`. |
| `cal=` / `loop=` not 0 at step 2.3, or `ERR id=N pool cal refused: run already has calibration frames, restart pool mode ...` (same for `loop`; an auto-start that hits it prints `pool loop AUTOSTART FAILED refused: ...`) | This run folder already holds a session (pool process not restarted after T-LAND, or a second `cal`) | **Before calibration:** step 0.1 command using the unit's IP (`pool_mode.sh stop`, then `start`), then `pc both status` shows a new `run=` and `cal=0/0`. **Mid-session:** `cal` or `loop` was sent twice. The first one is running or done; nothing to do, don't restart. |

---

## 11. Session log (fill in)

| Time (PT) | Event | id / note |
|---|---|---|
| | hotspot up, viewer OK | |
| | status both OK, run folders: | |
| | cal start | |
| | cal done 120/120 both | |
| | preview approved, card 1 m | |
| | loop start (manual / AUTO) | |
| | card 2 m + mark | |
| | loop done 18/18 | |
| | pull done, report complete | |
| | restore done, first production wake | |
