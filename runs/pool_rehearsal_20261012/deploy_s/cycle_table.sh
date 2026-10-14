#!/bin/bash
# cycle_table.sh LOG... — one line per cycle log: start, msgs/att, transmit (sent, uart s), heal, cycle end, halt uptime.
for L in "$@"; do
  s=$(grep -m1 -oE 'start_utc=[^ ]+' "$L" | cut -c11-29)
  img=$(grep -m1 -oE '[0-9T:-]+Z_image_compressed' "$L" | cut -c1-20)
  r=$(grep -m1 -oE 'nrjxl ready: [0-9]+ B, [0-9]+ msgs at d=[0-9.]+ att=[0-9]' "$L" | sed 's/nrjxl ready: //')
  t=$(grep -m1 -oE 'transmit done: sent=[0-9]+/[0-9]+ complete=[A-Za-z]+ .* uart=[0-9.]+s' "$L" | sed -E 's/transmit done: //; s/ incomplete_emitted=[A-Za-z]+//')
  hl=$(grep -oE 'sent [0-9]+ heal chunk\(s\) (after END|before START)' "$L" | head -1)
  e=$(grep -m1 -oE 'cycle end: elapsed=[0-9.]+s of [0-9]+s; halt=[a-z_]+' "$L" | sed 's/cycle end: //')
  u=$(grep -m1 -oE 'halt uptime_s=[0-9.]+' "$L")
  echo "${img:-?} | $r | $t | ${hl:-no heal} | $e | $u"
done
