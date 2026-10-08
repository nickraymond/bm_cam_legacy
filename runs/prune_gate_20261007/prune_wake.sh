#!/bin/bash
# PRUNE gate per wake: collect end-of-wake sent/ listing + cycle log (read-only), then at HH:16 the console wake report
# (wake->halt) and prune_score.py. Usage: prune_wake.sh YYYY-MM-DD HH BEFORE_TSV
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
D=$1; HH=$2; BEF=$3; R=runs/prune_gate_20261007
caffeinate -i -w $$ &
bash $R/eow_collect.sh $D $HH
t=$(python3 -c "import datetime as d,sys;print(int(d.datetime.fromisoformat(sys.argv[1]+'T'+sys.argv[2]+':16:00+00:00').timestamp()))" $D $HH); until [ "$(date +%s)" -ge "$t" ]; do sleep 10; done
HIL_RUN_DIR=$R hil/tools/hil_wake_report.sh SPOT-31593C ${D}T$HH:00 2>&1 | grep '^WAKE'
(cd $R && python3 prune_score.py --before $BEF --after sent_lists/${HH}Z_end.tsv --cycle pulled/cycle_${HH}Z.log)
