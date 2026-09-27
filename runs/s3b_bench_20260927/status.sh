#!/bin/bash
# status.sh [N] — wait until bmcam003's current stay_on log shows "action N done", then summarise.
N=${1:-1}
ssh -o BatchMode=yes pi@bmcam003 "cd /home/pi/BM_Devel_Pi; L=\$(ls -t cron_logs/rc_cycle_*.log | head -1)
for i in \$(seq 1 200); do grep -q 'action $N done' \$L && break; sleep 5; done
grep -E '=====|heartbeat|shared UART|transmit done|W10|ERR|Traceback|exit|restart|watchdog|RSS' \$L | cut -c1-170 | tail -25
echo '--- procs'; pgrep -af '[r]c_progressive_jp[e]g'; uptime
echo '--- action log'; tail -n 3 cron_logs/supervisor_actions.jsonl" < /dev/null
