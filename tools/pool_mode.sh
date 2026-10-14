#!/usr/bin/env bash
# pool_mode.sh — switch a bmcam unit into (or out of) POOL mode, reversibly.
#
# Repo path: tools/pool_mode.sh            Run ON the Pi (as pi):
#   bash ~/repos/bm_cam_legacy/tools/pool_mode.sh on [extra pool_sequence args]
#   bash ~/repos/bm_cam_legacy/tools/pool_mode.sh off | status | start | stop
#
# Pool mode (2026-10-13 pool test) = the @reboot RC cycle replaced by an
# @reboot launch of BM_Devel_Pi/pool_sequence.py, which owns the camera and
# the UART and waits for {"c":"pool",...} commands. CLAUDE.md §15/16:
#   on     backs up the crontab (timestamped, never overwritten), comments out
#          every rc_run_capture_cycle.sh line (prefix "# POOL_MODE_OFF "),
#          appends the pool line (tag "# POOL_MODE_LINE"), records the
#          effective config hash + sha256 of the config/state files. Takes
#          effect at the NEXT boot (or `start` now, on a unit with no cycle
#          running).
#   off    restores the EXACT crontab backup `on` made (LATEST pointer),
#          verifies it byte for byte, and checks the config/state files are
#          bit-identical to what `on` recorded. Exit 4 (ERROR, no PRODUCTION
#          line) when the restored crontab has no ARMED rc_run_capture_cycle
#          line (bus hold ran before `on`: re-arm from ~/hil_backup/<TS>/
#          crontab_ARMED.txt, printed); exit 5 when a config/state sha changed.
#          Does not stop a running pool process (use `stop`, or reboot).
#   status shows crontab, backups, pool processes, the newest run folder.
#   start  launches pool_sequence.py now (nohup, same command as the cron
#          line) — bench use; it refuses itself while an RC cycle runs.
#   stop   SIGTERM to running pool_sequence.py processes (it finishes the
#          current frame, writes its manifest, exits).
#   args   show / set / clear the extra pool_sequence args in ~/pool_args, which
#          pool_sequence.py reads ITSELF at every start (cron @reboot, `start`,
#          by hand), so no crontab change is needed. Validated before it is
#          written, e.g.:  pool_mode.sh args --lens-position 0.8
#          pool_mode.sh args --clear. Takes effect at the next start (stop +
#          start, or reboot).
#
# NOT touched: camera_config.yaml, bm_command_state*.json, images/, sent/,
# /dev/shm, Wi-Fi. Pool mode never calls network_ap.sh / the network boot
# apply: NetworkManager autoconnect owns wlan0 (at the pool HQ Wi-Fi is out of
# range and the boot apply would fall back to the open AP after 90 s, blocking
# ssh; EM 2026-10-12).
#
# Test hooks: BMCAM_DST, POOL_BACKUP_DIR, POOL_RUN_ROOT, HIL_BACKUP_DIR, CRONTAB
# (a fake crontab binary), POOL_MODE_NO_HASH=1 (skip the python config hash).

set -uo pipefail

DST="${BMCAM_DST:-/home/pi/BM_Devel_Pi}"
BACKUP_DIR="${POOL_BACKUP_DIR:-/home/pi/pool_mode_backups}"
RUN_ROOT="${POOL_RUN_ROOT:-/home/pi/pool_runs}"
HIL_BACKUP="${HIL_BACKUP_DIR:-/home/pi/hil_backup}"
ARGS_FILE="${POOL_ARGS_FILE:-/home/pi/pool_args}"
# An ARMED production line: @reboot ... rc_run_capture_cycle.sh, not commented.
ARMED_RE='^[[:space:]]*@reboot[^#]*rc_run_capture_cycle\.sh'
CRONTAB="${CRONTAB:-crontab}"
PY="${POOL_PYTHON:-/usr/bin/python3}"
LOCK="${BMCAM_CYCLE_LOCK:-/tmp/bmcam_rc_capture.lock}"
TAG="# POOL_MODE_LINE"
OFF_PREFIX="# POOL_MODE_OFF "
MODE="${1:-status}"
shift || true
EXTRA="$*"

say() { echo "[POOL-MODE] $*"; }
die() { echo "[POOL-MODE][ERROR] $*" >&2; exit 1; }

pool_cmd() {
    echo "$PY $DST/pool_sequence.py --run-root $RUN_ROOT${EXTRA:+ $EXTRA}"
}

cron_line() {
    # A held lock (an RC cycle?) or a crash at import is logged, not silent.
    echo "@reboot mkdir -p $RUN_ROOT && { /usr/bin/flock -n $LOCK $(pool_cmd) >> $RUN_ROOT/launch.log 2>&1 || echo \"pool launch exited rc=\$? (lock $LOCK held?)\" >> $RUN_ROOT/launch.log; } $TAG"
}

config_files() {
    for f in camera_config.yaml bm_command_state_v2.json bm_command_state.json camera_schedule.yaml; do
        [[ -f "$DST/$f" ]] && echo "$DST/$f"
    done
}

armed_candidates() {
    # hil_bus_always_on.sh keeps the pre-disarm crontab here; newest first, and
    # only files that really hold an armed line.
    local f
    for f in $(ls -1t "$HIL_BACKUP"/*/crontab_ARMED.txt 2>/dev/null); do
        grep -qE "$ARMED_RE" "$f" && echo "$f"
    done
}

pool_pids() {
    # The python process only (its argv starts with the interpreter), not the
    # flock wrapper whose argv also names pool_sequence.py.
    pgrep -f "^[^ ]*python3? [^ ]*pool_sequence\.py" 2>/dev/null || true
}

show_status() {
    say "crontab:"
    if "$CRONTAB" -l >/dev/null 2>&1; then
        "$CRONTAB" -l | sed 's/^/[POOL-MODE]   /'
        if "$CRONTAB" -l | grep -qF "$TAG"; then
            say "mode        : POOL (pool line installed)"
        else
            say "mode        : production (no pool line)"
        fi
    else
        say "  (no crontab)"
        say "mode        : no crontab"
    fi
    if [[ -f "$BACKUP_DIR/LATEST" ]]; then
        say "backup      : $(cat "$BACKUP_DIR/LATEST") (restored by off)"
    else
        say "backup      : none (pool mode was never turned on here)"
    fi
    if [[ -s "$ARGS_FILE" ]]; then
        say "pool args   : $(tr '\n' ' ' < "$ARGS_FILE")($ARGS_FILE, read at each start)"
    else
        say "pool args   : none ($ARGS_FILE absent: lens etc. from the effective config)"
    fi
    local pids
    pids="$(pool_pids)"
    say "pool process: ${pids:-not running}"
    local newest
    newest="$(ls -1d "$RUN_ROOT"/*_* 2>/dev/null | tail -1)"
    if [[ -n "$newest" ]]; then
        say "newest run  : $newest"
        [[ -f "$newest/run_manifest.json" ]] && "$PY" - "$newest/run_manifest.json" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1]))
print(f"[POOL-MODE]   cfg={m.get('config', {}).get('hash')} cal={len(m.get('calibration', []))} "
      f"loop={len(m.get('loop', []))} marks={len(m.get('marks', []))} "
      f"final={m.get('final_status', '(running)')}")
PYEOF
    fi
    if command -v df >/dev/null; then
        say "disk        : $(df -m "$(dirname "$RUN_ROOT")" 2>/dev/null | awk 'NR==2{print $4" MB free, "$5" used"}')"
    fi
}

case "$MODE" in
  on)
    mkdir -p "$BACKUP_DIR" || die "cannot create $BACKUP_DIR"
    [[ -f "$DST/pool_sequence.py" ]] || die "$DST/pool_sequence.py missing: deploy the pool branch first"
    if "$CRONTAB" -l 2>/dev/null | grep -qF "$TAG"; then
        die "pool line already installed (run off first; LATEST=$(cat "$BACKUP_DIR/LATEST" 2>/dev/null))"
    fi
    TS="$(date -u +%Y%m%dT%H%M%SZ)"
    BK="$BACKUP_DIR/crontab_$TS.txt"
    if "$CRONTAB" -l > "$BK" 2>/dev/null; then
        say "crontab backed up: $BK ($(wc -l < "$BK") lines)"
    else
        : > "$BK"
        say "[WARN] no crontab before pool mode (empty backup $BK; off will remove the crontab)"
    fi
    echo "$BK" > "$BACKUP_DIR/LATEST"
    if ! grep -qE "$ARMED_RE" "$BK"; then
        say "[WARN] the crontab being backed up has NO armed rc_run_capture_cycle line"
        say "[WARN] (bus hold ran first? # DISARMED_BUS_ON): off will restore it DISARMED and"
        say "[WARN] say so; re-arm from $HIL_BACKUP/<TS>/crontab_ARMED.txt after the session"
    fi
    SUMS="$BACKUP_DIR/config_sha256_$TS.txt"
    FILES="$(config_files)"
    if [[ -n "$FILES" ]]; then
        # shellcheck disable=SC2086
        sha256sum $FILES > "$SUMS" 2>/dev/null || shasum -a 256 $FILES > "$SUMS"
        say "config/state sha256 recorded: $SUMS"
    else
        say "[WARN] no config/state files under $DST to fingerprint"
    fi
    if [[ "${POOL_MODE_NO_HASH:-0}" != "1" ]]; then
        HASH_OUT="$("$PY" "$DST/pool_sequence.py" --print-config --config-dir "$DST" 2>&1)"
        echo "$HASH_OUT" > "$BACKUP_DIR/config_print_$TS.txt"
        H="$(echo "$HASH_OUT" | sed -n 's/^POOL_CONFIG_HASH=//p' | tail -1)"
        [[ -n "$H" ]] || die "effective config did not resolve (see $BACKUP_DIR/config_print_$TS.txt); crontab NOT changed"
        say "effective config hash: $H (focus/exposure as the pool will run them: $BACKUP_DIR/config_print_$TS.txt)"
    fi
    NEW="$BACKUP_DIR/crontab_pool_$TS.txt"
    { sed "/rc_run_capture_cycle\.sh/{/^#/!s/^/$OFF_PREFIX/;}" "$BK"; cron_line; } > "$NEW"
    "$CRONTAB" "$NEW" || die "crontab install failed; production crontab unchanged ($BK)"
    if "$CRONTAB" -l | grep -v '^#' | grep -q "rc_run_capture_cycle\.sh"; then
        die "an active rc_run_capture_cycle line survived; restore with: $CRONTAB $BK"
    fi
    "$CRONTAB" -l | grep -qF "$TAG" || die "pool line missing after install; restore with: $CRONTAB $BK"
    say "POOL MODE ON: the RC cycle is disarmed, pool_sequence.py launches at the next boot"
    say "  restore exactly: bash $(basename "$0") off   (or: $CRONTAB $BK)"
    RUNNING="$(pgrep -af "[r]c_progressive_jpeg.py|[r]c_run_capture_cycle.sh" 2>/dev/null || true)"
    if [[ -n "$RUNNING" ]]; then
        say "[WARN] an RC cycle is running NOW and owns the camera/UART:"
        echo "$RUNNING" | sed 's/^/[POOL-MODE]   /'
        say "[WARN] reboot (sudo reboot) so the pool script starts alone"
    fi
    show_status
    ;;
  off)
    [[ -f "$BACKUP_DIR/LATEST" ]] || die "no $BACKUP_DIR/LATEST: nothing to restore"
    BK="$(cat "$BACKUP_DIR/LATEST")"
    [[ -f "$BK" ]] || die "backup $BK missing"
    if [[ -s "$BK" ]]; then
        "$CRONTAB" "$BK" || die "crontab restore failed (backup kept: $BK)"
        if diff <("$CRONTAB" -l) "$BK" >/dev/null; then
            say "crontab restored and verified byte-identical to $BK"
        else
            die "crontab differs from $BK after restore — check by hand"
        fi
    else
        "$CRONTAB" -r 2>/dev/null || true
        say "crontab removed (there was none before pool mode)"
    fi
    RC=0
    TS="$(basename "$BK" .txt)"; TS="${TS#crontab_}"
    SUMS="$BACKUP_DIR/config_sha256_$TS.txt"
    if [[ -f "$SUMS" ]]; then
        if (sha256sum -c "$SUMS" 2>/dev/null || shasum -a 256 -c "$SUMS") | sed 's/^/[POOL-MODE]   /'; then
            say "config/state files bit-identical to pool-mode entry"
        else
            echo "[POOL-MODE][ERROR] a config/state file changed since pool mode went on (see above)" >&2
            RC=5
        fi
    fi
    PIDS="$(pool_pids)"
    [[ -n "$PIDS" ]] && say "[WARN] pool_sequence.py still running (pid $PIDS): 'pool_mode.sh stop' or reboot"
    mv "$BACKUP_DIR/LATEST" "$BACKUP_DIR/LATEST.restored_$(date -u +%Y%m%dT%H%M%SZ)"
    if "$CRONTAB" -l 2>/dev/null | grep -qE "$ARMED_RE"; then
        [[ $RC -eq 0 ]] && say "PRODUCTION MODE: the RC cycle runs at the next boot"
    else
        # QE B1 (#152): the pre-pool crontab is usually the bus-hold DISARMED one; the
        # exact restore is right, but it is NOT production. Say so and fail.
        echo "[POOL-MODE][ERROR] restored crontab is DISARMED (bus-hold before pool on?):" \
             "no active @reboot rc_run_capture_cycle.sh line; the unit will NOT capture" >&2
        CANDS="$(armed_candidates)"
        if [[ -n "$CANDS" ]]; then
            echo "[POOL-MODE][ERROR] re-arm with: $CRONTAB $(echo "$CANDS" | head -1)" >&2
            echo "$CANDS" | sed 's/^/[POOL-MODE][ERROR]   candidate (newest first): /' >&2
        else
            echo "[POOL-MODE][ERROR] no $HIL_BACKUP/*/crontab_ARMED.txt with an armed line:" \
                 "re-arm by hand (the line: @reboot /usr/bin/flock -n $LOCK" \
                 "/home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh)" >&2
        fi
        RC=4
    fi
    show_status
    exit $RC
    ;;
  start)
    PIDS="$(pool_pids)"
    [[ -z "$PIDS" ]] || die "pool_sequence.py already running (pid $PIDS)"
    mkdir -p "$RUN_ROOT"
    # </dev/null + setsid: `ssh host 'pool_mode.sh start'` must not hang on the
    # child holding ssh's stdin, and the process must outlive the ssh session.
    SETSID="$(command -v setsid || true)"
    nohup $SETSID /usr/bin/flock -n "$LOCK" $(pool_cmd) </dev/null >> "$RUN_ROOT/launch.log" 2>&1 &
    sleep 3
    PIDS="$(pool_pids)"
    [[ -n "$PIDS" ]] || die "pool_sequence.py exited at once: tail $RUN_ROOT/launch.log (RC cycle running? lock $LOCK held?)"
    say "started pid $PIDS; log: $RUN_ROOT/launch.log + the newest run folder's pool.log"
    ;;
  stop)
    PIDS="$(pool_pids)"
    [[ -n "$PIDS" ]] || { say "no pool_sequence.py running"; exit 0; }
    kill -TERM $PIDS && say "SIGTERM sent to $PIDS (finishes the current frame / loop iteration, up to ~2 min, then exits)"
    ;;
  args)
    if [[ -z "$EXTRA" ]]; then
        if [[ -s "$ARGS_FILE" ]]; then say "$ARGS_FILE: $(cat "$ARGS_FILE")"; else say "no $ARGS_FILE"; fi
        exit 0
    fi
    if [[ "$EXTRA" == "--clear" ]]; then
        rm -f "$ARGS_FILE"
        say "cleared $ARGS_FILE: the next start uses the effective config (stop + start to apply)"
        exit 0
    fi
    # Validate with the pool script itself (ignoring any current file) BEFORE writing:
    # a bad file must never meet a 7 AM cron start.
    OUT="$("$PY" "$DST/pool_sequence.py" --check-args --args-file /dev/null $EXTRA 2>&1)"
    if ! echo "$OUT" | grep -q "^ARGS_OK"; then
        echo "$OUT" | sed 's/^/[POOL-MODE]   /'
        die "args rejected; $ARGS_FILE unchanged"
    fi
    TMP="$ARGS_FILE.tmp.$$"
    echo "$EXTRA" > "$TMP" && mv "$TMP" "$ARGS_FILE"
    say "wrote $ARGS_FILE: $EXTRA"
    if [[ -n "$(pool_pids)" ]]; then
        say "[WARN] pool_sequence.py is running with its OLD args: pool_mode.sh stop, then start"
    else
        say "applies at the next start (pool_mode.sh start, or reboot)"
    fi
    ;;
  status)
    show_status
    ;;
  *)
    echo "usage: pool_mode.sh on [pool args] | off | status | start | stop | args [...|--clear]" >&2
    exit 2
    ;;
esac
