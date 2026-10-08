# Sprint28 B3a: cjxl VmPeak under the encoder guard (Linux arm64, desk)

**What:** the production B3a encode (`cjxl rgb.ppm -m 0 -e <e> -d <d> --num_threads=0`) of a REAL
bmcam004 frame (media 57521, 1600×900, `rgb.ppm` built by `rc_raw_jxl.rgb_codes`, headroom 2.1827)
under the unit's guard (`ulimit -v 256000` KiB = `ENCODER_MEM_LIMIT_BYTES`). Comparison: one v1 Bayer
plane (`R.pgm`, `-m 1 -e 5 -d 5.3`). `/proc/<pid>/status` sampled every 10 ms once the child is cjxl.

**Where:** Docker Desktop on the Mac (Apple Silicon), `debian:trixie` linux/arm64, `apt install
libjxl-tools` → **cjxl v0.11.2** [NEON]. Same OS and architecture as the units; NOT the Pi's CPU or
kernel, so the times are not Pi times. 3 reps each. Raw output: `results.txt`. Script:
`measure.sh`.

```bash
docker run --rm --platform linux/arm64 -v <dir with rgb.ppm, R.pgm, measure.sh>:/w debian:trixie \
  bash -c 'apt-get update -qq && apt-get install -y -qq libjxl-tools bc; cd /w; \
  ./measure.sh rgb_e5 256000 cjxl rgb.ppm /tmp/x.jxl -m 0 -e 5 -d 3.42 --num_threads=0'
```

| run | guard | VmPeak MiB | VmHWM MiB | wall s (container) | rc |
|---|---|---|---|---|---|
| v1 plane R, e5 d5.3 | 250 MB | 51.6 | 38.9–40.1 | 0.13–0.15 | 0 ×3 |
| **B3a RGB e5 d3.42** | 250 MB | **123.8–125.3** | 98.3–98.9 | 0.81–0.86 | **0 ×3** |
| B3a RGB e5 d2.6 | 250 MB | 123.8–124.1 | 98.3–98.9 | 0.81–0.82 | 0 ×3 |
| B3a RGB e4 d3.42 | 250 MB | 112.3–118.1 | 87.7–89.2 | 0.11–0.12 | 0 ×3 |
| B3a RGB e3 d3.42 | 250 MB | 118.1–120.2 | 88.1–89.8 | 0.11–0.12 | 0 ×3 |
| B3a RGB e5, no guard | none | 109.8–123.8 | 98.2–99.2 | 0.80–0.83 | 0 ×3 |
| guard teeth | 80 MB | 76.5 when killed | 49.8 | 0.06 | **1**, `JXL_FAILURE: Allocation failed` |
| guard teeth | 110 MB | 88.8 when killed | 78.9 | 0.72 | **1**, `Allocation failed` (`err_110MB.txt`) |
| guard | 150 MB | 125.3 | 98.3 | 0.79 | 0 |

(MiB = the `/proc` kB values / 1024; the guard is 256000 KiB = 250 MiB.)

**Reading:**
- B3a at 1600×900 peaks at **≤ 125.3 MiB virtual under the 250 MiB guard: 2.0× headroom, 0 kills in
  12 guarded runs.** RSS is ~99 MiB. The v1 plane's VmHWM here (38.9–40.1 MiB) is close to
  bmcam004's measured 31–39 MB, so the container is a fair proxy for memory.
- The guard has teeth: it kills at 80 / 110 MB. cjxl then exits rc 1 with "Allocation failed"
  (no signal), and `rc_raw_jxl.run_capped` classifies that as `mem` (its `alloc` word): **`rfb=mem`**,
  as designed.
- **e4 is ~7× faster than e5** in VarDCT here (0.11 vs 0.82 s), at nearly the same bytes (52.5 vs
  52.9 kB at d 3.42). One-frame quality check through the full search (`runs/s28_b3a_e2e_20261005/
  bmcam004_57521_e4/e2e.json`): e4 50.8 SSIMULACRA2 / 2.53 butteraugli at 194 msgs, vs e5 44.5 / 3.25 at
  184 msgs. The bytes are not equal, so this is not a decision: B0 should time e4 too, and a TG-7
  e4-vs-e5 equal-bytes check decides the default.
- NOT covered: Pi CPU time (ESTIMATE: e5 ≈ 0.82 s × ~12 ≈ 10 s per encode, e4 ≈ 1.4 s), the unit's
  own cjxl version (v0.11.1 vs this v0.11.2), and supervisor RSS during the prep. B0 on bmcam004
  measures them.
