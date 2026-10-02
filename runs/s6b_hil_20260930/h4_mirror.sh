#!/bin/bash
# S6b HIL H4/R5 evidence mirror (bench owner, Mac side). One pass per call; run every ~10 min.
#
# Purpose: give the S6b backend session (no admin API access) readable files on this Mac.
# Outputs (runs/s6b_hil_20260930/h4/, latest copy overwritten each pass):
#   <SPOT>_heal-events.json, <SPOT>_command-events.json   staging admin API, hours=$HOURS
#   events.jsonl, state.json                              conductor run dir on nereus000
#   <SPOT>_console_evidence.txt   console lines: rsd / bmcam/cmd / <HL / HEAL / Sync (all days of the run)
#   <SPOT>_queue_full_10min.txt   "Queue MS_Q_CELLULAR_ONLY is full" count per 10 min (today's log)
#   <host>_pi_evidence.txt        unit cron logs: [HEAL] / [CMD] / <HL lines since the H3 arm
#   media_status.jsonl            completion of every media in an autosend heal (h4_media_status.py)
#   mirror.log                    one line per pass (UTC, sizes, failures)
# The admin token is read on nereus000 and never leaves it.
# Example: runs/s6b_hil_20260930/h4_mirror.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/h4"; mkdir -p "$OUT"
N000=pi@192.168.1.45
RUN=/home/pi/spotter_logs/conductor/20261001T053033Z
API=https://nereus-vision-staging.onrender.com
HOURS="${HOURS:-30}"
TS=$(date -u +%FT%TZ)
FAIL=0
note() { echo "$TS $*" >> "$OUT/mirror.log"; echo "$*"; }

for S in SPOT-33507C SPOT-31593C; do
  for E in heal-events command-events; do
    ssh -o ConnectTimeout=15 $N000 "set -a; . ~/.config/nereus/heal_driver.env; set +a; curl -s -f -m 60 -H \"Authorization: Bearer \$ADMIN_TOKEN\" '$API/systems/$S/$E?hours=$HOURS'" > "$OUT/.${S}_$E.tmp" \
      && [ -s "$OUT/.${S}_$E.tmp" ] && mv "$OUT/.${S}_$E.tmp" "$OUT/${S}_$E.json" \
      || { note "FAIL fetch $S $E"; FAIL=1; }
  done
  ssh -o ConnectTimeout=15 $N000 "grep -a -h -E 'rsd|bmcam/cmd|<HL|HEAL|Sync' /home/pi/spotter_logs/$S/console_2026100*.log" > "$OUT/${S}_console_evidence.txt" 2>/dev/null
  ssh -o ConnectTimeout=15 $N000 "grep -a -h 'MS_Q_CELLULAR_ONLY is full' /home/pi/spotter_logs/$S/console_\$(date -u +%Y%m%d).log | cut -c1-15 | sort | uniq -c" > "$OUT/${S}_queue_full_10min.txt" 2>/dev/null
done

scp -q -o ConnectTimeout=15 "$N000:$RUN/events.jsonl" "$OUT/" 2>/dev/null || { note "FAIL scp events.jsonl"; FAIL=1; }
scp -q -o ConnectTimeout=15 "$N000:$RUN/state.json" "$OUT/" 2>/dev/null || true   # written after the first cycle

ssh -o ConnectTimeout=15 $N000 'python3 -' < "$HERE/h4_media_status.py" > "$OUT/.media.tmp" 2>/dev/null && mv "$OUT/.media.tmp" "$OUT/media_status.jsonl" || note "WARN media_status failed"

for H in bmcam003 bmcam004; do
  ssh -o ConnectTimeout=15 pi@$H 'cd ~/BM_Devel_Pi/cron_logs; for f in $(ls rc_cycle_2026100*.log 2>/dev/null); do grep -a -H -E "\[HEAL\]|\[CMD\]|<HL|heal" "$f"; done' > "$OUT/.${H}_pi.tmp" 2>/dev/null \
    && mv "$OUT/.${H}_pi.tmp" "$OUT/${H}_pi_evidence.txt" || { note "WARN $H unreachable (kept last copy)"; }
done

python3 "$HERE/h4_ledger.py" > /dev/null 2>&1 || note "WARN ledger build failed"
note "pass done fail=$FAIL events=$(wc -l < "$OUT/events.jsonl" 2>/dev/null) heal003=$(wc -c < "$OUT/SPOT-33507C_heal-events.json" 2>/dev/null)B heal004=$(wc -c < "$OUT/SPOT-31593C_heal-events.json" 2>/dev/null)B"
exit $FAIL
