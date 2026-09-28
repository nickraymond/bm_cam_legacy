#!/bin/bash
# filename: rc_run_capture_cycle.sh
# description: Sprint08 P8 — boot-time cron wrapper for one RC progressive-JPEG cycle.
#
# Field model (bench soak = customer cadence emulated by the Spotter):
#   power applied -> boot -> THIS script runs one RC cycle -> RC performs the
#   power halt (per power_halt YAML) -> Spotter cuts/restores power.
#
# Timestamped log, settle sleep, exit-code logging (the HEIC wrapper this
# mirrored, run_capture_cycle.sh, was deleted in Sprint26 S1). Installed via crontab as:
#   @reboot /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh
#
# NOTE: when power_halt.enabled=true (soak config) the box halts at cycle end
# and SSH drops — that is success. Recovery is the next Spotter power cycle.

# Sprint26 S3b (PLAN_S3b.md H6/H7): the restart wrapper for mode.run: stay_on.
# The runtime decides the mode from its config; this script only reads exit codes:
#   70  stay_on failed (watchdog, error)  -> restart after backoff 10,20,40,80,160 s (cap 300)
#   71  stay_on RSS ceiling (clean exit)  -> restart after 5 s
#   72  stay_on config restart (Sprint26 S4 G10g: a command changed a next-boot
#       setting) -> restart after 5 s, NOT counted toward the crash-loop cap
#   >128 (not 143) while the stay_on marker exists (e.g. an OOM kill) -> as 70
#   anything else (per_boot 0/1/2, SIGTERM 143, stay_on stop 0) -> done, never loops
# 5 restarts within 10 min -> one last run with --crashloop (per_boot, halt forced
# to dry-run, one <WS a=crashloop>), then this script ends: the unit stays up and
# reachable over ssh with no RC process until the next boot.
# SIGTERM to this script is passed to the runtime and ends the loop (a per_boot
# unit halting now logs "SIGTERM: passing it..." + exit_code=143 at shutdown,
# where the script used to die silently). Stop step for
# tools on a stay_on unit: pkill -TERM, wait until the process is gone (an
# in-flight burst finishes first, up to ~5 min), only then -KILL.
# Test hooks (tests/test_s3b_wrapper.py only): BMCAM_APP_DIR, BMCAM_PYTHON,
# BMCAM_SLEEP, BMCAM_STAY_ON_MARKER.

set -u

APP_DIR="${BMCAM_APP_DIR:-/home/pi/BM_Devel_Pi}"
LOG_DIR="$APP_DIR/cron_logs"
PYTHON="${BMCAM_PYTHON:-/usr/bin/python3}"
SLEEP="${BMCAM_SLEEP:-sleep}"
MARKER="${BMCAM_STAY_ON_MARKER:-/dev/shm/bmcam_stay_on}"
RESTART_CAP=5            # restarts ...
RESTART_WINDOW_S=600     # ... within this many seconds -> crash-loop fallback
BACKOFF_MAX_S=300

mkdir -p "$LOG_DIR"

RUN_TS="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || echo unknown_time)"
LOG_FILE="$LOG_DIR/rc_cycle_${RUN_TS}.log"

exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "[RC-CRON] Sprint08 progressive-JPEG RC cycle starting"
echo "[RC-CRON] start_utc=$(date -u --iso-8601=seconds 2>/dev/null || date)"
# Sprint22: seconds from kernel boot to this line = "power-on to runtime start".
echo "[RC-CRON] uptime_s=$(cut -d' ' -f1 /proc/uptime 2>/dev/null || echo na)"
echo "[RC-CRON] user=$(whoami)"
echo "[RC-CRON] hostname=$(hostname 2>/dev/null || echo unknown_hostname)"
echo "[RC-CRON] app_dir=$APP_DIR"
echo "[RC-CRON] log_file=$LOG_FILE"

# Storage health snapshot: natives accumulate ~1.4 MB/cycle; watch the trend.
echo "[RC-CRON] disk: $(df -h / | tail -1)"
echo "[RC-CRON] images_dir: $(du -sh $APP_DIR/images 2>/dev/null | cut -f1)"

# Give the Pi, UART, and BM bridge time to settle after boot.
#
# CHANGED 2026-07-29 (Nick, Sprint11): 30 s -> 0.5 s.
# WHY: transmit must finish before the next 5-minute wall-clock boundary,
# where the Spotter blacks out its 2-slot cellular queue for ~9 s (median;
# 24 s at the 90th pct). Every second spent before transmit is a second of
# margin lost. Measured budget with the 90 s listen also removed:
#   power-on -> cycle running ~55 s, capture+encode ~5 s,
#   194 msgs @ 1.0 s = 194 s  ->  transmit ends ~4 min 14 s into the window,
#   i.e. ~46 s clear of the :05 boundary. Reclaiming this 30 s is a third
#   of that margin.
#
# ROLLBACK CANDIDATE: this settle existed to let the Pi, UART and BM bridge
# come up before the cycle touches them. 0.5 s is a deliberate bet that boot
# has already done that by the time cron runs. IF THE NEXT TEST SHOWS
# ANYTHING ODD -- UART open failures, missed time-sync, bridge not ready,
# first-message loss, decode errors early in a burst -- RESTORE 30 s FIRST
# and re-test before chasing anything subtler. See Sprint11 DESIGN D4.
sleep 0.5

cd "$APP_DIR" || exit 1

# Sprint26 S1: no per-boot py_compile any more. The syntax check lives where a
# broken file can still be fixed by hand: tools/deploy_rc_runtime.sh
# py_compiles every file in tools/rc_runtime_manifest.txt and runs a
# --print-config smoke test after copying (tools/rc_field_update.sh wraps it).
# The old boot list was also stale (it missed the command_* modules), and a
# syntax error still fails loudly here: python3 prints the traceback into this
# log and exits nonzero.

uptime_s() { cut -d. -f1 /proc/uptime 2>/dev/null || date +%s; }

STOPPING=0
CHILD=""
on_term() {
  STOPPING=1
  echo "[RC-CRON] SIGTERM: passing it to the runtime; no restart"
  [ -n "$CHILD" ] && kill -TERM "$CHILD" 2>/dev/null
}
trap on_term TERM

# Run the runtime in the background and wait, so a SIGTERM to this script is
# handled (the trap) while the runtime runs; wait again after a trap until the
# runtime itself has exited, so its exit code is the one reported.
run_rc() {
  rm -f "$MARKER"          # a stale marker must never make a per_boot death loop
  RUN_START=$(uptime_s)
  "$PYTHON" -u rc_progressive_jpeg.py --transmit "$@" &
  CHILD=$!
  wait "$CHILD"
  EXIT_CODE=$?
  while kill -0 "$CHILD" 2>/dev/null; do
    wait "$CHILD"
    EXIT_CODE=$?
  done
  CHILD=""
}

RESTARTS=""              # uptime of each restart (space separated)
BACKOFF_S=10
echo "[RC-CRON] running RC capture/transmit cycle (halt at end per power_halt YAML)..."
while :; do
  [ "$STOPPING" -eq 1 ] && break
  run_rc
  # A run that lasted longer than the crash window was healthy: backoff resets.
  [ $(( $(uptime_s) - RUN_START )) -ge "$RESTART_WINDOW_S" ] && BACKOFF_S=10
  # If the halt is enabled and succeeded, the box is already shutting down and
  # these lines may not land. Their absence + a halt_initiated line above IS the
  # success signature for the rollup tool.
  echo "[RC-CRON] rc_progressive_jpeg.py exit_code=$EXIT_CODE"
  WAIT_S=""
  CONFIG_RESTART=0
  if [ "$STOPPING" -eq 1 ]; then
    WAIT_S=""
  elif [ "$EXIT_CODE" -eq 70 ]; then
    WAIT_S=$BACKOFF_S
    BACKOFF_S=$(( BACKOFF_S * 2 > BACKOFF_MAX_S ? BACKOFF_MAX_S : BACKOFF_S * 2 ))
  elif [ "$EXIT_CODE" -eq 71 ]; then
    WAIT_S=5
  elif [ "$EXIT_CODE" -eq 72 ]; then
    WAIT_S=5
    CONFIG_RESTART=1
  elif [ "$EXIT_CODE" -gt 128 ] && [ "$EXIT_CODE" -ne 143 ] && [ -f "$MARKER" ]; then
    echo "[RC-CRON] stay_on runtime died by signal $(( EXIT_CODE - 128 )) (marker $MARKER)"
    WAIT_S=$BACKOFF_S
    BACKOFF_S=$(( BACKOFF_S * 2 > BACKOFF_MAX_S ? BACKOFF_MAX_S : BACKOFF_S * 2 ))
  fi
  rm -f "$MARKER"
  [ -z "$WAIT_S" ] && break

  if [ "$CONFIG_RESTART" -eq 1 ]; then
    echo "[RC-CRON] stay_on config restart (a next-boot setting changed) in ${WAIT_S}s; " \
         "not counted toward the crash-loop cap"
    "$SLEEP" "$WAIT_S" &
    SLEEPER=$!
    wait "$SLEEPER"
    kill "$SLEEPER" 2>/dev/null
    [ "$STOPPING" -eq 1 ] && break
    echo "[RC-CRON] restart_utc=$(date -u --iso-8601=seconds 2>/dev/null || date)"
    continue
  fi

  NOW=$(uptime_s)
  KEPT=""
  N=0
  for T in $RESTARTS; do
    if [ $(( NOW - T )) -lt "$RESTART_WINDOW_S" ]; then KEPT="$KEPT $T"; N=$(( N + 1 )); fi
  done
  RESTARTS="$KEPT $NOW"
  N=$(( N + 1 ))
  if [ "$N" -gt "$RESTART_CAP" ]; then
    echo "[RC-CRON] CRASH LOOP: $RESTART_CAP restarts within ${RESTART_WINDOW_S}s; last run " \
         "with --crashloop (per_boot, halt dry-run), then no more restarts until reboot"
    run_rc --crashloop
    echo "[RC-CRON] rc_progressive_jpeg.py --crashloop exit_code=$EXIT_CODE"
    rm -f "$MARKER"
    break
  fi
  echo "[RC-CRON] stay_on restart $N (of $RESTART_CAP in ${RESTART_WINDOW_S}s) in ${WAIT_S}s"
  "$SLEEP" "$WAIT_S" &          # background + wait: a SIGTERM ends the backoff at once
  SLEEPER=$!
  wait "$SLEEPER"
  kill "$SLEEPER" 2>/dev/null
  [ "$STOPPING" -eq 1 ] && break
  echo "[RC-CRON] restart_utc=$(date -u --iso-8601=seconds 2>/dev/null || date)"
done

echo "[RC-CRON] end_utc=$(date -u --iso-8601=seconds 2>/dev/null || date)"
echo "============================================================"

exit $EXIT_CODE
