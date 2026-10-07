# Heal throughput vs loss — notes (Test Engineer, 2026-10-03 ~03:45Z)

Source: staging `GET /admin/ingest/devices/BMCAM_004/heal-candidates?hours=6` (read-only, from nereus000).

- Missing chunks are the TAIL of each clip (e.g. 0e5hou 03:00Z: 145-184 of 188, missing_total 43).
- The backend packs ONE rsd per wake per device: newest clip first, greedy, <= 40 chunks
  (`heal_commands.pack_command`); a clip whose missing count does not fit is truncated (0e5hou: 40 of 43)
  and OLDER clips are `left_out` ("chunks > 40").
- Unit side: `rc_heal.HEAL_CAP_PER_WAKE = 40`, `command_messages.RSD_MAX_CHUNKS = 40` (an rsd with > 40
  chunks is REFUSED by the unit).
- Consequence: with per-hour loss (~40-45 chunks on SPOT-31593C outdoors) ≈ the 40-chunk cap, every wake
  heals (most of) the NEWEST clip and the remainder of every older clip is never served -> older normal
  clips never complete; D1 (≤ 3 h) fails by construction, independent of newest/oldest order.
- Any fix needs per-wake heal capacity > per-hour loss + backlog drain (cap > ~45 + margin), or less loss.
