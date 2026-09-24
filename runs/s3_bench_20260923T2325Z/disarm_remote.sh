# S3 bench disarm (read by: bash -s). Backup crontab, comment @reboot, stop the running cycle by PID.
set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
echo "HOST=$(hostname) UTC=$(date -u +%FT%TZ) UPTIME=$(cut -d' ' -f1 /proc/uptime)"
mkdir -p ~/backups
crontab -l > ~/backups/crontab.before_s3_bench_$TS 2>/dev/null; echo "crontab backup: ~/backups/crontab.before_s3_bench_$TS ($(wc -l < ~/backups/crontab.before_s3_bench_$TS) lines)"
crontab -l 2>/dev/null | sed 's|^@reboot|#S3BENCH @reboot|' | crontab -
echo "crontab now:"; crontab -l | grep -v '^#[^S]' 
P=$(pgrep -f "rc_progressive_jpeg.py --transmit" | head -1)
if [ -n "$P" ]; then
  KIDS=$(pgrep -P "$P" | tr '\n' ' ')
  echo "cycle pid=$P age_s=$(ps -o etimes= -p $P | tr -d ' ') children=[$KIDS]"
  kill -TERM "$P"; for k in $KIDS; do kill -TERM "$k" 2>/dev/null; done
  sleep 2; pgrep -f "rpicam-vid|ffmpeg" | while read q; do echo "killing leftover $q $(ps -o comm= -p $q)"; kill -TERM $q; done
else echo "no running cycle"; fi
sleep 1; echo "after: cycle=$(pgrep -f 'rc_progressive_jpeg.py' | tr '\n' ' ') halt_script=$(pgrep -f tuned_halt | tr '\n' ' ')"
echo "last cron log lines:"; ls -t ~/rc_logs/*.log 2>/dev/null | head -1 | xargs -r tail -5
