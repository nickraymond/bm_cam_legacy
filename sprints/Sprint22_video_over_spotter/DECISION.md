# Sprint 22 — Short video over Spotter: decisions

**Locked by Nick 2026-09-19** ("mint this as the new plan"). Evidence:
`docs/video_over_spotter_test_ladder_20260920.md`, PR #54, and a read-only
review of the backend repo (`nereus-vision-dev`, ref `origin/staging` @ 516b813).

**Goal:** a 3–5 s video clip travels bmcam → Spotter → cellular → Sofar API →
backend → dashboard, playable, without changing anything about how a JPEG
travels today. MVP, but close to good enough for paying customers: no major
refactor, no cut corners.

---

## D-S22-1 — Media type rides the existing `fmt` key in START

The device START already carries `fmt=pjpg`. Video sends `fmt=h264`.
**`fmt` absent, `pjpg`, or `heic` ⇒ today's behaviour, byte-identical.**
No new "media type" key is invented. (Earlier proposal `mt=` is dropped.)

JPEG today (real builder output, 213 B of the 285 B budget):

```text
<START IMG> filename: 2026-09-20T06-10-00Z_image_q35.jpg, timestamp: 2026-09-20T06:10:04Z, length: 112, fmt=pjpg, q=35, att=2, cmp=1, rk=1000p, tz=America/New_York, ws=0600, we=1800, sha=681ddb0d6c6b, hn=bmcam004
<I0>...  (112 chunks, 384 base64 chars each)
<END IMG> filename: 2026-09-20T06-10-00Z_image_q35.jpg, uart_duration_sec: 118.2, sent_buffers: 112, cpu_temp_c: 41.2
```

H.264 (this sprint):

```text
<START IMG> filename: 2026-09-20T06-10-00Z_video_5s.h264, timestamp: 2026-09-20T06:10:04Z, length: 88, fmt=h264, fps=10, dur=5.0, res=480x270, crf=40, cmp=1, tz=America/New_York, sha=681ddb0d6c6b, hn=bmcam004
<I0>...  (88 chunks — identical framing)
<I0>...  (chunk 0 repeated at the tail — D-S22-4)
<END IMG> filename: 2026-09-20T06-10-00Z_video_5s.h264, fmt=h264, uart_duration_sec: 93.1, sent_buffers: 88, cpu_temp_c: 41.2
```

Key rules (from the backend parser as it exists on staging):
- New keys go **after `length`** — the fast-path START regex is positional.
- No key may contain `len`, `chunks`, or `buffer` — the length regex
  `(?:chunks|length|len|buffers?)` has no word boundary.
- Unknown keys are already parse-safe (captured, ignored).
- `crf=` not `q=`: `q` is JPEG quality (higher = better); CRF is the opposite
  scale and would poison the quality telemetry.
- `fps=` is REQUIRED: raw H.264 carries no timestamps (ladder step 1).
- `dur=` / `res=` let the dashboard label a clip without decoding it.

## D-S22-2 — Chunks stay untyped; type is a property of the group

`<I n>` chunks carry no media type and need none. The backend runs a
per-node state machine: START opens a group; every `<I n>` belongs to it
until END, the next START, or a 600 s gap; at finalize the group's `fmt`
selects the JPEG or the video handler.

- Video uses the **legacy `<I n>` chunk form only.** The Sprint10
  `<I{gid}.{i}>` form is NOT understood by the backend (zero `gid` support on
  staging; such chunks are silently dropped). `media_gid` stays disabled.
- If START is lost the whole group is discarded — true for JPEG today too.
  Inherited weakness, **not fixed in this sprint** (Future hardening).

## D-S22-3 — Payload = raw Annex-B H.264, single keyframe, SEI stripped

From ladder step 2: raw `.h264` survives a tail cut (145 of 172 msgs → first
3.4 s play clean); `mp4` with `moov` at the end yields nothing. x264's ~690 B
version-string SEI (2 messages of pure overhead) is stripped on the device.
libx264 on the Pi Zero 2 W is viable (5–23 s for a 5 s 480x270 clip while
recording 1080p).

First cellular payload: **5 s, 480x270, 10 fps, CRF 40 = 25,118 B = 88 msgs**
(Nick's pick; CRF 34 = 172 msgs is the quality target once delivery is
proven). Cellular approval on record: **up to 300 messages** (Nick, 2026-09-19).

## D-S22-4 — Two cheap redundancies, no refactor

1. **Chunk 0 is sent twice.** With SEI stripped, chunk 0 holds SPS/PPS — the
   single point of failure (lose it → 0 frames). The backend already dedupes
   by index (keeps the longest payload per index), so a repeat costs one
   message and needs no backend change.
2. **`fmt` is repeated in END, and the backend sniffs the Annex-B start code.**
   START `fmt`, END `fmt`, filename extension and sniffed bytes must agree;
   disagreement is FLAGGED on the media row, never silently resolved.

## D-S22-5 — Backend stores the raw stream and serves an mp4 display variant

Browsers cannot play raw `.h264`. At ingest the backend stores the original
(`type=video`, `format=h264`, correct Content-Type at `put_object` — it
cannot be fixed later without a re-upload) and produces an **mp4 display
variant + a poster JPEG** in the slot the display JPEG occupies today. The
gallery grid keeps using `<img>` (poster); only the detail view gains a
`<video>`. A partial clip plays up to the gap with `is_complete=False`.

## D-S22-6 — Order of work: contract → backend → device → prove

Backend lands BEFORE any video is sent over cellular: without it a clip is
stored permanently as a broken `image/jpeg`, typed `image`, marked complete,
and the processing worker burns three failed jobs on it.

Branching: device work on `feature/*` off `development` (this repo).
Backend work on a feature branch off `staging` → PR to `staging`.
**`main` is never touched in either repo.**

## Explicitly OUT of this sprint (Next sprint / Future hardening)

`gid` chunk support · START-loss recovery · per-message retransmit · HEVC ·
multi-cycle clips (> 195 msgs) · customer-facing quality tiers · scheduled
(unattended) video sends.

## Open questions (must be answered in Phase 0, before backend code)

| # | Question | Why it matters |
|---|---|---|
| Q1 | Is `ffmpeg` available on the Render backend? | The mp4 display variant depends on it. Fallback: add it to the build, or a PyAV remux. Read the deploy config. |
| Q2 | Who sends on the device, and what triggers it? | In video mode the recorder process owns `/dev/ttyAMA0` for life (ladder step 3 only worked by stopping it). The send must live INSIDE that process — consistent with the BM-serial port-owner plan. Proposed trigger for the demo: a BM command. |
| Q3 | Privacy of test vectors | Both repos need golden vectors; this repo is PUBLIC. Use a synthetic ffmpeg test-pattern clip, never bench footage. |
