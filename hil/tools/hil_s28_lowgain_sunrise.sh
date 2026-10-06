#!/bin/bash
# hil_s28_lowgain_sunrise.sh — Sprint28 sunrise low-gain test (Nick GO 2026-10-05; bmcam004, bus held
# on, Wi-Fi offload): every 3 min a PRODUCTION still capture pair, profile auto then low_gain
# (max_shutter_us 30000, max_gain 16), RAW crop + metadata kept, NO transmit. The loop runs ON the
# unit (hil/tools/s28_lowgain_pair_loop.py) from the deployed app dir; this wrapper copies it,
# starts / stops it and pulls the results.
#
# Usage:  hil/tools/hil_s28_lowgain_sunrise.sh <host> <local run dir> run      # foreground; Ctrl-C stops
#         hil/tools/hil_s28_lowgain_sunrise.sh <host> <local run dir> detach   # survives a Wi-Fi drop
#         hil/tools/hil_s28_lowgain_sunrise.sh <host> <local run dir> status|stop|pull
# Env:    S28LG_INTERVAL_S (180), S28LG_MAX_SHUTTER_US (30000), S28LG_MAX_GAIN (16), S28LG_MAX_PAIRS (0 =
#         until stopped), S28LG_KEEP_DNG=1 / S28LG_KEEP_JPEG=1 (24 MB / ~5 MB per capture; default off).
# Preconditions (the loop REFUSES otherwise): no camera / runtime process on the unit (stop the
#         runtime and disarm cron first, as for R0: LADDER R0 steps 1-3); #133 (+ #120) deployed.
#         The loop never edits cron; it records the crontab hash at start and end.
# Outputs: on the unit /home/pi/s28lg/run_<ts>/ (pairs/, pairs.csv, run_manifest.json, loop.log);
#         `pull` copies it to <local run dir>/pulled/<host>_lowgain/; then run
#         hil/tools/hil_s28_lowgain_analyze.py on that folder.
# Example: hil/tools/hil_s28_lowgain_sunrise.sh bmcam004 runs/s28_lowgain_sunrise_20261006 detach
# Limits: ~2 GB SD per 100 pairs with the defaults (PGM 2.9 MB + JPEG crop ~1 MB per capture).
set -u
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
H="${1:?host}"; OUT="${2:?local run dir}"; MODE="${3:-run}"; U="${HIL_UNIT_USER:-pi}"
hil_require_host "$H"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 $U@$H"
R=/home/$U/s28lg
mkdir -p "$OUT"
log() { echo "$(date -u +%FT%TZ) [$H] $*" | tee -a "$OUT/gate.log"; }

case "$MODE" in
  run|detach)
    TS=$(date -u +%Y%m%dT%H%M%SZ); RUN=$R/run_$TS
    $SSH "mkdir -p $R" < /dev/null || { log "ssh mkdir failed"; exit 1; }
    scp -q -o BatchMode=yes "$HIL_TOOLS_DIR/s28_lowgain_pair_loop.py" "$U@$H:$R/" || { log "scp failed"; exit 1; }
    ARGS="--out $RUN --interval-s ${S28LG_INTERVAL_S:-180} --max-shutter-us ${S28LG_MAX_SHUTTER_US:-30000}"
    ARGS="$ARGS --max-gain ${S28LG_MAX_GAIN:-16} --max-pairs ${S28LG_MAX_PAIRS:-0}"
    [ "${S28LG_KEEP_DNG:-0}" = 1 ] && ARGS="$ARGS --keep-dng"
    [ "${S28LG_KEEP_JPEG:-0}" = 1 ] && ARGS="$ARGS --keep-jpeg"
    log "low-gain sunrise $MODE: $RUN ($ARGS); loop $(shasum -a 256 "$HIL_TOOLS_DIR/s28_lowgain_pair_loop.py" | cut -c1-16)"
    echo "$RUN" > "$OUT/.lowgain_remote_run"
    if [ "$MODE" = run ]; then
      # -t: Ctrl-C reaches the loop as SIGINT (it stops cleanly and writes the manifest)
      ssh -t -o ConnectTimeout=8 -o ServerAliveInterval=15 "$U@$H" "cd /home/$U/BM_Devel_Pi && python3 -u $R/s28_lowgain_pair_loop.py $ARGS"
      log "loop ended (rc $?); pull with: $0 $H $OUT pull"
    else
      $SSH "cd /home/$U/BM_Devel_Pi && mkdir -p $RUN && setsid nohup python3 -u $R/s28_lowgain_pair_loop.py $ARGS > $RUN/nohup.out 2>&1 < /dev/null & echo \$! > $RUN/loop.pid; sleep 3; cat $RUN/loop.pid; tail -3 $RUN/loop.log 2>/dev/null || tail -3 $RUN/nohup.out" < /dev/null | tee -a "$OUT/gate.log"
    fi ;;
  status)
    RUN=$(cat "$OUT/.lowgain_remote_run"); $SSH "tail -5 $RUN/loop.log; wc -l < $RUN/pairs.csv; df -h /home | tail -1" < /dev/null ;;
  stop)
    RUN=$(cat "$OUT/.lowgain_remote_run")
    # SIGINT to the loop only (not its rpicam child): the current capture finishes, then it exits cleanly
    $SSH "kill -INT \$(cat $RUN/loop.pid) 2>/dev/null || pkill -INT -f '[s]28_lowgain_pair_loop.py --out $RUN'; for i in \$(seq 1 60); do kill -0 \$(cat $RUN/loop.pid) 2>/dev/null || break; sleep 1; done; tail -2 $RUN/loop.log" < /dev/null | tee -a "$OUT/gate.log" ;;
  pull)
    RUN=$(cat "$OUT/.lowgain_remote_run"); mkdir -p "$OUT/pulled/${H}_lowgain"
    scp -q -r -o BatchMode=yes "$U@$H:$RUN/." "$OUT/pulled/${H}_lowgain/" && log "pulled $RUN -> $OUT/pulled/${H}_lowgain" ;;
  *) echo "mode: run | detach | status | stop | pull" >&2; exit 1 ;;
esac
