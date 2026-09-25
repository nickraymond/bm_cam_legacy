#!/usr/bin/env python3
# filename: scenarios.py
# description: Sprint26 S1 — the golden-vector scenario catalogue (what behaviour is pinned).
"""
Golden-vector scenarios (DESIGN_supervisor.md §8.1).

Every scenario starts from the bmcam003 profile (the bench unit S1 is gated on)
and applies small, exact text edits, so a scenario says precisely how it differs
from the unit. An edit that no longer matches the profile fails the run loudly
rather than silently testing something else.

Fields
  kind        "stills" | "video"
  utc         the Spotter UTC at t=0 (bmcam003 window: 10:00-15:00 America/New_York)
  edits       [(after_line, old_line, new_line)]: replace the first `old_line` at or
              after `after_line` (after_line None = from the top)
  append      YAML text appended to the profile
  argv        extra rc_progressive_jpeg CLI arguments (--transmit is always given)
  rules       scripted inbound commands (see world.World.fire); "{KEY}" in a payload
              becomes the seeded old media key
  seed        "old_media": an earlier keyed send exists (sent record + payload)
              "pending_heal": state holds an rsd heal for it
              "pending_trg": state holds an armed trg 2
  cam_failures  the camera fails this many invocations first
  app_ref     run BM_Devel_Pi as committed at this git ref instead of the working tree
  profile / profile_ref   start from this profile (repo path, optionally at a git ref)
  notes       what the scenario pins, for a reviewer reading a diff

Settings goldens (resolved config per profile, and per v1 state fixture) are
listed in SETTINGS_PROFILES / STATE_FIXTURES.
"""

IN_WINDOW = "2026-09-24T15:00:00+00:00"      # 11:00 America/New_York
OUT_WINDOW = "2026-09-24T21:00:00+00:00"     # 17:00 America/New_York
PHASE_UTC = "2026-09-24T15:03:40+00:00"      # 3:40 into a 5-minute lane

CMD_ON = "  enabled: true             # Sprint12: hlt/twn/trg remote config commands."
CMD_OFF = "  enabled: false"
STATE_LINE = '  state_path: "/home/pi/BM_Devel_Pi/bm_command_state.json"'
STATE_TMP = '  state_path: "{TMP}/state/bm_command_state.json"'

HALT_LINE = '  script_path: "/home/pi/BM_Devel_Pi/tuned_halt.sh"   # deployed copy of tools/power/tuned_halt.sh'
HALT_TMP = '  script_path: "{TMP}/tuned_halt.sh"'

# Every scenario: state file and halt script live in the run's temp dir (the
# harness creates a stub halt script there, so the REAL halt path runs and the
# fake subprocess records the call instead of halting anything).
BASE_EDITS = [("bm_commands:", STATE_LINE, STATE_TMP), ("power_halt:", HALT_LINE, HALT_TMP)]
COMMANDS_OFF = [("bm_commands:", CMD_ON, CMD_OFF)]

MEDIA_KEY = """
media_key:
  enabled: true
  retain_days: 14
"""

VIDEO_ISLANDS = """
video:
  dir: "{TMP}/videos"
  ui:
    enabled: false

video_tx:
  enabled: true
  duration_s: 5
  lead_in_s: 2
  output: "480x270"
  fps: 10
  message_cap: 126
  keyframe_repeat_max: 30
  preset: "medium"
"""

TO_VIDEO = [(None, 'capture_mode: "progressive_jpeg"', 'capture_mode: "video"')]
PHASE_ON = [("transmit_phase:", "  enabled: false", "  enabled: true")]

SCENARIOS = {
    # --- stills -------------------------------------------------------------------------
    "still_stock": {
        "kind": "stills", "utc": IN_WINDOW, "edits": BASE_EDITS + COMMANDS_OFF,
        "notes": "commands off, no media key: private UTC read, WS, capture, ladder, send, halt",
    },
    "still_bench": {
        "kind": "stills", "utc": IN_WINDOW, "edits": BASE_EDITS, "append": MEDIA_KEY,
        "rules": [
            {"when": "on_sub", "payload": {"id": 501, "c": "roi", "v": 2}},
            {"when": "after_tx", "n": 30, "payload": {"id": 502, "c": "ping"}},
            {"when": "tx_contains", "text": "<END", "payload": {"id": 503, "c": "help"}},
        ],
        "notes": "daemon on, keyed wire: command at boot, mid-burst ack slot, help in the tail",
    },
    "still_window_skip": {
        "kind": "stills", "utc": OUT_WINDOW, "edits": BASE_EDITS, "append": MEDIA_KEY,
        "notes": "outside the window: skip_win WS, final drain, halt (no listen tail today)",
    },
    "still_trigger": {
        "kind": "stills", "utc": OUT_WINDOW, "edits": BASE_EDITS, "append": MEDIA_KEY,
        "seed": ["pending_trg"],
        "notes": "an armed trg 2 bypasses the window once and clears",
    },
    "still_heal": {
        "kind": "stills", "utc": IN_WINDOW, "edits": BASE_EDITS, "append": MEDIA_KEY,
        "seed": ["old_media", "pending_heal"],
        "rules": [{"when": "on_sub", "payload": {"id": 100002, "c": "rsd", "h": [["{KEY}", "2"]]}}],
        "notes": "heal chunks before START, <HL> after END; a live rsd replaces the pending one",
    },
    "still_incomplete": {
        "kind": "stills", "utc": IN_WINDOW,
        "edits": BASE_EDITS + [("progressive_jpeg:", "  message_cap: 195         # field-tested hard cap on messages per image",
                                "  message_cap: 20")],
        "notes": "the ladder cannot fit 20 messages: bounded incomplete send",
    },
    "still_phase": {
        "kind": "stills", "utc": PHASE_UTC, "edits": BASE_EDITS + PHASE_ON, "append": MEDIA_KEY,
        "notes": "transmit_phase on: lane wait computed from the gate's Spotter UTC",
    },
    "still_capture_retry": {
        "kind": "stills", "utc": IN_WINDOW, "edits": BASE_EDITS, "cam_failures": 1,
        "notes": "first camera call fails: retry variant without --metadata succeeds",
    },
    # --- video --------------------------------------------------------------------------
    "video_stock": {
        "kind": "video", "utc": IN_WINDOW, "edits": BASE_EDITS + COMMANDS_OFF + TO_VIDEO,
        "append": VIDEO_ISLANDS,
        "notes": "one-clip video cycle, commands off, rev 3 wire",
    },
    "video_bench": {
        "kind": "video", "utc": IN_WINDOW, "edits": BASE_EDITS + TO_VIDEO,
        "append": VIDEO_ISLANDS + MEDIA_KEY, "argv": ["--bench-drop-chunks", "3,7"],
        "seed": ["old_media", "pending_heal"],
        "rules": [
            {"when": "on_sub", "payload": {"id": 511, "c": "ping"}},
            {"when": "after_tx", "n": 20, "payload": {"id": 512, "c": "awb", "v": 1}},
        ],
        "notes": "daemon on, keyed, heal before START, pump-only burst, bench drops 3 and 7",
    },
    "video_window_skip": {
        "kind": "video", "utc": OUT_WINDOW, "edits": BASE_EDITS + TO_VIDEO,
        "append": VIDEO_ISLANDS + MEDIA_KEY,
        "notes": "video outside the window",
    },
    "video_trigger_pending": {
        "kind": "video", "utc": IN_WINDOW, "edits": BASE_EDITS + TO_VIDEO,
        "append": VIDEO_ISLANDS + MEDIA_KEY, "seed": ["pending_trg"],
        "notes": "today a video unit does NOT service trg: it stays armed (DESIGN W5 changes this)",
    },
}

# --- field units -----------------------------------------------------------------------
# bmcam001/002 run `main` with commands off and cannot be updated until the mote
# cache lands (DESIGN §11): their wire is what the S6 backend must keep ingesting.
# Pinned to the main commit they run (origin/main at 2026-09-25; the bmcam001/002
# RC profiles there "match the 2026-07-31 live deploy" per commit 1a64c7b).
MAIN_SHA = "3d90ae43aea411fd8c9c88d9716b5c079a026e43"
for _unit in ("bmcam001", "bmcam002"):
    SCENARIOS[f"field_{_unit}_main"] = {
        "kind": "stills", "utc": IN_WINDOW, "app_ref": MAIN_SHA,
        "profile": f"device_profiles/{_unit}/camera_schedule.yaml", "profile_ref": MAIN_SHA,
        "notes": f"{_unit} as deployed: main's runtime + main's {_unit} profile (commands off)",
    }

# Resolved-settings goldens: every repo profile as-is ...
SETTINGS_PROFILES = [
    "BM_Devel_Pi/camera_schedule.yaml",
    "device_profiles/bmcam000/camera_schedule.yaml",
    "device_profiles/bmcam001/camera_schedule.yaml",
    "device_profiles/bmcam002/camera_schedule.yaml",
    "device_profiles/bmcam003/camera_schedule.yaml",
    "device_profiles/rc_field_template/camera_schedule.yaml",
]

# ... and bmcam003 under each v1 command-state fixture (the S2 migration must
# reproduce these exactly). Each is a list of (id, cmd, value) CommandState.record calls.
STATE_FIXTURES = {
    "roi5": [(1, "roi", 5)],
    "foc0_over_manual": [(1, "foc", 0)],
    "foc3": [(1, "foc", 3)],
    "awb1_exp4": [(1, "awb", 1), (2, "exp", 4)],
    "hlt3": [(1, "hlt", 3)],
    "win2_txd1_cap1": [(1, "win", 2), (2, "txd", 1), (3, "cap", 1)],
    "twn1_tmz2": [(1, "twn", 1), (2, "tmz", 2)],
}
