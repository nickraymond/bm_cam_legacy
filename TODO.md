# Nereus / BM Camera TODOs

This file tracks follow-up work from the Bristlemouth camera Sprint 04 release-candidate effort. IDs are stable and should be referenced in commits, PRs, and sprint notes.

## Open

### TODO-BM-001 — Backend media rows for partial BM image attempts

**Status:** Open  
**Priority:** High after shipment  
**Area:** Backend / ingestion / gallery data model

Create a real gallery-visible media/capture-attempt record for every BM `<START IMG>` image transmission, even when image reconstruction fails.

Desired behavior:

- Complete BM image with all chunks → save real image media row as today.
- `partial_missing` / `partial_open` / reconstruct failure → create a media/capture-attempt row with a unique black placeholder image and explanatory telemetry.
- Do not reuse a shared placeholder `r2_key`, because media creation is usually idempotent by object key.
- Use unique placeholder keys per capture, for example:
  - `BMCAM_000/2026/07/09/2026-07-09T14-31-50Z_placeholder_partial_missing.jpg`
- Preserve true capture/transmission metadata:
  - START filename / timestamp
  - external node ID
  - expected chunks
  - received chunks
  - missing chunk list/count
  - `partial_open` vs `partial_missing` vs `reconstruct_error`
  - BM status
  - image byte estimate if available

Why this matters:

The gallery should truthfully show that the camera captured/transmitted on cadence, even when downstream chunk completion or reconstruction failed. This avoids confusing “missing gallery image” with “camera did not capture.”

---

### TODO-BM-002 — First-class BM gallery events endpoint

**Status:** Open  
**Priority:** Medium/high  
**Area:** Backend / frontend contract

Create a normalized endpoint for BM gallery/capture events so `gallery.html` does not need to call the admin Sofar message probe directly.

Suggested endpoint shape:

```text
/devices/{device_id}/bm-gallery-events?start_utc=...&end_utc=...&page_size=...&cursor=...
```

Each event should include:

- capture timestamp from START filename
- received/start/end timestamps
- status: `complete`, `partial_missing`, `partial_open`, `reconstruct_error`
- media ID if a real media row exists
- placeholder media ID if a placeholder row exists
- expected/received/missing chunks
- image size bytes
- format
- duration
- delta from previous capture
- selected camera metadata fields

---

### TODO-BM-003 — Exact UTC range and pagination for BM probe

**Status:** Open  
**Priority:** Medium  
**Area:** Backend / admin debug tools

Update `/admin/ingest/sofar-message-probe` to support exact UTC windows and true pagination.

Current RC workaround:

- Frontend sends `hours` and raises `max_images_per_node` to 50.

Desired behavior:

- Accept `start_utc` and `end_utc` explicitly.
- Accept `image_offset` / `cursor` and `max_images_per_node`.
- Return `has_more_images`, `next_image_offset`, and total count before limit.
- Avoid “custom UTC range” in UI silently becoming “look back N hours from now.”

---

### TODO-BM-004 — Replace RC gallery overlay with backend-backed placeholder media

**Status:** Open  
**Priority:** Medium  
**Area:** Frontend cleanup

The RC `gallery.html` BM overlay calls the admin Sofar probe directly and renders black placeholders for partial/open BM transmissions. This is useful for release-candidate review but should be replaced once TODO-BM-001 and TODO-BM-002 are complete.

Keep the useful UI behavior:

- show every BM capture attempt as a card
- show black placeholder when no rendered image exists
- show status badge
- show chunks received/expected
- show delta from previous capture
- detail view should explain why an image did not render

Remove or reduce:

- direct dependency on `/admin/ingest/sofar-message-probe` from production gallery
- frontend-only matching between BM START timestamps and media rows

---

### TODO-BM-005 — BM frontend metadata polish

**Status:** Open  
**Priority:** Medium  
**Area:** Frontend / UX

Clean up BM metadata display after shipment.

Include:

- requested vs actual camera controls grouped together
- START storage metadata labels:
  - `st` = SD total MiB
  - `su` = SD used MiB
  - `sf` = SD free MiB
  - `sp` = SD used percent
  - `im` = images dir MiB
  - `bf` = buffer dir KiB
  - `lg` = cron_logs dir KiB
  - `zh` = zero-byte HEIC count
- END camera metadata labels:
  - `rfm`, `rlp`, `rwb`, `rcg`, `rem`, `rsh`, `rag`
  - `et_us`, `ag`, `dg`, `cg`, `cct`, `lp`, `afs`, `ffom`, `lux`, `fd_us`, `stemp`
- avoid dumping raw debug-only fields by default
- keep raw START/END messages accessible for debugging

---

### TODO-BM-006 — Transmission completeness dashboard

**Status:** Open  
**Priority:** Medium  
**Area:** Diagnostics / frontend

Add a compact diagnostic summary for BM capture/transmission health over a selected window.

Useful aggregate metrics:

- expected cadence
- total capture attempts
- complete count
- partial/open count
- missing chunk count distribution
- largest HEIC size
- max buffer count
- max UART duration
- image cadence delta min/max/mean
- first and last capture timestamp

This should make it easy to prove whether failures are camera-side, BM/Sofar transport-side, or backend parsing-side.

---

### TODO-BM-007 — Spotter/power/GPS correlation sprint

**Status:** Open  
**Priority:** High after shipment / next diagnostics sprint  
**Area:** Spotter / power / backend diagnostics

Add a way to correlate expected camera power windows with actual Spotter status.

Motivation:

If Spotter cannot get GPS/time or does not power the camera at a scheduled interval, the Pi will not boot and cannot capture. Current BM image logs prove camera cadence only after the Pi boots and START messages arrive.

Desired data:

- Spotter GPS/time validity per interval
- camera power on/off command times
- Pi boot observations
- first BM message after boot
- reason for skipped power interval if available

---

### TODO-BM-008 — SD-card ring buffer

**Status:** Open  
**Priority:** Low/medium  
**Area:** Pi runtime storage management

Implement a local storage ring buffer after shipment.

Current state:

- SD reporting exists and is transmitted in START metadata.
- Ring buffer is intentionally not implemented yet.

Requirements:

- dry-run mode first
- never delete current active capture/transmit artifacts
- delete oldest safe artifacts first
- preserve recent logs and metadata sidecars where practical
- protect against deleting OS/system files
- include telemetry when cleanup happens


---

## RC follow-up items added 2026-07-09

### TODO-COLOR-001 — Local color-correction smoke test before/after shipment
**Status:** open  
**Priority:** high  
**Context:** Before building the full backend color-correction pipeline, create a small local smoke-test app/script that can take a backend-rendered BM image with the reef reference card visible, detect/use the card, apply a basic color correction, and write a corrected derivative for visual QA.  
**Acceptance criteria:**
- Input: one downloaded/rendered BM image.
- Output: corrected preview image plus a short metrics/QA summary.
- Does not block camera shipment unless the image/reference-card data is unusable.
- Later backend work can reuse the approach for batch/backfill processing.

### TODO-BM-005 — Investigate BM gallery completed-but-not-rendered images
**Status:** open  
**Priority:** medium  
**Context:** The RC BM gallery overlay shows some `complete` BM transmissions as black placeholders with “No gallery image rendered.” These should have enough chunks to reconstruct, so either the media row, display derivative, R2 upload, or gallery/media matching logic is incomplete.  
**Acceptance criteria:**
- For each complete BM transmission, determine whether reconstructed bytes exist.
- Determine whether a media row was created.
- Determine whether `display_key` / thumbnail derivative exists.
- Fix the pipeline so complete BM transmissions render as real gallery images when possible.

### TODO-BM-006 — Fix BM gallery overlay pagination and image count controls
**Status:** open  
**Priority:** medium  
**Context:** The RC BM gallery overlay is useful for debugging but broke or bypassed normal gallery pagination behavior. The page-size selector and navigation buttons need to work predictably for BM overlay mode.  
**Acceptance criteria:**
- Page size selector controls the number of BM cards shown.
- Previous/Next work in BM overlay mode.
- The count pill clearly distinguishes BM transmissions loaded vs renderable media rows.
- No regression to normal `/devices/{device_id}/media` gallery mode.

### TODO-BM-007 — Replace RC BM gallery overlay with backend-backed placeholder media rows
**Status:** open  
**Priority:** medium/long-term  
**Context:** The RC overlay proves the UX need: show every BM capture/transmission attempt, even if image reconstruction fails. Long term, the backend should create a real media/capture-attempt row for every BM START, with either a real image or a unique placeholder image plus telemetry explaining the failure.  
**Acceptance criteria:**
- One gallery-visible row per BM image START.
- Partial/missing/open/reconstruct-failed attempts show unique placeholder images.
- Telemetry records missing chunks, expected/received chunks, START/END metadata, and reconstruct status.
- Gallery no longer needs to call the admin BM probe endpoint for normal display.

