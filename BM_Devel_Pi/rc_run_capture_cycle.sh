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

set -u

APP_DIR="/home/pi/BM_Devel_Pi"
LOG_DIR="$APP_DIR/cron_logs"

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

echo "[RC-CRON] running RC capture/transmit cycle (halt at end per power_halt YAML)..."
/usr/bin/python3 -u rc_progressive_jpeg.py --transmit
EXIT_CODE=$?

# If the halt is enabled and succeeded, the box is already shutting down and
# these lines may not land. Their absence + a halt_initiated line above IS the
# success signature for the rollup tool.
echo "[RC-CRON] rc_progressive_jpeg.py exit_code=$EXIT_CODE"
echo "[RC-CRON] end_utc=$(date -u --iso-8601=seconds 2>/dev/null || date)"
echo "============================================================"

exit $EXIT_CODE
