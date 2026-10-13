# Pool day runbook, Tue 10/13 (for Nick, poolside)

Rev 4, Mon 10/12 PM: **T-ENDURE numbers filled in** (TE2: both units, full session on the bench; write-up `runs/pool_rehearsal_20261012/tendure/TENDURE_RESULTS.md` on `feature/r1-hil-test-engineer`). **DNGs stay on the units until Fri** (Nick 8:20 PM). The pool pull **measured 12 m 02 s for both units** at pool_pull 66a9f2e. EM decision **(E)**: units in production Mon night, into pool mode Tue 8:00–8:30 AM at the bench. §9.3 re-arms from the path `off` prints.
Times are Pacific, AM/PM. Plan: `docs/em/POOL_PLAN_2026-10-13.md`.

## Pool-day card (one screen; details in §0–§10 below)

Mac Terminal, repo root. Set the shortcuts first (§1.3). After that, `pc` = pool command on both consoles.

0. **8:00–8:30 AM, bench (TE2, §0B)**: after the 8 AM wake halts, bus hold → `tools/pool_sha_check.sh` (ALL MATCH, or STOP) → `pool_mode.sh args --lens-position 0.8` + `on` + `start` on both units. `status` must show `cal=0/0 loop=0/0 lp=0.8`. Then transport with the bus held.
1. **10:00 AM** arrive. Phone hotspot ON (name/password = home Wi-Fi, **Maximize Compatibility**). Mac, nereus000 and the units join on their own. Mac on power, lid open, `caffeinate -dims` in a spare tab (§1.2). `export N=pi@<nereus000 IP>`, define `pc` (§1.3). Viewer: `http://<nereus000 IP>:8090`
2. **10:15** `pc both status` → both `phase=idle cal=0/0 loop=0/0 lp=0.8`. **lp must be 0.8**, otherwise the pool runs at the config focus 1.82 (§10). Write down `run=` and `ip=`. Battery: §2.4
3. **10:30** `pc both cal` → `OK ... start frames=120`. Dive 20 min; it stops on its own (~10:50)
4. **10:55** card at **1 m**. `pc both sweep` (focus check: `OK id=N pool sweep ... n=...` when done; `pool_cmd` waits up to 180 s), then `pc both preview` → both cards **OK** in the viewer (PARTIAL: run the resend shown on the card)
5. **11:00** `pc both mark card_1m`, then `pc both loop` → `OK ... iterations=18` (auto-starts ~11:20 if not sent)
6. **12:30 PM** after `loop=9/18`: card to **2 m**, then `pc both mark card_2m` (no sweep now: it is refused during the loop)
7. **2:00 PM** loop ends (`loop=18/18`; to end early: `pc both stop`). Card still at 2 m: `pc both sweep` (the 2 m focus check)
8. **2:05** units out of the water. `pc both status` → new `ip=`; `run=` as noted
9. Pull dry run: `python3 tools/pool_pull.py --host bmcam003=<ip> --host bmcam004=<ip> --run bmcam003=<run> --run bmcam004=<run> --dry-run`. **DNGs stay on the units until Fri** (Nick 10/12); the pull is ~0.5 GB per unit, **~12 min measured** (incl. ~2.5 min of DNG hashing on the Pi).
10. **2:15** the same command without `--dry-run` → `LEFT ON UNIT: <n> DNGs` + `[pool_pull] OK` (re-run to resume). Report: `~/NereusPool/<date>/pull_report.json`. Friday pickup: same command + `--include-dng`.
11. **~3:30 PM** restore (TE, §9): `pool_mode.sh stop` → `off` → check ARMED → halt → `hil/tools/hil_restore_schedule.sh <SPOT>`
12. Next :00 → a new image from each unit on the dashboard. Trouble at any step: §10

---

Run every command on the **Mac**, in Terminal, from the repo root.
Each step has a **See:** line. If you don't see it, go to the table in §10.

| | |
|---|---|
| Units | bmcam003 (SPOT-33507C) and bmcam004 (SPOT-31593C) |
| Console host | nereus000. Viewer at `:8090`, rig dashboard at `:8095` |
| Code | `development` with #151 (host tools), #152 (Pi pool sequence), #153 (calib check), #154 (this runbook + `tools/pi_wifi_profile.sh`), #155 (rev 3 + `tools/pool_sha_check.sh`), #156/#157 (viewer), **#158 (DNGs stay on the units, `--include-dng`)**, **#160 (Pi: `sweep`, `--lens-position`, `pool_mode.sh args`)** and **#161 (`pool_cmd` sweep)** merged. TE confirms Mon evening. |
| Pi tools | `/home/pi/pool_mode.sh`, `/home/pi/BM_Devel_Pi/pool_sequence.py` |
| nereus000 tools | `/home/pi/pool_tools/pool_cmd.py` and `pool_preview.py` |
| Session defaults | calibration 20 min, one frame every 10 s (120 frames). Loop 18 iterations, one every 10 min (3 h). Loop auto-starts 30 min after calibration ends. |

> **Hourly images:** normal Mon night (production). They stop Tue ~8:30 AM, when the bus hold starts, and resume after the restore (~3:30 PM).

> **Battery.** The hold runs from Tue ~8:30 AM to the restore at ~3:30 PM, about 7 h. Measured so far (TE2, Mon), all **on the nereus000 hub charger**:
> - 1:12 PM spot reading: net **−0.7 W** per Spotter. This is what decision (E) rests on.
> - T-ENDURE, 1:15–3:56 PM: BATT_V **4.03–4.04 V → 4.07 V**, net **+0.41 W** on both, i.e. charging. There were no readings after 3:56 PM (nereus000 went offline).
> - **Off the charger** (transport + pool deck): **not measured**.
> - **If the pool deck has power, bring a USB charger or battery pack for the Spotters.** It slows the drain; it won't stop it.
> - Check BATT_V with step 2.4 at every phase.

---

## 0A. Monday evening (TE, after T-LAND): back to production

1. **Download T-LAND first** (§8 commands, T-LAND's `run=` names).
2. **Deployed files = `development`** (the units are still up on the held bus):
   ```bash
   tools/pool_sha_check.sh
   ```
   See: 8 `MATCH` lines and `ALL MATCH origin/development <sha>`. Any `DIFF` or `MISSING` → **STOP**: redeploy that file from `development`, then re-run. A stale `pool_mode.sh` (e.g. a T-DRY copy) prints `PRODUCTION MODE` even when the restored crontab is DISARMED; §9.3's ARMED check is the backstop.
3. **Restore both units to production** with §9 steps 1–6. If T-LAND's bus hold ran before `pool_mode.sh on`, `off` exits 4 (DISARMED) and §9.3 re-arms; exit 0 needs no re-arm. §9.3's check runs either way.
   See: `PRODUCTION schedule confirmed` for both Spotters. At the next :00, a new image from each unit (§9.7).
4. Leave the units on the normal hourly schedule overnight, Spotters on the nereus000 hub charger; they net-charge. Do NOT hold the bus overnight (decision E; the 1:12 PM reading was −0.7 W net on the charger).

5. **Hotspot = home Wi-Fi: verify only** (Nick + TE). Nick's phone hotspot uses the SAME SSID and password as the home Wi-Fi the units, nereus000 and the Mac already know. Nothing needs to be added to the units.
   - On the phone: the hotspot name (Settings → General → About → Name) matches the home Wi-Fi SSID byte for byte, including case and punctuation. iOS turns a typed `'` into a curly quote. Hotspot password = home Wi-Fi password. **Maximize Compatibility ON**.
   - On each host, check there is a saved profile for the home SSID with autoconnect on. Pool mode has no boot Wi-Fi apply; NetworkManager autoconnect alone decides what a rebooted unit joins.
   ```bash
   for h in bmcam003 bmcam004 nereus000.local; do echo "== $h"; ssh pi@$h 'nmcli -g NAME,TYPE connection show | grep wireless | cut -d: -f1 | while IFS= read -r c; do echo "$c | ssid=$(nmcli -g 802-11-wireless.ssid connection show "$c") | autoconnect=$(nmcli -g connection.autoconnect connection show "$c")"; done'; done
   ```
   See: on every host, at least one line with `ssid=<home Wi-Fi>` and `autoconnect=yes`. `bmcam-ap ... autoconnect=no` is normal.
   No such line on a host (most likely nereus000): the TE adds one with `tools/pi_wifi_profile.sh home-wifi pi@<host>`. It asks for the SSID and password with a silent prompt, so the password stays out of argv and shell history.
   - Side effect: the hotspot looks like HQ. During a poolside restore (§9), the production boot Wi-Fi apply treats it as HQ and joins it, so there is no open-AP fallback.

6. **Pack:**
   - Mac with the repo on the merged `development`, plus `pip install opencv-contrib-python-headless rawpy` for step 8.5, and **its charger** (it must stay awake all session)
   - phone
   - USB charger / battery pack for the Spotters
   - power for nereus000 and its hub
   - the ChArUco board and the v3 card

## 0B. Tuesday 8:00–8:30 AM, bench (TE2): into pool mode

Run on the Mac, repo root, with `hil/hil.env` on the bench (`HIL_MONITOR=pi@192.168.1.45`). Steps 1–2 one unit at a time: bmcam003 / SPOT-33507C, then bmcam004 / SPOT-31593C.

1. **Wait for the 8 AM wake to finish.** The unit halts itself, usually by ~8:10 AM.
   ```bash
   ssh -o ConnectTimeout=5 pi@bmcam003 true; echo rc=$?
   ```
   See: `rc=255` (halted). The dashboard shows its 8 AM image.

2. **Bus hold** (Nick's OK for bridge config is part of this plan). This catches the boot, backs up the ARMED crontab to `~/hil_backup/<TS>/`, disarms the RC line and stops the cycle.
   ```bash
   hil/tools/hil_bus_always_on.sh SPOT-33507C
   ```
   See: `bus held ON, bmcam003 up + disarmed (backup ~/hil_backup/<TS>)`, exit 0. Exit 1 `Pi not reachable in 120 s`: the unit runs its cycle and halts; restore the schedule (`hil/tools/hil_restore_schedule.sh SPOT-33507C`) and retry at the next wake.

3. Repeat 1–2 for **bmcam004 / SPOT-31593C**. Both Pis are now up on a held bus.

4. **Deployed files = `development`, STOP if not** (QE: an old T-DRY copy would fake `PRODUCTION MODE` at the restore):
   ```bash
   tools/pool_sha_check.sh
   ```
   See: 8 `MATCH` lines (3 per unit + 2 on nereus000), `ALL MATCH origin/development <sha>`, exit 0.
   Any `DIFF` / `MISSING` / `UNREACHABLE` → **STOP**. Redeploy that file from `development`, restart the viewer if it was `pool_preview.py` (`sudo systemctl restart pool-preview`), then re-run until ALL MATCH. Pool mode is not on yet, so nothing runs stale code.

5. **Pool lens 0.8 + pool mode on + start**, per unit. `args` writes `~/pool_args`, which `pool_sequence` reads at every start, including a reboot. Without it the pool runs at the config focus, 1.82 (#160).
   ```bash
   ssh pi@bmcam003 'bash /home/pi/pool_mode.sh args --lens-position 0.8 && bash /home/pi/pool_mode.sh on && bash /home/pi/pool_mode.sh start'
   ```
   ```bash
   ssh pi@bmcam004 'bash /home/pi/pool_mode.sh args --lens-position 0.8 && bash /home/pi/pool_mode.sh on && bash /home/pi/pool_mode.sh start'
   ```
   See:
   - `wrote /home/pi/pool_args: --lens-position 0.8`
   - `crontab backed up`
   - three `[WARN]` lines saying the backed-up crontab has no armed line (expected under (E); §9.3 re-arms at the end)
   - `effective config hash`
   - `started pid ...`

   The console then shows `[bmcamNNN] pool ready run=... lp=0.8 ...`.

6. **Status, both units** (on nereus000):
   ```bash
   ssh pi@192.168.1.45 'python3 /home/pi/pool_tools/pool_cmd.py both status'
   ```
   See: both `phase=idle cal=0/0 loop=0/0 lp=0.8 run=<new folder>`. Then unplug the Spotters from the hub and transport (bus held, about 1–2 h; the off-charger drain is not measured, so read BATT_V at §2.4 on arrival).
   Need a fresh run folder later (§10)? This restarts the pool process (§0B.6 restart):
   ```bash
   ssh pi@<ip> 'bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh start'
   ```

---

## 1. Arrive, about 10:00 AM: network + viewer (about 15 min)

1. **Phone hotspot ON** (same name and password as the home Wi-Fi, §0A.4). Settings → Personal Hotspot → **Maximize Compatibility ON** (2.4 GHz). Stay on that screen, phone awake, near the units.
   See: the phone's connection count rises as the Mac, nereus000, bmcam003 and bmcam004 join on their own. Keep other devices off the hotspot.

2. **Mac joins the hotspot.** Then find nereus000's IP:
   ```bash
   ssh -o ConnectTimeout=8 pi@nereus000.local 'hostname -I'
   ```
   See: an address like `172.20.10.x`. If `.local` fails, scan the hotspot subnet (172.20.10.1–14):
   ```bash
   for i in $(seq 2 14); do ping -c1 -W300 172.20.10.$i >/dev/null & done; wait; arp -a | grep 172.20.10
   ```

   **Keep the Mac awake for the whole session**: lid open, on power. (T-ENDURE: the Mac slept with the lid closed.) Run this in a spare Terminal tab and leave it until the restore; Ctrl-C ends it:
   ```bash
   caffeinate -dims
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
   [bmcam003] OK id=9xxxx pool status phase=idle cal=0/0 loop=0/0 lp=0.8 run=<YYYYMMDD_HHMMSS>_bmcam003 free=<MB> marks=0 err=none ip=172.20.10.y ssid=<home Wi-Fi name> up=<s>
   ```
   Write `ip=` and `run=` for both units into §11; step 8 needs them.

3. **Read the status line:**
   - **Bus held:** `up=` (pool process uptime) is bigger than 600 s while the clock is NOT between :00 and :10. A small `up=` means the Spotter or Pi reset in transit. That is expected and gives a new, empty run folder (new `run=`).
   - **Lens:** `lp=0.8` on both. Anything else (e.g. `lp=1.82`): fix it before calibration (§10 row "lp is not 0.8").
   - **Fresh run:** `cal=0/0 loop=0/0`. Any other count: restart that unit's pool process (the restart command at the end of §0B.6, using the unit's IP).
   - **Space:** `free=` is at least 6000 MB. Calibration ≈ 3.8 GB, loop ≈ 0.7 GB, plus a 1 GB margin. The `pool ready ... need~<MB>` console line has the exact figure.

4. **Spotter battery:**
   ```bash
   for s in SPOT-33507C SPOT-31593C; do ssh $N "echo sensors > /home/pi/spotter_logs/$s/cmd.txt; sleep 4; tail -n 80 /home/pi/spotter_logs/$s/console_\$(date -u +%Y%m%d).log | grep -E 'BATT_(V|I)' | tail -2" | sed "s/^/$s /"; done
   ```
   See: `BATT_V` about 4.0–4.2 V (T-ENDURE on the charger: 4.03–4.07 V). Repeat at each step. No discharge has been measured yet, so treat **< 3.7 V (assumed)** as plug-in-now and note every reading in §11.

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
   See: `[bmcam003] OK id=N pool cal start frames=120 every=10s dur=1200s free=...MB`, and the same for bmcam004. (T-ENDURE: 120/120 frames, each ≤ 6.15 s.) Then `pool_cmd` prints `OK` for both and exits 0.
3. Nick dives and moves the board through 15–20 poses: hands on the edge.
   - **Dive tip** (rig test, Mon): at ~1 m the board covered only ~4 % of the frame. Underwater, hold it **~0.5–0.8 m** from the port.
   - **Tilt it 30–45°**, in different directions.
   - Cover the **centre** (the production 1600×900 crop), then **each corner** of the frame.
   - Hold each pose still for one 10 s frame.
4. About 20 min later, both units stop by themselves.
   See (console): `pool cal done 120/120 skipped=0 stopped=0 free=...MB`. Or run `pc both status` and look for `phase=idle cal=120/120 ... autostart_in=<s>`.

---

## 5. Card at 1 m → sweep → preview → loop, about 10:55 AM

The loop **auto-starts 30 min after calibration ends**, about 11:20 AM, with the default 18 × 10 min. (In T-ENDURE the loop came from this auto-start, and it worked.) Need more time? Send `pc both stop` while idle. That only cancels the auto-start; then send `loop` yourself when ready.

1. Place the v3 card at **1 m** in front of both cameras.
2. **Focus sweep** (Nick's focus-spec check, #160): 5 full-res stills, JPEG + DNG, at lens 0.6, 0.7, 0.8, 0.9 and 1.0, saved in the run's `sweep/<HHMMSS>/` folder. Afterwards the unit goes back to its own lens setting. The OK arrives only when it's done, so `pool_cmd` waits up to 90 s.
   ```bash
   pc both sweep
   ```
   See, per unit: `OK id=N pool sweep ... dir=sweep/<HHMMSS> ...` once all frames are done. `pool_cmd` waits up to 180 s. The format depends on the Pi build on the units:
   - **#160 build:** `n=5/5 req=0.6,...,1 got=<lens values> t=<s>s back_to_lp=<lens>`, after ~20–40 s.
   - **Extended sweep build** (Pi focus-sweep PR): `n=11 dt=<s>s dir=sweep/<HHMMSS> best_lp=<x> best_lp_ten=<x> back_to_lp=0.8`, after ~60–70 s.
     - Another lens list: `pc both sweep --lp 0,0.2,...,1.82` or `--lo 0 --hi 1.8 --step 0.2`, ≤ 12 positions, each 0–15.
     - `pool_cmd` refuses a bad list before sending it.
   - `n` below 5/5 or an `x` in `got=`: a frame failed; run `sweep` again.
   - `ERR ... busy phase=...`: it only runs while idle (not during `cal` or `loop`).
   The sweep frames are pulled with the rest (DNGs stay on the unit).
3. Preview both:
   ```bash
   pc both preview
   ```
   See: `OK id=N pool preview img=<HHMMSS> n=<lines> bytes=<B> q=<q> eta=<s>s` per unit. Within about 30 s the viewer shows both images, marked **OK** with lines n/n. The image is the production 1600×900 crop, shown at 640×360.
4. **PARTIAL** on a card? The card shows the exact resend command. Run it, for example:
   ```bash
   pc bmcam003 preview --lines 20-21,35
   ```
   See: the card turns **OK**. Lines lost twice? Send a fresh `pc bmcam003 preview`.
5. Adjust the card and preview again until the framing is right on both units. If you move the card, run `sweep` again before the loop.
6. Mark the position (optional), then start the loop:
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
   **No sweep here.** The Pi refuses `sweep` for the whole loop phase (`ERR ... busy phase=loop`), between iterations too. The 2 m sweep runs after the loop ends (§7), with the card still at 2 m.
3. Optional framing check. During the loop it can wait up to about 35 s for the camera (`waiting for camera` line), hence the longer wait:
   ```bash
   pc both preview --wait 60
   ```

---

## 7. Loop ends, about 2:00 PM (3 h after loop start)

T-ENDURE: 18/18 iterations at 54–71 s each, mp4 ~7.3 MB, pool_sequence peak memory 135.8 MiB with no growth, 0 ERR / OOM.

See: the console shows `pool loop done 18/18 ...`, and `pc both status` shows `phase=idle loop=18/18`.

**2 m focus sweep**, with the card still at 2 m, before anyone moves it or takes the units out:
```bash
pc both sweep
```
See: `OK id=N pool sweep ... dir=sweep/<HHMMSS> ...` per unit, when done (`pool_cmd` waits up to 180 s).

To end early:
```bash
pc both stop
```
See: `OK id=N pool stop phase=loop stopping after the current step`.

---

## 8. Units on land → download, about 2:05 PM (~15 min; DNGs stay on the units)

The Pis stay up in pool mode (bus held) until the restore, so the data is safe on the SD card. Wi-Fi only works once the units are out of the water.

**DNGs stay on the units until the Friday pickup** (Nick, Mon 8:20 PM). The pool pull copies everything else, ~0.5 GB per unit.

**Measured** in T-ENDURE (both units in parallel over bench Wi-Fi, pool_pull 66a9f2e): **12 m 02 s total**.
- ~9.5 min of data:
  - bmcam003: 515.6 MB at 0.92–0.96 MB/s
  - bmcam004: 469.2 MB at 0.83–0.88 MB/s
  - ~1.75 MB/s combined
- then **2 m 22 s** of on-Pi DNG sha256 inventory
- left on each unit: **138 DNGs, 3.32 GB**
- the old plan, with DNGs (7.1 GB at ~1.67 MB/s combined), would have taken ~71 min

The hashing is repeated on every resume; `--no-dng-sha` skips it (count + bytes only). At pickup: the same command + `--include-dng`.

1. **Units out of the water, near the phone.** Run `pc both status`.
   See: `ip=172.20.10.y ssid=<home Wi-Fi name>` for both units. Use these IPs below. A `172.20.10.x` address confirms it's the hotspot, not a real home network.

2. **Run folder per unit** = the `run=` in that status line, the same one you wrote down in step 2.2. Cross-check that no second Tuesday folder exists (a reset mid-session):
   ```bash
   for ip in 172.20.10.A 172.20.10.B; do ssh pi@$ip 'hostname; du -sh /home/pi/pool_runs/*/ | tail -4'; done
   ```
   See: the session folder (e.g. `20261013_170512_bmcam003`) at about 3.5–4 GB, mostly DNGs. A second Tuesday folder on one unit means pulling both; see §10.

3. **Dry run:**
   ```bash
   python3 tools/pool_pull.py --host bmcam003=172.20.10.A --host bmcam004=172.20.10.B --run bmcam003=<folder3> --run bmcam004=<folder4> --dry-run
   ```
   See, per unit:
   - the classes with sizes, pulled in this order. T-ENDURE measured sizes and times per unit, at ~0.9 MB/s:
     1. manifest+logs: under 1 MB
     2. loop products, i.e. nrjxl + pjpg + sidecars: a few MB, seconds
     3. calibration JPEGs: 289–330 MB, ~6 min
     4. videos: ~126 MB (mp4 ~7.3 MB each), ~2.5 min
     5. loop native JPEGs: 51–57 MB, ~1 min
     6. other: ~0
     7. DNGs: 138 files, ~3.3 GB, marked `(LEFT ON UNIT: --include-dng to pull)`
   - a measured MB/s
   - `projected wall time`, about 10 min, plus ~2.5 min of DNG hashing

   Projected time > 30 min? Something's off with the link (§10). Pull classes 1–2 now (`--classes 1,2`: every nrjxl + pjpg, a few MB) and the rest later.

4. **Real pull** (same command without `--dry-run`). It pulls in priority order: manifest/logs → loop products (nrjxl + pjpg) → calibration JPEGs → videos → loop native JPEGs → other. The DNGs stay on the unit.
   See:
   - progress lines every 5 s, e.g. `[bmcam003] class 3 calibration JPEGs: 120.0 MB/330.0 MB files 45/240 | 0.90 MB/s`
   - per unit: `DNGs left on unit: <n> files, <GB>; sha256 on the Pi (one call) ...`, then `DNG inventory: <n>/<n> sha256`
   - at the end, `SUMMARY COMPLETE: left_on_unit=<n>, ok=<n>` and `LEFT ON UNIT: <n> DNGs, <GB>, <n> with a Pi sha256 in pull_report.json` per unit, then `[pool_pull] OK`
   - exit 0
   - files in `~/NereusPool/<run date>/<unit>/`. `<run date>` is the first 8 digits of the run folder name (UTC), e.g. `20261013`.
   - Interrupted? Run the same command again; it resumes.

5. **Read `~/NereusPool/<run date>/pull_report.json`:**
   - `units.<unit>.complete: true` means every file was checked with sha256, Pi against Mac.
   - `counts` should be only `ok` and `left_on_unit`. `missing` or `sha_mismatch` means re-run the pull. `skipped` means a class you left out with `--classes`.
   - `units.<unit>.left_on_unit` has the DNG count, bytes and how many have a Pi sha256. Each DNG is listed under `files` with `status: left_on_unit`, `size_pi` and `sha256_pi`: exactly what stays behind.
   - **Friday pickup:** the same command with `--include-dng` (keep the `--run` names). It resumes, so only the DNGs move, and each is checked against the Pi.
   - Then the in-air-style calibration check, for a quick look:
     ```bash
     python3 tools/pool_calib_check.py ~/NereusPool/<run date>/bmcam003/calibration
     ```
     See: `VERDICT PASS` (≥ 15 distinct poses, RMS < 1 px) and a `contact_sheet.jpg` next to the folder. Underwater frames go through a flat port, so treat it as an engineering check. **Detection is proven on real frames**: the rig session ran `pool_calib_check.py` (development 2d822c1) on 5 full-res nereus002 IMX708 frames of Nick's board at ~1 m, DNG and JPEG. Every frame had 104/104 corners and 63/63 markers. Auto layout picked **14×9 modern**. The verdict was FAIL, as expected for a single pose; a full calibration needs ≥ 15 distinct poses.

---

6. **Focus sweep review** (Mac, after the pull). Pull only the sweep folders, if the main pull skipped them or for a quick look first:
   ```bash
   python3 tools/pool_pull.py --sweep --host bmcam003=172.20.10.A --host bmcam004=172.20.10.B --run bmcam003=<folder3> --run bmcam004=<folder4>
   ```
   See: `sweep mode: <n> files under sweep/` per unit and `[pool_pull] OK`. DNGs stay on the unit. The report is `pull_report_sweep.json`.

   Then the cut sheet + plot. Pass the board distance and the medium (underwater at the pool).
   - **Every** sweep folder of each unit is analysed, one column per sweep, side by side, with the best-lens spread between them.
   - To pick specific sweeps, use `--sweep-dir bmcam003=<.../sweep/HHMMSS>` (repeatable) instead of `--unit`.
   - All frames of a unit are scored on ONE common board region: the best-detected frame's box.
   ```bash
   python3 tools/focus_sweep_sheet.py --unit bmcam003=~/NereusPool/<run date>/bmcam003 --unit bmcam004=~/NereusPool/<run date>/bmcam004 --board-distance-m 1.0 --medium underwater
   ```
   See:
   - `VERDICT bmcam00N: PASS|WARN best lp per sweep <x>, <y> (spread <d>)` per unit
   - `runs/focus_sweep_<UTC>/` holding `focus_sheet_<unit>.jpg` (scaled full frame + 1:1 board crop per lens), `focus_plot.png` (both units overlaid, best marked), `focus_sweep.csv` and `focus_sweep.json`

   A WARN that says "peak at the sweep edge" means the true focus may lie outside the swept range.

---

## 9. Restore to production, by about 3:30 PM (TE)

Poolside or back at HQ. The hil tools reach the units by name (`pi@bmcam003`, via Tailscale), and `hil/hil.env` `HIL_MONITOR` must be nereus000's **current** IP: the hotspot IP poolside, 192.168.1.45 on the bench. If the units don't resolve by name poolside, do this at HQ; they stay safe in pool mode on the held bus until then.
Per unit, in this order: bmcam003 / SPOT-33507C, then bmcam004 / SPOT-31593C.

1. **Stop pool** (and clear the pool lens args, so a later pool start uses the config again):
   ```bash
   ssh pi@bmcam003 'bash /home/pi/pool_mode.sh args --clear; bash /home/pi/pool_mode.sh stop; for i in $(seq 1 60); do pgrep -f "[p]ool_sequence.py" >/dev/null || break; sleep 3; done; bash /home/pi/pool_mode.sh status'
   ```
   See: `SIGTERM sent`, then (within ~2 min) a status with no `pool_sequence.py` process listed.

2. **Pool mode off** (crontab + config sha check):
   ```bash
   ssh pi@bmcam003 'F=~/pool_mode_off_$(date -u +%Y%m%dT%H%M%SZ).txt; bash /home/pi/pool_mode.sh off 2>&1 | tee "$F"; exit ${PIPESTATUS[0]}'
   ```
   See:
   - `crontab restored and verified byte-identical`
   - `config/state files bit-identical to pool-mode entry`
   - `PRODUCTION MODE`
   - exit 0

   Other exit codes (#152 @ a23f4d6):
   - **Exit 4**, `[ERROR] restored crontab is DISARMED ... re-arm with: crontab /home/pi/hil_backup/<TS>/crontab_ARMED.txt`. **This is the normal outcome under (E)**: the bus hold (§0B.2) always runs before `pool_mode.sh on`. Re-arm in step 3, which uses exactly that path. Each `off` run saves its output to its own dated `~/pool_mode_off_<UTC>.txt` on the Pi; nothing is overwritten.
   - **Exit 5**, `[ERROR] a config/state file changed`: stop and tell the EM.

3. **Check the crontab is ARMED** (always; required after exit 4):
   ```bash
   ssh pi@bmcam003 "crontab -l | grep -E '^@reboot.*rc_run_capture_cycle'"
   ```
   See: exactly one uncommented `@reboot ... rc_run_capture_cycle.sh` line.
   Nothing printed (`off` exited 4): re-arm from the path `off` printed on its `re-arm with:` line. `pool_mode.sh` lists only backups that hold an armed line.
   - The command takes that path from the newest `~/pool_mode_off_<UTC>.txt` (the `off` you just ran) and prints which file it read.
   - It installs the file only if it has the uncommented RC line and no `POOL_MODE_LINE`.
   - It first saves the current crontab to `~/crontab_before_rearm_<UTC>.txt` (restore: `crontab <that file>`).
   - After installing, it checks again. If any check fails, nothing is installed.
   ```bash
   ssh pi@bmcam003 'set -e; L=$(ls -t ~/pool_mode_off_*.txt | head -1); echo "off log $L"; B=$(sed -n "s/.*re-arm with: crontab //p" "$L" | head -1); [ -n "$B" ] || { echo "NO re-arm path in $L"; exit 1; }; echo "using $B"; grep -E "^@reboot.*rc_run_capture_cycle" "$B"; if grep -q POOL_MODE_LINE "$B"; then echo "REFUSED: $B has the pool line"; exit 1; fi; crontab -l > ~/crontab_before_rearm_$(date -u +%Y%m%dT%H%M%SZ).txt; crontab "$B"; echo installed; crontab -l | grep -E "^@reboot.*rc_run_capture_cycle"'
   ```
   See: `off log /home/pi/pool_mode_off_<UTC>.txt` (just now), `using /home/pi/hil_backup/<TS>/crontab_ARMED.txt` (the same path `off` printed), the RC line, `installed`, then the RC line again from the live crontab. `NO re-arm path`, `REFUSED` or no RC line: stop and tell the EM; `off` found no armed backup.

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
| `ERR id=N pool ...` | The unit refused: busy, out of space, or bad lines | Read the reason after the verb. `busy phase=...`: wait or `stop`. `sweep` with `busy phase=loop` is expected during the loop: run the 2 m sweep after the loop ends (§7). `space ...`: tell the EM, don't delete anything. |
| Preview **PARTIAL** | Console lines dropped | Run the resend command shown on the card (`pc <unit> preview --lines ...`). Twice PARTIAL: send a fresh `pc <unit> preview`. |
| Preview never appears | Camera busy (a loop clip), or the viewer is down | Wait 35 s. Then check that `http://<nereus000-ip>:8090/previews.json` loads; if not, restart the viewer (step 1.4). |
| Unit not on the hotspot (`ssid=none` in status) | 5 GHz hotspot, phone asleep, hotspot name/password not exactly the home Wi-Fi's, or the unit is still in the water | Maximize Compatibility ON; stay on the hotspot screen; check the phone name against the home SSID (curly `'`?); unit out of the water and close to the phone. Still nothing: **leave it**. The data is safe on the SD card and the Pi stays up; pull at HQ on the bench Wi-Fi. |
| Spotter battery low (< 3.7 V, assumed) | ~7 h on a held bus under (E): Tue ~8:30 AM → restore ~3:30 PM. Off-charger drain not measured (on the charger: −0.7 W at 1:12 PM, +0.41 W in T-ENDURE) | Plug the Spotter into deck power or the battery pack. If it can't be charged and the loop is near the end, `pc both stop` gives a clean finish rather than a power cut mid-file. |
| nereus000 offline: `ssh $N` times out, viewer down, no `pc` (T-ENDURE: ~4 PM) | nereus000 or its USB hub lost power, or it left the hotspot | **The units keep running on their own**: calibration auto-stops, and the loop auto-starts 30 min after calibration (verified in T-ENDURE). Check nereus000's power and hub, and the phone's connection count. Until it is back: no preview, no `mark` (write the time of the card move in §11 instead), no battery reading. After it returns, `pc both status` shows where the loop is. |
| Mac asleep / lid closed (T-ENDURE) | macOS sleep stops Terminal sessions and the pull | Lid open, on power, `caffeinate -dims` running (§1.2). A pull that stopped resumes with the same command. |
| Pull slow (dry run > 30 min without DNGs), or in a hurry | Hotspot / Wi-Fi throughput (T-ENDURE: 0.83–0.96 MB/s per unit, 12 min total). Two units share one access point. | Units closer to the phone; check `wlan0` power save is off. Add **`--no-dng-sha`** to skip the ~2.5 min DNG hashing (the report then has count + bytes only; re-run without it later to record the sha256s). `--classes 1,2` (the nrjxl + pjpg, a few MB) now and the rest later (re-run without `--classes`; it resumes). Diagnose afterwards with `tools/pool_link_ab.py pi@<ip>` (one unit, never during a pull). Or move everyone to `pool-wifi` (step 1.5): `ssh pi@<ip> 'nohup sudo nmcli connection up pool-wifi &'` per unit, then the Mac. The new IP shows in `pc both status`; nereus000 must move too. Pool networks may block device-to-device traffic. |
| `pool_pull` REFUSED: dest holds a different run | A second run folder for the same unit and date | Pull it with its own `--date`, e.g. `--date 20261013_run2 --run bmcam003=<folder>`. |
| `lp=` is not 0.8 in `status` / `pool ready` | `~/pool_args` missing or wrong (a reboot reads it again, so it was never set), or an old `pool_mode.sh` without `args` | **Before calibration only:** `ssh pi@<ip> 'bash /home/pi/pool_mode.sh args --lens-position 0.8'`, then the restart command at the end of §0B.6 (stop, wait, start). Then `pc <unit> status` shows `lp=0.8` and a new `run=`. `args rejected` / unknown `args`: the unit lacks #160; tell the EM. |
| `cal=` / `loop=` not 0 at step 2.3, or `ERR id=N pool cal refused: run already has calibration frames, restart pool mode ...` (same for `loop`; an auto-start that hits it prints `pool loop AUTOSTART FAILED refused: ...`) | This run folder already holds a session (not a fresh `start` on Tue AM, or a second `cal`) | **Before calibration:** the restart command at the end of §0B.6, using the unit's IP (`pool_mode.sh stop`, wait, `start`). Then `pc both status` shows a new `run=` and `cal=0/0`. **Mid-session:** `cal` or `loop` was sent twice. The first one is running or done; nothing to do, don't restart. |

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
