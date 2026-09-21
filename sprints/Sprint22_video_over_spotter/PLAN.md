# Sprint 22 — Short video over Spotter: build plan

Decisions: `DECISION.md` (locked 2026-09-19). Tracker: `TRACKER.md`.
Four phases, each gated on the one before. One variable at a time.

```text
Phase 0  wire contract + golden vectors      both repos   no hardware
Phase 1  backend: ingest, store, display     nereus-vision-dev (staging)
Phase 2  device: clip -> encode -> transmit  bm_cam_legacy (development)
Phase 3  prove: bench console, then cellular bmcam004 + SPOT-33507C
```

---

## Phase 0 — Wire contract + golden vectors

**Deliverables**
- `docs/bm_media_wire_contract.md` (this repo; mirrored into the backend repo
  in Phase 1): `fmt` registry (absent/`pjpg`/`heic` ⇒ image, `h264` ⇒ video),
  START/END key tables, key-naming rules, chunk rules (legacy `<I n>` only,
  384 b64 chars, chunk-0 repeat), partial-clip semantics, agreement rule
  (START fmt / END fmt / extension / sniff).
- Golden vectors from a **synthetic** clip (`ffmpeg -f lavfi -i testsrc2`, 5 s,
  480x270, 10 fps, CRF 40, SEI stripped): payload, wire lines with real
  START/END, expected sha256, plus three damaged variants — tail cut, chunk 0
  lost (rescued by the repeat), one mid chunk lost. Small enough to commit;
  identical bytes in both repos.
- Extend `tools/bm_video_tx_loopback.py`: SEI strip, chunk-0 repeat, START/END
  via the real builders, so the vectors are generated, not hand-made.
- Answer Q1 (ffmpeg on Render) and Q2 (sender/trigger) from DECISION.md.

**Gate:** Nick signs off the contract. Key names are hard to change once
units are in the field.

## Phase 1 — Backend (`nereus-vision-dev`, feature branch off `staging`)

Follow that repo's `backend/CLAUDE.md` exactly (backup tag, PR to `staging`,
never `main`, never merge own PR, new Alembic revisions only).

| File | Change (all additive, behind `fmt`) |
|---|---|
| `services/bm_image_parser.py` | allow `.h264` in `ALLOWED_EXTS` (today any unknown ext is rewritten to `.jpg` — the worst landmine); read `fmt` from START/END, default ⇒ image; Annex-B branch in `sniff_format`; `media_type` on `ParsedBmImage`; agreement flag |
| `services/poll_once_ingest.py` | h264 branch in `_media_format_for_image` / `_content_type_for_image` / `_extension_for_image`; `type=video` instead of the three hardcoded `MediaType.image`; skip Pillow metadata + image derivative for video |
| `services/` (new, small) | video display variant: raw `.h264` + `fps` → mp4 + poster JPEG; partial clip ⇒ transcode what decodes, record playable seconds |
| `models.py` + new Alembic rev | `MediaFormat.h264`; idempotent `ALTER TYPE media_format ADD VALUE IF NOT EXISTS 'h264'` (`video` already exists in `media_type`) |
| `processing/enqueue.py` | verify only — filter is already `type == image` |
| `schemas.py`, `main.py` | widen `format`/`ext` patterns; content-type map |
| `dashboard/gallery.html` | detail view: `<video controls poster>` when `type=video`; grid keeps `<img>` on the poster; a "video" badge |
| `tests/` | golden vectors: complete, tail-cut, chunk-0-lost, mid-loss, fmt disagreement; **JPEG regression: same inputs ⇒ byte-identical rows/keys as before**; non-BM `SYS_0003`/`CAM_0003` regression per that repo's rules |

**Gate:** tests green; golden video renders and plays on the staging
dashboard from an admin poll against recorded rows; a legacy JPEG burst
ingests identically to before.

## Phase 2 — Device (`bm_cam_legacy`, `feature/*` off `development`)

- New module (working name `rc_video_clip.py`): pick the newest finished ring
  clip → cut N s → libx264 (480x270, 10 fps, CRF from config, one keyframe,
  SEI stripped) → raw Annex-B bytes. Refuses to upscale; logs geometry.
- `build_rc_video_start_message` / END `fmt` in `rc_uplink_messages.py`,
  same budget discipline and drop order as the still builder; `fmt`, `fps`,
  `dur`, `res`, `crf` are never-dropped base fields.
- Send through the EXISTING `rc_transmit` loop (budget guard, pacing, ack
  drain untouched) + chunk-0 repeat. The still path stays byte-identical —
  pinned by the existing tests.
- Runs inside the process that owns the UART (Q2). `video_tx:` YAML island,
  **disabled by default**; trigger = BM command for the demo.
- Message cap honoured; a clip that does not fit is refused loudly, not
  truncated silently.

**Gate:** unit tests (injectable tx/sleep/clock, zero-sleep); golden-vector
round trip; stills regression byte-identical; settings visible in `cfg`.

## Phase 3 — Prove it

1. **Bench, zero cellular** — skill `spotter-usb-console-capture`: real START
   / chunks / repeat / END to SPOT-33507C via `spotter_print`, rebuilt from the
   Mac console log, sha256 match.
2. **One cellular cycle, 88 msgs** (within Nick's 300-msg approval): deploy the
   sprint branch to bmcam004 only; verify with a raw Sofar API pull
   (`tools/count_complete_images.py` pattern), then on the staging dashboard.
   Record delivered-vs-sent, where any cut landed, Notecard fill.
3. If clean: CRF 34 (172 msgs) as the quality run.

**Success =** the clip plays in the staging dashboard detail view, typed
`video`, poster in the grid, original `.h264` in R2 with the right
Content-Type, and the next JPEG cycle from the same unit is unaffected.

## Risks

| Risk | Mitigation |
|---|---|
| The ~145-msg delivery wall exists on SPOT-33507C too (measured only on SPOT-33361C) | first payload is 88 msgs; raw H.264 degrades gracefully on a tail cut |
| No ffmpeg on Render | resolved in Phase 0, before any backend code |
| Both bench units share one Spotter | only bmcam004 transmits; bmcam003 untouched |
| Public repo + bench footage | synthetic vectors only; run-folder `.gitignore` guards |
| Field units pick up video code | `video_tx` disabled by default; field units stay on `main` |
