#!/usr/bin/env bash
# filename: catch_awake_disarm.sh
# description: Sprint10 Phase E pre-flight — catch each bmcam Pi the moment its
#              BM-bus power window opens and disarm it per PHASE_E.md §3.1-3.3.
#
# WHY
#   Both units are field-normal: @reboot cron runs a capture cycle on every
#   power-up and that cycle HALTS the box in its finally block, and the Spotter
#   only powers the bus 20 min in every 60. So the Pi is reachable for a short,
#   unpredictable window. This script polls until SSH answers, then does the
#   three disarm steps in the safe order before the running cycle can halt:
#     1. back up crontab, comment out @reboot        (PHASE_E.md §3.1)
#     2. SIGTERM any in-flight cycle - NOT SIGKILL, NOT halt (§3.2)
#     3. power_halt enabled:false / dry_run:true in camera_schedule.yaml (§3.3)
#
#   Order matters: cron off first so a power blip cannot start a new cycle,
#   then kill the current one, then belt-and-braces the YAML.
#
# INPUTS   none (hosts hardcoded below - the two Phase E bench units)
# OUTPUTS  <outdir>/disarm_<host>_<TS>.log   full transcript per unit
#          <outdir>/disarm_state.json        machine-readable result per unit
#          crontab backup ON the Pi: /home/pi/crontab_backup_phaseE_<TS>.txt
#                                    (this filename is the §6 re-arm source)
#
# EXAMPLE  ./catch_awake_disarm.sh runs/sprint10_phaseE_20260728
#
# LIMITATIONS
#   - Assumes Tailscale SSH key auth already works (it prints a banner line
#     that we filter). Does not power anything on; if the bus never comes up
#     this loops until --deadline and exits nonzero.
#   - Does NOT touch Spotter power config (Nick's call, PHASE_E.md §3.4).
set -uo pipefail

OUTDIR="${1:-runs/sprint10_phaseE_20260728}"
DEADLINE_MIN="${2:-75}"          # 75 min > one full 20/40 Spotter period
HOSTS=("100.103.35.24:bmcam003" "100.119.14.92:bmcam000")
mkdir -p "$OUTDIR"
STATE="$OUTDIR/disarm_state.json"

sshq() {  # ssh with the Tailscale auth banner filtered out
  ssh -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=no \
      "pi@$1" "$2" 2>&1 | grep -viE "tailscale|authenticate"
}

disarm_one() {
  local ip="$1" name="$2" ts log
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  log="$OUTDIR/disarm_${name}_${ts}.log"
  {
    echo "=== $name ($ip) disarm at $(date -u +%FT%TZ) ==="
    # ALL THREE DISARM STEPS IN ONE SSH ROUND TRIP.
    # This is a RACE against the @reboot cycle's power_halt: when the bus is
    # forced on (bridgePowerControllerEnabled=0) rather than opening on its
    # own schedule, the Pi boots with cron still armed and the cycle can end
    # -- and halt the box -- within a minute if it takes the a=skip_win fast
    # path (finding 008). A halt onto a continuously-powered bus is one-way
    # (finding 004). Separate ssh calls cost a round trip each; one compound
    # call closes the window to ~1-2 s.
    #
    # SIGTERM FIRST, deliberately inverting PHASE_E.md §3.1/§3.2 order: the
    # imminent threat is the RUNNING cycle's halt, and @reboot cron has
    # already fired by definition, so it cannot start a second one. Cron is
    # disabled immediately after, before anything else can reboot the unit.
    # NOTE the [r] bracket trick in every pkill/pgrep pattern below.
    # BUG FOUND ON THE BENCH 2026-07-29T00:50Z: `pkill -f
    # 'rc_run_capture_cycle.sh|...'` matches the REMOTE SHELL'S OWN
    # COMMAND LINE, because ssh passes this whole script as one argv
    # string that literally contains the pattern. pkill SIGTERM'd its own
    # parent shell, so bmcam003 got step 1 and nothing else — cron and
    # power_halt were left ARMED on a now-permanently-powered bus, the
    # exact one-way trap this script exists to prevent (finding 004).
    # `[r]c_progressive_jpeg` matches the real process but NOT this
    # command line, which contains the literal text `[r]c_...`.
    sshq "$ip" "set -x
      pkill -TERM -f '[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py'
      crontab -l > /home/pi/crontab_backup_phaseE_${ts}.txt
      crontab -l | sed 's|^@reboot |# DISABLED phaseE ${ts}: @reboot |' | crontab -
      cp /home/pi/BM_Devel_Pi/camera_schedule.yaml \
         /home/pi/BM_Devel_Pi/camera_schedule.yaml.phaseE_${ts}
      python3 - <<'PYEOF'
import re
p='/home/pi/BM_Devel_Pi/camera_schedule.yaml'
s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) true', r'\1 false', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) false', r'\1 true', s, count=1)
open(p,'w').write(s)
i=s.find('power_halt:')
print('power_halt block now:'); print(s[i:i+220])
PYEOF
      set +x
      echo 'BACKUP=/home/pi/crontab_backup_phaseE_${ts}.txt'
      echo '--- verify: crontab ---'; crontab -l
      echo '--- verify: no cycle running ---'
      sleep 2; pgrep -af '[r]c_progressive|[r]c_run_capture' || echo 'no cycle running'
      echo '--- identity ---'; hostname; uptime; date -u
      git -C /home/pi/BM_Devel_Pi rev-parse --short HEAD"
    echo "=== done $name ==="
  } 2>&1 | tee "$log"
  echo "$ts"
}

echo "[catch] waiting for bus power on both units (deadline ${DEADLINE_MIN} min)"
echo "[catch] $(date -u +%FT%TZ) start"
# macOS ships bash 3.2 (no associative arrays) -> per-unit marker files hold
# the disarm timestamp, which doubles as a restart-safe record of the §6
# re-arm source filename.
end=$(( $(date +%s) + DEADLINE_MIN * 60 ))
ndone=0
while [ "$(date +%s)" -lt "$end" ]; do
  ndone=0
  for entry in "${HOSTS[@]}"; do
    ip="${entry%%:*}"; name="${entry##*:}"
    marker="$OUTDIR/.disarmed_${name}"
    if [ -f "$marker" ]; then ndone=$((ndone + 1)); continue; fi
    if ssh -o ConnectTimeout=4 -o BatchMode=yes -o StrictHostKeyChecking=no \
         "pi@$ip" true >/dev/null 2>&1; then
      echo "[catch] $(date -u +%FT%TZ) $name IS UP -> disarming now"
      disarm_one "$ip" "$name" | tail -1 > "$marker"
      ndone=$((ndone + 1))
    fi
  done
  if [ "$ndone" -eq "${#HOSTS[@]}" ]; then
    echo "[catch] both units disarmed"
    break
  fi
  sleep 3   # tight poll: forced-boot race against the cycle's power_halt
done

{
  echo "{"
  echo "  \"finished_utc\": \"$(date -u +%FT%TZ)\","
  echo "  \"units\": {"
  first=1
  for entry in "${HOSTS[@]}"; do
    name="${entry##*:}"
    marker="$OUTDIR/.disarmed_${name}"
    [ $first -eq 0 ] && echo ","
    first=0
    if [ -f "$marker" ]; then
      printf '    "%s": {"disarmed": true, "backup_ts": "%s"}' \
        "$name" "$(cat "$marker")"
    else
      printf '    "%s": {"disarmed": false, "backup_ts": null}' "$name"
    fi
  done
  echo ""
  echo "  }"
  echo "}"
} > "$STATE"
cat "$STATE"
[ "$ndone" -eq "${#HOSTS[@]}" ] || { echo "[catch] TIMEOUT - not all units caught"; exit 1; }
