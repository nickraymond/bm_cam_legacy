#!/bin/bash
# RC2 deploy on bmcam003 in the 20Z window (1 PM PDT): development 786b100 (JPEG XL #133/#134 merged) + POST post_20z.sh.
cd /Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/sprint26-s3a-runtime-parity-092958
caffeinate -i -w $$ &
HIL_RUN_DIR=runs/jxl_rc_20261008 ACCEPT_DIFF=1 POST="bash runs/jxl_rc_20261008/post_20z.sh" hil/tools/hil_deploy_window.sh bmcam003 development 786b100 bmcam003/live_20260925 2026-10-08T20:00:00Z rc2 2>&1 | tail -40
