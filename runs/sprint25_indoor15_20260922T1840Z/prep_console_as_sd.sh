#!/bin/zsh
# Live view during the Sprint25 indoor run: turn each Spotter's console log into the SD log/ shape
# the report tool reads (one file per module suffix; every parser matches by pattern, so the same
# stripped console text serves all suffixes). Strips the monitor's host timestamp prefix.
RUN=$(dirname "$0")
for S in SPOT-31593C SPOT-33507C; do
  D=$RUN/console_as_sd/$S; mkdir -p $D
  sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z \.?//' ~/spotter_logs/$S/console_20260922.log > $D/stripped.txt
  for suf in MS BM_TX HDR BRIDGE_SYS ORC SYS; do cp $D/stripped.txt $D/0000_$suf.log; done
  echo "$S: $(wc -l < $D/stripped.txt) lines"
done
