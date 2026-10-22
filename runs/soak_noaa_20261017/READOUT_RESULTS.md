# NOAA soak read-out (bmcam003 / SPOT-31593C scored), TE5, Thu 10/22 ~10:20 AM PDT

Card `runs/soak_noaa_20261017/TEST_CARD.md @ 92921f76`, bars unchanged. Approval: Nick, TE5 chat, Thu 9:52 AM (APPROVAL.txt).
Ran a day late: stand-down Wed evening, approvals Thu 9:52 AM, and nereus000 off from Wed 8:50 PM (lost external power
Wed 6:45 PM; battery shutdown). The data window is unchanged; the late run only adds Sofar rows that arrived after the
instant, and the tool censors by instant.

## Pre-registered instant: Wed 10/21 11:30 AM PDT (2026-10-21T18:30:00Z)
Folder `readout_prep/readout_20261022T1704Z/` (readout_table.md, run_manifest.json, SHA256SUMS).
- Wakes: 53 / 53 scheduled hourly wakes seen. One had no `<WS>` at Sofar: Mon 12:00, the known SPOT-31593C mem_fault reboot.
- **Images complete ≤ 24 h: 9 / 9 scoreable** (P1 Mon 5/5, P2a Tue indoor 1/1, P2b Tue outdoor 3/3). Late-window images:
  4 / 4. 10 censored (< 24 h old).
- **Commands (soak loop) answered ≤ 3 h: 16 / 16.**
- FALLBACK / CFG_ERR / Traceback: 0 / 0 / 0. Max halt 480.64 s, msgs 194, cjxl peak 116868 KiB.
- Misses: none.

## Trap-7 re-run: Wed 10/21 6:30 PM PDT (2026-10-22T01:30:00Z), scores Tue's afternoon images
Folder `readout_prep/readout_20261022T1717Z/`.
- 60 / 60 wakes; **15 / 15 scoreable images complete ≤ 24 h**; 16 / 16 commands; 0 misses.

## Limits
- bmcam003 has had no Wi-Fi since Tue 4 PM, so its cycle logs after that weren't pulled. P2b halt/cjxl columns are
  partial: the image and command counts come from Sofar, the backend and the console.
- EXPECTATIONS_noaa.md was never locked (runbook trap 6). The TE4 dry run had already seen Tue data.
- Two TE3 refresh `get`s (1000183, 1000190) are `late`. They are outside the loop's n (data issue 3, for the Lead/PE).

## After the read-out (Nick's line)
- Command loop: not running. It died when nereus000 lost power; its last send was due Wed 8:30 AM. Checked Thu 10:04 AM
  `pgrep` = none.
- bmcam003: Branch B, config kept (no change).
