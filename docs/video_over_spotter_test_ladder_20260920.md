# Short video over Spotter cellular — test ladder (2026-09-20)

**Goal (Nick):** send a 3–5 s video clip from a bmcam through the Spotter
API to the dashboard. No video-transmit code existed; video (Sprint15–18)
is SD-only.

**Approach:** remove one failure mode per step, cheapest first, so that a
failure on a real cellular cycle can only mean cellular/backend.

| Step | Question | Where | Status |
|---|---|---|---|
| 1 | Does a clip that fits the budget look like anything? | Mac + Pi timing | PASS |
| 2 | Is production framing byte-exact for video? What does loss do? | Mac | PASS |
| 3 | Is Pi → UART → BM bus → Spotter clean at production pacing? | bmcam004 + SPOT-33507C, zero cellular | PASS |
| 4 | transmit queue → Notecard → cellular → Sofar API → backend | real cellular | NOT RUN |

Source for all steps: first 10 s of
`2026-09-20T04-48-48Z_video_1920x1080_15fps.mp4` from bmcam003 (preset
`wide_1080p`, 9.3 Mbps, development `681ddb0`). Indoor bench scene, a
person waving a hand in the first ~2 s — a HARD case; a calm reef will
compress better.

> **Evidence policy:** this repo is public and the footage shows a person
> and a home interior. Only manifests and this document are committed. Cut
> sheets, CSVs, encodes, wire files, recovered clips and console logs stay
> in the local run folders (each has a `.gitignore` guard). Every number
> below is reproducible from the tools + any source clip.

## Budget (provenance)

| Constant | Value | From |
|---|---|---|
| chunk | 384 base64 chars = 288 raw bytes / message | `bm_serial.image_buffer_size` |
| pacing | 1.0 s / message | `bm_serial.image_transmit_delay_seconds` |
| PASS | ≤ 135 messages (38,880 B) | TODO-SPOT-001 interim mitigation, under the ~145-msg wall seen on bmcam001 / SPOT-33361C |
| WARN | ≤ 195 messages (56,160 B) | `progressive_jpeg.message_cap` |

START / END / ack overhead messages are not counted. The 135 wall was
measured on a different Spotter than the bench one (assumption).

## Step 1 — size sweep (`tools/bm_video_tx_size_sweep.py`)

96 cells: {3, 5} s × {240, 320, 480, 640} px × {5, 10, 15} fps × CRF
{28, 34, 40, 46}; libx264 veryslow, one keyframe. 66 PASS / 12 WARN / 18
FAIL. SSIM is vs a lossless reference at the same geometry (codec loss
only).

| Dur | Size | fps | CRF | Bytes | Msgs | Cycles@135 | SSIM | Status |
|---|---|---|---|---|---|---|---|---|
| 3 s | 480x270 | 10 | 34 | 37,257 | 130 | 1 | 0.937 | PASS |
| 5 s | 480x270 | 10 | 34 | 49,327 | 172 | 2 | 0.942 | WARN |
| 5 s | 480x270 | 10 | 40 | 25,118 | 88 | 1 | 0.892 | PASS |
| 5 s | 320x180 | 10 | 34 | 28,175 | 98 | 1 | 0.933 | PASS |
| 5 s | 640x360 | 5 | 40 | 35,966 | 125 | 1 | 0.918 | PASS |
| 5 s | 240x136 | 10 | 34 | 19,003 | 66 | 1 | 0.924 | PASS |
| 5 s | 640x360 | 10 | 34 | 77,195 | 269 | 2 | 0.948 | FAIL |

- **fps is cheap:** 5 → 15 fps costs ~1.2× bytes.
- **Duration cost is scene-dependent:** 5 s cost 1.32× of 3 s *here*
  because the motion is front-loaded (17.9 kB in second 0 → 4.7 kB in
  second 4). The keyframe is only ~3.9 kB of 49 kB — P/B frames dominate.
- **Resolution and CRF are what cost bytes.**
- **Pi Zero 2 W can encode it:** 480x270, 5 s, 10 fps, CRF 34, `nice -n 19`
  while the unit was recording 1080p: veryfast 5.4 s, medium 8.4 s,
  veryslow 23 s; sizes within 10 %; peak 52 °C, no throttle.

Nick's pick: **5 s / 480x270 / 10 fps / CRF 34** (172 msgs); first cellular
attempt uses **CRF 40** (88 msgs) for margin.

## Step 2 — loopback + loss (`tools/bm_video_tx_loopback.py`)

Framing is imported from production (`rc_transmit.split_base64_chunks`,
`rc_media_id.chunk_prefix`), not re-implemented. 6 payload variants × 10
loss scenarios; survivors shuffled before reassembly.

1. **Byte-exact** for all variants (sha256). Longest wire line 391 B. Every
   384-char chunk decodes independently — a lost message costs exactly its
   own 288 bytes.
2. **Tail cut is survivable with raw `.h264`:** the 172-msg clip cut at
   message 145 plays a clean first 3.4 s (34/50 frames). `mp4` with `moov`
   at the end → nothing decodable.
3. **One message is a single point of failure:** SPS/PPS ride in message
   #2; lose it → 0/50 frames. Messages #0–1 are x264's ~690 B
   version-string SEI (pure overhead; strip it). A keyframe every 1 s
   survives header loss (40/50) at +42 % bytes.
4. Loss inside the keyframe (msgs 2–16) smears the whole clip (SSIM
   0.94 → 0.77); loss later is near-invisible (0.940).

**MVP recommendation:** raw Annex-B `.h264`, single keyframe, SEI stripped,
SPS/PPS sent redundantly. The receiver must be told the fps — a raw stream
carries no timestamps.

Caveat: decode = ffmpeg with error concealment, an upper bound on what a
browser `<video>` would show. The backend should transcode recovered clips
before the dashboard plays them.

## Step 3 — bench UART (`bm_video_tx_bench_send.py`, `bm_video_tx_bench_verify.py`)

bmcam004 → `/dev/ttyAMA0` → BM bus → SPOT-33507C (fw v2.16.6) using
`bm_serial.spotter_print` (topic `spotter/printf`: console line, never
enters the transmit queue, zero cellular). Mac logged the Spotter USB
console with `tools/spotter_serial_monitor.py`; the clip was rebuilt from
that log alone.

| | |
|---|---|
| sent / received / exact | 172 / 172 / 172 |
| missing / corrupted / duplicate | 0 / 0 / 0 |
| rebuilt | 49,327 / 49,327 B, sha256 match, 50/50 frames |
| Spotter-clock gap | mean 1.048 s (1.016–1.07, sd 0.005) |
| burst | 179.2 s, 275 payload B/s |
| Spotter errors / queue-full | 0 |

Hardware state changes (all reverted, see the bench `run_manifest.json`):
runtime on bmcam004 stopped 05:49:14Z → restarted 05:54:01Z with the cron
command; one in-progress bench clip lost (boot sweep deletes `.part`);
crontab backed up, never modified.

How-to is captured as the skill `spotter-usb-console-capture`.

## Not tested (step 4)

`spotter/transmit-data` queue, Notecard fill, 5-min cellular blackout
lanes, the ~145-message wall on THIS Spotter, Sofar API delivery, backend
reassembly, START/END messages for video, two units sharing one Spotter.

## Tool bugs found and fixed during the work

- Muxing raw `.h264` → mp4 with `-c copy` silently drops a frame and
  scrambles pts when B-frames are present. Encode to mp4 first, then
  extract Annex-B with `h264_mp4toannexb`. (Bytes were never affected;
  SSIM and playback were.)
- ffmpeg `ssim` needs `settb=1/F,setpts=N` on BOTH legs; `setpts=N` alone
  pairs frames across different timebases.
- `pkill -f <pattern>` over ssh kills your own remote shell. Kill by PID.

## Reproduce

```bash
python3 tools/bm_video_tx_size_sweep.py --source <clip.mp4> --run-dir runs/<sweep>
python3 tools/bm_video_tx_loopback.py --ref runs/<sweep>/refs_lossless/ref_d5s_w480_f10.mkv \
    --crf 34 --fps 10 --run-dir runs/<loopback>
# Pi (runtime stopped):  cd ~/BM_Devel_Pi && python3 /tmp/bm_video_tx_bench_send.py \
#     --wire /tmp/h264_1key.txt --delay 1.0 --tag VTXFULL --out /tmp/send.csv
python3 tools/bm_video_tx_bench_verify.py --wire <wire.txt> --payload <payload.h264> \
    --console runs/<bench>/spotter_logs/SPOT-33507C/console_YYYYMMDD.log \
    --tag VTXFULL --fps 10 --out-dir runs/<bench>/verify_full
```
