#!/usr/bin/env bash
# filename: configure_units.sh
# description: Sprint11 — set the per-arm YAML config on both bmcam units, then read it back.
#
# RUN THIS AFTER the runtime deploy and BEFORE re-arming cron. Both units must
# already be disarmed by catch_awake_disarm.sh (cron off, power_halt off) or
# this races their halt.
#
# WHAT IT SETS (Sprint11 SPEC §4 test matrix)
#
#   Unit A = bmcam003 (SPOT-33507C) — the candidate
#     txd 1.0 s   C2 on    C3 on    C4 150 s   win 13 min
#   Unit B = bmcam000 (SPOT-31593C) — production-ish control
#     txd 5.0 s   C2 off   C3 off   C4 0 s     win 18 min
#   Both: image_buffer_size 384, message_cap 195, bm_commands enabled,
#         src=1 reef primary (already in bm_command_state.json).
#
# C1 (capture-first) is NOT config — it is unconditional in the code, so both
# arms get it. The control is "Sprint10 minus the listen window", because that
# 90 s value was an error and keeping it would only preserve the bug.
#
# WHY win DIFFERS PER ARM (DEV_LOG F3): the C2 phase wait spends the same
# CycleBudget the transmit needs, so `win` must cover
#   capture/encode + worst-case lane wait + burst + listen tail + halt margin
#   Unit A: 5 + 300 + 197 + 150 + 20 = 672 s -> 13 min (780 s), inside the
#           15-minute bus window less ~55 s of boot.
#   Unit B: 5 + 0 + 977 = 982 s -> 18 min (1080 s), unchanged from Sprint10.
#
# The edit is done by tools/patch_camera_schedule.py: block-scoped, backed up,
# and LOUD if a key is missing (`enabled:` appears in four islands — an
# unscoped sed would silently edit the wrong one). Every value is then READ
# BACK, including through the runtime's own --print-config, because a config
# you did not read back is a config you do not have.
#
# INPUT   none (hosts pinned below)
# OUTPUT  <rundir>/configure_<host>_<TS>.log per unit
# EXAMPLE ./runs/sprint11_20260729/configure_units.sh
set -uo pipefail

RUNDIR="${1:-runs/sprint11_20260729}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$RUNDIR"

# host:name:txd:phase:defer:tail:win
UNITS=(
  "100.103.35.24:bmcam003:1.0:true:true:150:13"
  "100.119.14.92:bmcam000:5.0:false:false:0:18"
)

# Hard wall-clock bound on every ssh — see sshto.sh and
# INCIDENT_tailscale_ssh_check.md: `-o ConnectTimeout` bounds the TCP connect
# only, and a stalled handshake against a halting Pi hangs forever.
. "$(dirname "${BASH_SOURCE[0]}")/sshto.sh"

sshq() {
  local out rc
  out="$(ssh_to 120 "$1" "$2" 2>&1)"; rc=$?
  printf '%s\n' "$out" | grep -viE "^[[:space:]]*tailscale (login|status)"
  [ "$rc" -eq 124 ] && echo "[sshq][TIMEOUT] $1 did not answer within 120 s"
  return $rc
}

for entry in "${UNITS[@]}"; do
  IFS=: read -r ip name txd phase defer tail win <<< "$entry"
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$RUNDIR/configure_${name}_${ts}.log"
  {
    echo "=== CONFIGURE $name ($ip) $(date -u +%FT%TZ) ==="
    echo "--- target: txd=$txd phase=$phase defer=$defer tail=${tail}s win=${win}min ---"

    # Stage the patcher via /tmp — never into the repo tree, an untracked
    # file there blocks the next `git pull --ff-only` (field-update skill).
    scp_to 60 "$REPO/tools/patch_camera_schedule.py" "pi@$ip:/tmp/"

    echo "--- patch ---"
    sshq "$ip" "/usr/bin/python3 /tmp/patch_camera_schedule.py \
        /home/pi/BM_Devel_Pi/camera_schedule.yaml \
        --set bm_serial.image_transmit_delay_seconds=$txd \
        --set bm_serial.image_buffer_size=384 \
        --set progressive_jpeg.max_run_time_min=$win \
        --set progressive_jpeg.message_cap=195 \
        --ensure bm_commands.enabled=true \
        --ensure bm_commands.post_transmit_listen_s=$tail \
        --ensure bm_commands.defer_acks_during_transmit=$defer \
        --ensure transmit_phase.enabled=$phase \
        --ensure transmit_phase.grid_seconds=300 \
        --ensure transmit_phase.post_boundary_guard_s=30 \
        --ensure transmit_phase.pre_boundary_guard_s=20 \
        --ensure transmit_phase.max_wait_s=300"

    echo "--- READ BACK (the only thing that counts) ---"
    sshq "$ip" "cd /home/pi/BM_Devel_Pi
      echo -n 'txd          : '; grep -oE 'image_transmit_delay_seconds: *[0-9.]+' camera_schedule.yaml
      echo -n 'buffer       : '; grep -oE 'image_buffer_size: *[0-9]+' camera_schedule.yaml
      echo -n 'win          : '; grep -oE 'max_run_time_min: *[0-9]+' camera_schedule.yaml
      echo -n 'cap          : '; grep -oE 'message_cap: *[0-9]+' camera_schedule.yaml
      echo -n 'capture_mode : '; grep -oE 'capture_mode: *\"?[a-z_]+' camera_schedule.yaml
      echo '--- bm_commands island ---'; sed -n '/^bm_commands:/,/^[^ #]/p' camera_schedule.yaml | grep -E '^ '
      echo '--- transmit_phase island ---'; sed -n '/^transmit_phase:/,\$p' camera_schedule.yaml | grep -E '^ '
      echo '--- command state (src MUST be 1) ---'; cat bm_command_state.json 2>/dev/null; echo
      echo '--- resolved settings: what the cycle will ACTUALLY use ---'
      /usr/bin/python3 rc_progressive_jpeg.py --print-config 2>&1 \
        | grep -E 'pacing|message cap|transmit_phase|bm_commands|cycle budget|capture_mode'"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
  echo
done

cat <<'NEXT'
=========================================================================
CHECK BEFORE GOING FURTHER
  Unit A bmcam003: txd 1.0 | transmit_phase ON | defer_acks true | tail 150
                   | win 13 | "transmit_phase rule: ... -> fits"
  Unit B bmcam000: txd 5.0 | transmit_phase OFF | defer_acks false | tail 0
                   | win 18
  BOTH: src=1 in bm_command_state.json, cap 195, buffer 384,
        capture_mode progressive_jpeg

NEXT: arm (power_halt real + cron), THEN the Spotter power schedules LAST.
=========================================================================
NEXT
