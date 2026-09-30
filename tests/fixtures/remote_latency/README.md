# remote_latency fixtures

`console_rsd_heal_w9.log` (tests/test_remote_latency_report.py):
- lines 3+ are verbatim nereus000 SPOT-33507C console (spotter_serial_monitor.py format), the
  2026-09-29 18:43Z wake of the S6b W9 proof (source: runs/s6b_w9_proof_20260929/pulled/
  console_heal_wake.txt, lines 1-156): the heal chunks `<I0dz8um.84/185>` and `.85/185` as
  `[BM_TX]` hex dumps, then the next clip's START.
- lines 1-2 are CONSTRUCTED in the recorded formats, not recorded: a Sofar-lane receive
  (`Remote message received(N)! "..."`, format from runs/remote_msg_latency/latency_log.jsonl)
  and the unit's console answer (`<epoch> <node>, [bmcam003] OK id=...`, format from
  runs/s5_console_20260928/pulled/). rsd 100077 was really published on the console, not
  through Sofar.
