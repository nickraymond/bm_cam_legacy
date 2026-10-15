# TE2 lines for the Friday 10/16 envelope (approvals/2026-10-16_pool.md): pull everything, then back to bench production

**Units:**
- bmcam003 / SPOT-31593C (bridge 0e582dd12c1e1480);
- bmcam004 / SPOT-33507C (bridge c3c564b91856226c);
- console host nereus000: LAN pi@192.168.1.45, or Tailscale pi@100.82.38.22.

Never SPOT-33361C / bmcam001 / bmcam002 (the tool guards refuse them).

**State today:**
- Both units in PRODUCTION (hourly 3600000/600000) since 10/14 2:40 PM / 2:44 PM. fdeb01a, cfg 0cb7ad9e, keys ON (still.raw.progressive=true, uplink.media_key.heal_order=after).
- Pool-day files still on the units only:
  - all DNGs;
  - bmcam004's 119 missing files of 20261014_190821 (incl. sweep/204023 + sweep/210724);
  - the small 10/14 bench runs (bmcam003 20261014_172545, 20261014_172828; bmcam004 20261014_172553).

## Order (download BEFORE any production change: Nick 10/14)

0. **Pre:**
   - `grep -E '^export HIL_RIG_[AB]=' hil/hil.env` → swapped pairs;
   - HIL_MONITOR = nereus000 address reachable from the bench;
   - units dry, on home Wi-Fi (profile nereus-hq), HALTED: start between :12 and :55.
1. **Bus hold, both:**
   `HIL_RUN_DIR=<run dir> hil/tools/hil_bus_always_on.sh SPOT-31593C` and `… SPOT-33507C`
   → `bus held ON, <host> up + disarmed (backup ~/hil_backup/<TS>)`.
2. **Full pull** (resumable, sha256-verified DNGs):
   - dry run: `python3 tools/pool_pull.py --host bmcam003=bmcam003 --host bmcam004=bmcam004 --run bmcam003=20261014_175926_bmcam003 --run bmcam004=20261014_190821_bmcam004 --include-dng --dry-run`;
   - then the same without `--dry-run`;
   - plus the small bench runs: `rsync -a --partial <host>:/home/pi/pool_runs/<run>/ <dest>/<host>_<run>/` for 20261014_172545_bmcam003, 20261014_172828_bmcam003, 20261014_172553_bmcam004.
   - Dest: pool_pull's own ~/NereusPool layout, or ~/NereusData/bm_cam_legacy/2026-10-16_pool_pull/ if §11 is merged by then.
   - No deletes: the 10/14 review copies stay.
3. **Count check, per run, before step 4:**
   - Pi `find <run> -type f | wc -l` + `du -sb <run>` + `find <run> -name '*.dng' | wc -l` must EQUAL the same on the Mac;
   - pool_pull's per-file Pi sha256 = Mac sha256 for every DNG ("counts match the Pi listing" = files, bytes, DNGs, sha all equal).
4. **Restore to bench production, per unit:**
   - `bash runs/pool_rehearsal_20261012/deploy_s.sh rearm <host>` (byte-identical @reboot line from the hold backup);
   - `… deploy_s.sh halt <host>`;
   - `hil/tools/hil_restore_schedule.sh SPOT-31593C` / `SPOT-33507C` → `PRODUCTION schedule confirmed` (controller 1, 3600000/600000).
   - "Pool → bench schedule switch" = nothing to change: both Spotters have been on the production hourly schedule since 10/14. Only the physical move, which must happen at :12–:55. A Spotter restart while moving = a 2-min stub wake.
5. **Verify** the next :00 wake per unit (`bash runs/pool_rehearsal_20261012/hourly_verify.sh <host>`):
   - `[CFG] hash=0cb7ad9e`, keyed nrjxl, `-p` in the cjxl argv, msgs ≤ 195, no FALLBACK/ERR;
   - console submits;
   - a new backend media row per unit.

**cfg sets:** none planned. Keys stay ON.
- Optional, Nick's call: LED revert `cfg vle 1` + `cfg save` (needed only before at-sea; a save = a Spotter restart = a stub wake, so units disarmed or dry).
- `cfg hrpm 0` + `cfg save` if d26 has not landed by then.

**Power cycles:** none planned.
- Contingency only (Nick OK): nereus000 LiFePO4 WAKE_TIME cycle if the consoles drop (skill rig-power-cycle).

**Stop rules:**
- No restore / production change before step 3 passes (Nick 10/14).
- Unit not caught within 120 s of the hold commit → it runs one production cycle and halts; retry the hold after it halts. No forced cuts.
- A unit not on Wi-Fi → no pull for it; ask Nick (hotspot / move it).
- nereus000 VIN < 4.9 V, or VBAT falling while holding → end the holds (restore schedule).
- Spotter battery < 3.80 V → end the holds.
- Count/sha mismatch → re-run the pull (resumes) until equal. Never delete on the unit.
