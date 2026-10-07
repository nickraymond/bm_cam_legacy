#!/bin/bash
# G4 red watcher (Mac, read-only): exits with a RED line so the Test Engineer is woken; amber is logged.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c
RUN=runs/g4_outdoor12h_20261002; W=$RUN/watch.log
START_H=${1:-4}; END_H=${2:-15}
for H in $(seq $START_H $END_H); do
  HH=$(printf %02d $H); T=$(python3 -c "import datetime;print(int(datetime.datetime(2026,10,3,$H,14,tzinfo=datetime.timezone.utc).timestamp()))")
  [ $(date +%s) -gt $((T+3000)) ] && continue
  until [ $(date +%s) -ge $T ]; do sleep 30; done
  # alternator: did the :20 run of the previous hour send OK?
  ALT=$(ssh -o BatchMode=yes -o ConnectTimeout=10 pi@192.168.1.45 "grep -h '\"event\"' /home/pi/hil_g4/alternator.jsonl 2>/dev/null | tail -4" < /dev/null 2>/dev/null)
  echo "$(date -u +%FT%TZ) alt: $(echo "$ALT" | python3 -c "import sys,json; print([ (d.get('device'),d.get('event'),d.get('target'),d.get('send_status')) for d in map(json.loads, filter(None, sys.stdin.read().splitlines()))])" 2>/dev/null)" >> $W
  echo "$ALT" | grep -q '"send_status": [^2]' && { echo "RED $(date -u +%T) alternator send failed: $ALT" | tee -a $W; exit 1; }
  # a refused change never writes an "event" line: also check the service result (v2, 2026-10-03 03:58Z)
  RES=$(ssh -o BatchMode=yes -o ConnectTimeout=10 pi@192.168.1.45 "systemctl show -p Result --value hil-g4-alternator.service; journalctl -u hil-g4-alternator.service --since -60min --no-pager | grep -c REFUSED" < /dev/null 2>/dev/null | tr '\n' ' ')
  echo "$(date -u +%FT%TZ) alt service: $RES" >> $W
  ALTBAD=0; echo "$RES" | grep -qE "^success 0 " || ALTBAD=1
  for S in SPOT-33507C SPOT-31593C; do
    out=""; for i in 1 2; do out=$(hil/tools/hil_wake_report.sh $S 2026-10-03T$HH:00 2>&1) && break; sleep 60; done
    echo "$out" | tail -1 >> $W
    echo "$out" | grep -q "monitor unreachable" && { echo "RED $(date -u +%T) nereus000 unreachable ($S $HH:00)" | tee -a $W; exit 1; }
    pi=$(echo "$out" | grep -oE "Pi [0-9:]*→[0-9:]*" | head -1); wth=$(echo "$out" | grep -oE "wake→halt [0-9]+" | grep -oE "[0-9]+")
    [ "$pi" = "Pi →" ] || [ -z "$pi" ] && { echo "RED $(date -u +%T) $S did not wake at $HH:00: $out" | tee -a $W; exit 1; }
    [ -n "$wth" ] && [ "$wth" -gt 590 ] && { echo "RED $(date -u +%T) $S wake→halt ${wth}s at $HH:00" | tee -a $W; exit 1; }
    ssh -o BatchMode=yes pi@192.168.1.45 "awk '\$1 >= \"2026-10-03T$HH:00:00Z\" && \$1 < \"2026-10-03T$HH:59:59Z\"' /home/pi/spotter_logs/$S/console_20261003.log | grep -cE 'rebootctl|Charge mode'" < /dev/null 2>/dev/null | grep -qv '^0$' && echo "AMBER $(date -u +%T) $S rebootctl/charge-mode lines in hour $HH" | tee -a $W
  done
  [ $ALTBAD = 1 ] && { echo "RED $(date -u +%T) alternator service not clean (Result / REFUSED count): $RES" | tee -a $W; exit 1; }
done
echo "watch done $(date -u +%T)"
