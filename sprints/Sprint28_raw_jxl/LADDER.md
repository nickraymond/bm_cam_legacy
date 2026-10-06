# Sprint28 HIL ladder: RAW → JPEG XL stills (R0–R4)

Owner: the **Test Engineer** (the bench owner). Written by the Sprint28 camera build session on
2026-10-02, desk only. Nothing here runs before the EM says R1 is cut (`r1-rc1`) **and** the TE
hands over the bench. Source: SPEC r4 §7.2. Format: `hil/README.md` (HIL-ready = the 6 items
below).

> **r4 ruling (Nick):** the default nrjxl crop is today's `still.crop` **1600×900 at native
> resolution**. R0.3 measures 1600×900 **first**, because that preset gates the feature.
> 2000×1124 and 2400×1350 are measured for information only. They are opt-in presets and never
> the default.

## The six HIL-ready items

| # | item | value |
|---|---|---|
| 1 | Spec | this file (+ SPEC r4 §7.2, CONTAINER.md v1) |
| 2 | Code ref | R0: the branch tip of `feature/sprint28-camera` (only its `rc_raw_jxl.py`, `config_registry.py` and `config_validate.py` are copied to `/tmp` on the unit; no deploy). R1–R4: the merged Sprint28 commit on `development`. Check on the unit: `grep -n nrjxl ~/BM_Devel_Pi/rc_uplink_messages.py` (`RC_RAW_FORMAT = "nrjxl"`) and `grep -n "REGISTRY_VERSION = 8" ~/BM_Devel_Pi/config_registry.py` |
| 3 | Criteria | the table in §2 |
| 4 | Inputs | each step below names its exact command and lane |
| 5 | Restore | §4: `reset` of the 4 still.* keys → `get` hash == the pre-test hash. Crontab is restored from the backup. `/tmp/s28r0_*` is gone (the probe deletes it). No `/boot` change (§1 R0, CMA branch) |
| 6 | Budget | R0: ~25 min per unit, 0 cellular, both units. R1–R3: ~2 h, 0 cellular (console lane), bmcam003. R4: 12 h, ~12 × 176 messages (the same as pjpg), bmcam003 (+ bmcam004 if its R0 passes). Nick: none (the bench is already wired) |

## 1. Steps

### R0: can the unit capture `--raw` and encode in time? (both units, before ANY code ships)

Run on **bmcam003 (SPOT-33507C)** first, then **bmcam004 (SPOT-31593C)**. Each unit gets its
own verdict.

1. `hil/tools/hil_new_run.sh s28_ladder` creates `runs/s28_ladder_<YYYYMMDD>/` (the current run).
2. `hil/tools/hil_unit_snapshot.sh bmcam003 before_r0` records runtime sha, config hash,
   crontab, CMA and disk.
3. Field-ops (CLAUDE.md §15): back up crontab, disarm cron, then stop the runtime. Use the
   `hil_deploy_unit.sh` disarm block or do it by hand. Write the restore command into `gate.log`
   BEFORE you run it:
   ```bash
   ssh pi@bmcam003 'mkdir -p ~/hil_backup/s28r0 && crontab -l > ~/hil_backup/s28r0/crontab_ARMED.txt'
   ```
   Restore: `ssh pi@bmcam003 'crontab ~/hil_backup/s28r0/crontab_ARMED.txt && sudo reboot'`.
4. `hil/tools/hil_s28_r0_probe.sh bmcam003 runs/s28_ladder_<YYYYMMDD>`. It refuses if any
   camera or runtime process is alive. It then runs:
   - 10 production captures without `--raw` and 10 with `--raw`, with CmaFree sampled every
     0.1 s (the Sprint07 method);
   - 10 encodes per preset, with 1600×900 first, from one real DNG.
5. `hil/tools/hil_s28_r0_analyze.py runs/s28_ladder_<YYYYMMDD> bmcam003` prints the R0.1–R0.4
   rows and writes `analysis/r0_{capture,encode,budget,verdict}_bmcam003.*`.
   The first output line names the R0.1 CMA source, e.g. `R0.1 CMA check: counter unavailable ->
   dmesg rule used (the kernel log of the --raw phase + rpicam stderr of every --raw capture)`.
   **Not measured ≠ FAIL.** The probe exits 3 if the CMA sampler wrote fewer than 3 rows in its
   first second, if the kernel has no CmaFree, or if fewer than 10 rows were pulled. The analyzer
   exits 2 when the CMA log has no usable `cap_raw_*` rows, and it writes no verdict then. In
   both cases R0.1 was never measured: fix the cause and re-run. Never score it as a unit FAIL.
   History: the #120 sampler ran an empty program, a heredoc + `< /dev/null` bug found by the rig
   study on nereus002 and fixed 2026-10-03.
6. Restore the crontab and reboot (step 3's restore line). `hil_unit_snapshot.sh bmcam003 after_r0`
   must show the same runtime sha + config hash as before.

**If R0.1 fails (CMA):** STOP. Do not change `/boot` from this ladder. The options at the S3
gate, in order of risk, are: (1) `--buffer-count 1` (re-run R0 with it added to the
probe's capture line); (2) raise `cma=` (a `/boot/firmware/cmdline.txt` change, with backup +
restore command, and **Nick's OK via the EM**); (3) re-plan. The EM decides.

**If R0.3 1600×900 fails:** STOP. nrjxl is not deployable on that unit. Report to the EM with
`r0_encode_<host>.csv`. hydrium is the SPEC's named fallback (Next sprint).

**If a larger preset passes:** report it. Raising `RAW_MAX_PX` (config_validate.py,
rc_raw_jxl.py) is a code change, made by the camera session in its own PR. It is never done on
the bench.

S0 desk note (runs/s28_s0_calibration_20261002): at e5, 2400×1350 does not get under 56 kB
even at distance 9 on the study frames. Its R0.3 encode runs at d 9.0 and cap 500, so it is a
**time/RSS** measurement only, not a one-wake fit.

### R1: one nrjxl still on the console lane (bmcam003)

Preconditions: the Sprint28 commit is deployed to bmcam003 through `hil_deploy_unit.sh`
(`--accept-print-config-diff`: registry v8 adds 7 keys, and the print-config diff shows exactly
those). Then run `hil/tools/hil_refresh.sh BMCAM_003 SPOT-33507C`. The unit runs stay_on with
the G5 values.

1. Record the pre-test hash: `hil/tools/hil_pistate.sh R1.before bmcam003`.
2. Switch to nrjxl at today's field of view, in ONE change. The backend refuses the keys
   until nvd vendors catalog registry v8. Until then use the console lane directly
   (`hil_cmd.sh`, console id range).
   ```bash
   hil/tools/hil_change.sh R1.set BMCAM_003 SPOT-33507C '{"set":{"still.format":"nrjxl","still.crop":[1504,846,1600,900]}}'
   # before nvd has catalog v8:
   hil/tools/hil_cmd.sh R1.set '{"id":52801,"c":"set","kv":{"still.format":"nrjxl"}}' 8 SPOT-33507C
   ```
3. Trigger one still and pull its evidence:
   ```bash
   hil/tools/hil_step.sh R1 bmcam003 SPOT-33507C - '{"id":52802,"c":"trg","v":2,"kv":{"med":"still"}}' 600
   ```
4. Pull the sent record (`app/sent/<stem>_compressed.sent.json`) into `pulled/R1/`. Copy the
   monitor's console log for the burst window (`$HIL_MONITOR_LOG_ROOT/...` on nereus000; see
   the `spotter-usb-console-capture` skill) to `console/R1.log`. Then reassemble:
   ```bash
   python3 hil/tools/hil_con_decode.py < runs/<run>/console/R1.log \
     | hil/tools/hil_s28_reassemble.py --sent runs/<run>/pulled/R1/<stem>_compressed.sent.json \
         --out runs/<run>/analysis/r1.nrjxl > runs/<run>/analysis/r1_reassemble.txt
   ```
   The tool is proven offline on the golden wire: `hil_s28_reassemble.py --in
   tests/golden/vectors_v9/v9_nrjxl/trace.txt --out /tmp/x.nrjxl` gives a 29/29 chunk rebuild
   whose sha256 equals the vector's sent record.
5. On the Mac, decode `analysis/r1.nrjxl` with the rig's study decoder. Save the mosaic shape,
   mean and NR header to `analysis/r1_decode.json`:
   ```bash
   PYTHONPATH=<rig>/src:<rig> <rig venv>/bin/python -c "from compression_study.methods import raw_planes as rp; import numpy as np, json; m = rp.decode(open('runs/<run>/analysis/r1.nrjxl','rb').read()); print(json.dumps({'shape': m.shape, 'mean': float(m.mean())}))"
   ```

### R2: backend (bmcam003; needs the nvd S2 PR on staging)

After R1's still is ingested (Sofar exposure lag 11–30 min):
- save the media row JSON to `api/R2_media.json` (`format=nrjxl`, complete, `display_key` set);
- the nvd session's jxl-oxide parity script on the same blob writes `analysis/r2_parity.json`.

### R3: forced fallbacks (bmcam003, console lane)

Each step is one change and then one trigger, with the evidence pulled by `hil_step.sh`. The
console shows the START.

| step | change | expected START |
|---|---|---|
| R3.1 | `{"set":{"still.format":"nrjxl","still.raw.encode_max_s":5}}` | `fmt=pjpg ... rfb=time`: one rung takes ~6.4 s on the Zero (study), which is over 5 s |
| R3.2 | `{"set":{"still.raw.distances":[0.3],"still.raw.target_fill":0.0,"still.raw.encode_max_s":60}}` | `fmt=pjpg ... rfb=fit`: the search is off (`target_fill 0` = fixed rungs only). d 0.3 gave 369 kB = 1282 msgs on the Mac (study frame cool stop −1, 1600×900, libjxl 0.11.1), against a cap of 195 |
| R3.5 | `{"set":{"still.raw.target_fill":0.97,"still.raw.distances":[3.8,4.6,5.95,8.25],"still.raw.d_max":0.5}}` | `fmt=pjpg ... rfb=floor`: the search plans d ≈ 3.8 > d_max 0.5, measures d 0.5 (far over the cap), and floors after 1 encode |
| R3.3 | `{"set":{"still.crop":[1505,846,1600,900]}}` with nrjxl on | ack `e:xk` (config_validate `_rule_raw_crop`); `get` hash unchanged |

### R4: production wakes (bmcam003, Sofar lane, 12 h)

Use the G5 cadence and bus values with nrjxl on and the default rungs. Make the change with
`hil/tools/hil_sofar_change.sh R4.set BMCAM_003 '{"set":{"still.format":"nrjxl"}}'`, then leave
the unit alone for 12 h. Evidence: `api/media_*.json`, the Spotter SD cycle logs in `pulled/`,
and the START `rfb` count.

## 2. Criteria

| id | criterion | PASS when | evidence |
|---|---|---|---|
| R0.1 | raw capture works at the unit's CMA | 10/10 `--raw` captures give DNG + JPEG, and **no failed CMA allocation during the `--raw` phase**, judged from what the kernel offers (the verdict's `cma_alloc_check` names the source): (1) `/sys/kernel/mm/cma/*/alloc_pages_fail` delta == 0, if the counters exist; (2) otherwise **"counter unavailable → dmesg rule used"**: no `cma_alloc` / alloc-failed / camera-buffer allocation line in the kernel log of the `--raw` phase (`dmesg_raw_phase.txt`) AND no libcamera / V4L2 / dma-heap allocation error in rpicam's own stderr of any `--raw` capture (`raw_cap_stderr.txt`). bmcam004's rpi trixie kernel has **no** CMA sysfs counters (TE 2026-10-05), so (2) applies there; (3) neither counters nor a readable kernel log: **NOT MEASURED** (analyzer exit 2), never a silent PASS. Info only: CmaFree (the kernel parks page cache in the CMA area, so a long-running unit shows a low CmaFree with full headroom: bmcam004 6.3 MB, 10/10, 0 errors) and debugfs `/sys/kernel/debug/cma/*` if readable without root (the probe never needs root) | `pulled/<host>_r0/{cma_counters.csv, dmesg_status.txt, dmesg_raw_phase.txt, raw_cap_stderr.txt, cma_samples.csv, cma_debugfs.txt}`, `analysis/r0_verdict_<host>.json` |
| R0.2 | capture time cost | median(`--raw`) − median(no `--raw`) ≤ 3 s | same |
| R0.3 | encode on the unit, **1600×900 first**, then 2000×1124 and 2400×1350 for information | per preset: 10 runs, median rung (4 planes, `--num_threads=0`) ≤ 20 s, peak RSS ≤ 120 MB (cjxl's **own** VmHWM, `peak_rss_kb`, since 2026-10-05; the wait4 figure `maxrss_incl_parent_kb` includes the parent process's RSS at fork and is not judged), 0 kills, CmaFree ≥ 1 MB during the encodes, and the predicted wake (capture + 2 rungs + 50 kB burst + 150 s tail from process start) ≤ 480 s. The 1600×900 row gates the feature. The largest PASS is reported for `RAW_MAX_PX` | `analysis/r0_encode_<host>.csv`, `analysis/r0_budget_<host>.csv` |
| R0.4 | tools present + memory guard has teeth | cjxl on PATH (version recorded), `import numpy` works, SD free recorded, and the encoder guard (`sh`: `oom_score_adj 1000` + `ulimit -v` 250 MB, then `exec`) **kills a 400 MB allocation** (`guard {"kind": "mem"}`). Without this, `rfb=mem` can never fire and an overrun swaps the Zero | `pulled/<host>_r0/env.txt` |
| R1.1 | console lane, one nrjxl still | START `fmt=nrjxl`; the reassembled console bytes' sha256 == the sent record's sha256; the NR header check is OK; the rig decoder decodes it to (900, 1600) | `analysis/r1_reassemble.txt`, `analysis/r1_decode.json` |
| R2.1 | backend | media row `format=nrjxl`, complete, display JPEG present; jxl-oxide == production decode | `api/R2_media.json`, `analysis/r2_parity.json` |
| R3.1 | forced `time` | START `fmt=pjpg rfb=time`, and that pjpg arrives complete | `commands.log`, `console/`, `api/` |
| R3.2 | forced `fit` | START `fmt=pjpg rfb=fit`, and that pjpg arrives complete | same |
| R3.3 | refused config | ack `e:xk`; `get` hash unchanged | `commands.log` |
| R3.5 | forced `floor` | START `fmt=pjpg rfb=floor`, and that pjpg arrives complete | `commands.log`, `console/`, `api/` |
| R3.4 | other `rfb` codes | `cap`, `dng`, `enc`, `mem`, `err` are **N/A on hardware** (forcing them needs edits on the unit). They are covered by the golden vectors `tests/golden/vectors_v9/v9_nrjxl_rfb_*` and `tests/test_s28_raw_jxl.py` | test log |
| R4.1 | production wakes | 12/12 wakes deliver an image (nrjxl or pjpg) complete ≤ 3 h, with 0 redundant heals | `api/media_*.json` |
| R4.2 | wake fits the window | halt uptime ≤ 570 s on 12/12 | `pulled/` cycle logs |
| R4.3 | fallback rate | ≤ 1/12 wakes fall back | START `rfb` count |
| R4.4 | still START time | uptime at START recorded per wake, with no-heal and heal wakes kept apart (it feeds SPEC §3.8) | cycle logs |

## 3. What a cycle log shows (for the operator)

```text
[RAW] still.format=nrjxl: target_fill=0.97 d_max=10.4 distances=[3.8, 4.6, 5.95, 8.25] effort=5 encode_max_s=30 keep_crop=False crop=[1504, 846, 1600, 900] (native px)
[DEBUG] Running RAW capture (one attempt, no retries): /usr/bin/rpicam-still ... --raw -o ..._native_full.jpg
[RAW] search1 d=3.499: 58345 B, 203 msgs, over_cap=True, budget_fit=True, 6.4 s, peak_rss=31040 KiB
[RAW] search2 d=3.812: 55205 B, 192 msgs, over_cap=False, budget_fit=True, 6.4 s, peak_rss=30752 KiB
[RAW] nrjxl ready: 55205 B, 192 msgs at d=3.812 att=2 (encode 12.8 s, elapsed=...)
```
A fallback shows `[RAW] FALLBACK rfb=<code>: <why>; sending today's JPEG`. If the pjpg is
re-selected against the remaining budget, the log adds `[RAW] pjpg re-selected ...`.

## 4. Restore (after R3, and after R4)

```bash
hil/tools/hil_change.sh RESTORE BMCAM_003 SPOT-33507C '{"reset":["still.format","still.raw.distances","still.raw.encode_max_s","still.raw.keep_crop"]}'
hil/tools/hil_change.sh RESTORE2 BMCAM_003 SPOT-33507C '{"reset":["still.raw.target_fill","still.raw.d_max"]}'
hil/tools/hil_pistate.sh RESTORE.after bmcam003   # config hash == R1.before
```
`still.crop` is reset too if R1 set it away from the YAML value. Crontab: as in R0 step 6.

## 5. Not tested here

Underwater colour (no real water), Iridium (nrjxl is refused there), bmcam001/002 (field units),
energy per wake (reported in O1, not gated), and the larger presets as a default (r4 ruling).
