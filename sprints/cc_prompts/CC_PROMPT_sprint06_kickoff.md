# CC Session Kickoff Prompt — Sprint 06 (JPEG compression & partial-transmission DOE)

> Paste into a fresh Claude Code session started in the `bm_cam_legacy` repo root.
> Reusable across every Sprint 06 session — it self-selects the next Work Tracker item.

---

```text
Read CLAUDE.md (repo root) and sprints/Sprint06_jpeg_partial_transmission_sweep.md in full before doing anything.

Go to the Work Tracker in the Sprint 06 spec. Take the FIRST row that is not ✅ DONE or ⛔ DEFERRED
and whose "Depends on" item is ✅ DONE — that is your task for this session. Work only that ONE item.
(For P2, if compute is tight you may take one split — one encoder or one image — and say which.)

Execute it following CLAUDE.md and this workflow exactly:
1. Inspect first. Read the existing HEIC sweep (tools/bm_reference_card_heic_compression_sweep.py),
   the analyzer (tools/bm_reference_card_quality_v2.py), and prepare_reference_images.py. Do NOT
   write code yet.
2. Propose a short plan. For any real design choice, give options in a pros/cons table with a
   recommendation — on the first session that includes the spec's [D1] (new JPEG script vs extend
   the HEIC sweep) and [D2] (truncation model).
3. Wait for my approval of the plan.
4. Build on a feature branch, small focused commits. Mac-side only.
5. Validate the ARTIFACTS — open the cut sheets / decoded images and confirm they actually decoded;
   report bytes / base64_len / message_count / est_minutes. Trust the artifacts, not the exit code.
6. Update the item's Work Tracker row (status + findings) and hand off with the exact reproduce
   command and output paths.

Inputs (already committed — see reference_images/README.md):
- Reference card (AprilTags):  reference_images/reference_card_native_imx708.jpg
- Primary coral (no tags):     reference_images/reference_reef_coral_primary.jpg
- Robustness batch (P2/later): reference_images/reference_reef_coral_alt_01..07.jpg

Standing constraints:
- Mac-side DOE only. No Pi, no SSH (that is the deferred P4).
- Do NOT touch runtime/transport: bm_serial.py, process_image_v2.py, main_pi_camera.py,
  heic_encode_helper.py, camera_schedule.yaml. Add NEW files under tools/.
- Message count is computed on the base64-encoded length: ceil(base64_len / 300), NOT bytes/300.
- Card image = AprilTag detection (all 4 tags required to PASS). Coral image = sharpness / contrast
  / PSNR (no tags).
- One variable at a time. Write a timestamped, self-contained run folder (run_manifest.json, CSV,
  cut sheets, logs).
- Work on a feature branch; do NOT merge to main yourself — I review and merge.
```
