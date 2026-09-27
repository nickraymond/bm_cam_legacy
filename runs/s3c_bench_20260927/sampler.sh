#!/bin/bash
# S3c bench, ON the Pi: every 15 s one CSV row of SD usage and media counts until
# /home/pi/s3cbench/sampler.stop exists. Start: nohup setsid bash sampler.sh </dev/null >/dev/null 2>&1 &
A=/home/pi/BM_Devel_Pi; B=/home/pi/s3cbench; OUT=$B/sampler.csv
[ -f $OUT ] || echo "utc,total_b,used_b,avail_b,used_pct,mp4,natives,compressed,images_b,videos_b" > $OUT
rm -f $B/sampler.stop
while [ ! -f $B/sampler.stop ]; do
  read -r total used avail <<< "$(df -B1 --output=size,used,avail / | tail -1)"
  pct=$(awk -v u=$used -v t=$total 'BEGIN{printf "%.3f", 100*u/t}')
  mp4=$(ls $A/videos/*.mp4 2>/dev/null | wc -l)
  nat=$(ls $A/images/*_native_full.jpg 2>/dev/null | wc -l)
  cmp=$(ls $A/images/*_compressed.jpg 2>/dev/null | wc -l)
  ib=$(du -sb $A/images 2>/dev/null | cut -f1); vb=$(du -sb $A/videos 2>/dev/null | cut -f1)
  echo "$(date -u +%FT%TZ),$total,$used,$avail,$pct,$mp4,$nat,$cmp,$ib,$vb" >> $OUT
  sleep 15
done
