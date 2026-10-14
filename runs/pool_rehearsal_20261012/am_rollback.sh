#!/bin/bash
# am_rollback.sh — roll bmcam003 + bmcam004 back to the pre-deploy point of 2026-10-13 (runtime 9ec4cb7ae7c1, cfg
# 44b81292, registry v11) after the bm #146 + #147 deploy (deploy_s.sh). Two levels:
#
#   bash am_rollback.sh keys    BEHAVIOUR rollback (Nick-approved line, 10/13 9:03 PM): at each unit's next hourly
#                               wake, in its post-transmit listen window, console `reset` of still.raw.progressive +
#                               uplink.media_key.heal_order (drops both from the overlay -> progressive false, heal_order
#                               before = 9ec4cb7 behaviour; S with defaults is wire-identical to 9ec4cb7 per #146/#147).
#                               Ids 66009 (bmcam003) / 66010 (bmcam004). No bus hold. ~1 wake.
#   bash am_rollback.sh code    CODE rollback to 9ec4cb7 (a deploy: needs Nick's own typed OK first). Requires `keys`
#                               done (refuses if either key is still in the overlay: the v11 loader would not know it).
#                               Per unit on a held bus: restore ONLY the runtime files tonight's deploy overwrote, from
#                               the deploy's own tar (/home/pi/backups/BM_Devel_Pi_before_rc_deploy_<host>_<TS>.tgz),
#                               plus software_sha.txt. Never restores state: sent/ (keyed chunks), bm_media_key_last.txt
#                               (key counter: restoring it would REUSE media keys), bm_command_state_v2.json (high-water,
#                               pending heals). Then verify sha 9ec4cb7ae7c1 + print-config [CFG] hash 44b81292 v11,
#                               re-arm (byte-check), halt, hil_restore_schedule.
#
# Inputs: deploy_s/<host>/field_update_*_accept.log (tar path + "copy <file>" list) written by deploy_s.sh deploy.
# Outputs: gate.log lines; deploy_s/<host>/rollback_*.txt.  Limits: bash 3.2; one unit at a time for `code`.
set -u
cd "$(dirname "$0")"; RUN=$PWD; DS="bash $RUN/deploy_s.sh"
O=(-o BatchMode=yes -o ConnectTimeout=4)
log(){ echo "$(date -u +%FT%TZ) [TE2 am_rollback] $*" | tee -a "$RUN/gate.log"; }
MODE=${1:?keys|code}
wait_listen(){ # poll from the next :00 until the unit's listen window is open
  local H=$1 st f a b
  for i in $(seq 1 240); do
    st=$(ssh "${O[@]}" pi@$H 'f=$(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1); echo "$f $(grep -c "post-transmit listen window: " $f) $(grep -c "post-transmit listen window done" $f)"' </dev/null 2>/dev/null)
    read -r f a b <<<"$st"; [ "${a:-0}" = 1 ] && [ "${b:-1}" = 0 ] && { echo "$f"; return 0; }; sleep 5
  done; return 1; }
case "$MODE" in
keys)
  for p in "bmcam003 66009" "bmcam004 66010"; do set -- $p
    ( log "$1: waiting for the next hourly listen window to send reset id $2"
      L=$(wait_listen $1) || { log "$1: no listen window within 20 min (outside a wake? rerun at :00)"; exit 1; }
      log "$1: listen window open ($L)"; $DS reset $1 $2 ) &
  done; wait
  log "keys rollback sent; check 'OK id=66009/66010' in steps.log and [CFG] overlay at the next wake" ;;
code)
  for H in bmcam003 bmcam004; do
    FL=$(ls -t "$RUN"/deploy_s/$H/field_update_*.log "$RUN"/pulled/${H}_field_update_s147_*.log 2>/dev/null | head -1)
    [ -n "$FL" ] || { log "$H: no accepted deploy log, nothing to roll back"; continue; }
    TAR=$(grep -oE '/home/pi/backups/BM_Devel_Pi_before_rc_deploy_[^ ]+\.tgz' "$FL" | head -1)
    FILES=$(grep -oE '\[RC-DEPLOY\] copy [^ ]+' "$FL" | awk '{print "BM_Devel_Pi/"$3}' | tr '\n' ' ')
    log "$H: tar=$TAR, $(echo $FILES | wc -w | tr -d ' ') runtime files; holding the bus"
    $DS hold $H || { log "$H: hold failed, stop"; exit 1; }
    ssh "${O[@]}" pi@$H "python3 -c 'import json;o=json.load(open(\"/home/pi/BM_Devel_Pi/bm_command_state_v2.json\")).get(\"overlay\",{});b=[k for k in o if k in (\"still.raw.progressive\",\"uplink.media_key.heal_order\")];print(\"overlay_v13_keys=\",b);raise SystemExit(1 if b else 0)'" </dev/null \
      || { log "$H: REFUSED: a v13 key is still in the overlay; run 'keys' first. Unit left HELD + disarmed: deploy_s.sh rearm/halt + hil_restore_schedule"; exit 1; }
    ssh "${O[@]}" pi@$H "set -e; cd /home/pi; IN=\$(tar tzf $TAR | sed 's|/\$||'); R=''; NEW=''; for f in $FILES BM_Devel_Pi/software_sha.txt; do echo \"\$IN\" | grep -qx \"\$f\" && R=\"\$R \$f\" || NEW=\"\$NEW \$f\"; done; echo restore:\$R | wc -w; echo new_in_S_left_unused:\$NEW; tar xzf $TAR -C /home/pi \$R; cat BM_Devel_Pi/software_sha.txt; cd BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config 2>&1 | grep -m1 '\[CFG\] config v2'" </dev/null 2>&1 | tee "$RUN/deploy_s/$H/rollback_$(date -u +%Y%m%dT%H%MZ).txt"
    $DS rearm $H && $DS halt $H && HIL_RUN_DIR=$RUN bash -c "cd $RUN/../.. && hil/tools/hil_restore_schedule.sh \$(source hil/hil.env; for r in \"\$HIL_RIG_A\" \"\$HIL_RIG_B\"; do IFS== read -r sp bm ho br <<<\"\$r\"; [ \"\$ho\" = $H ] && echo \$sp; done)"
    log "$H: code rollback done; expect sha 9ec4cb7ae7c1 + hash=44b81292 registry=v11 above"
  done ;;
*) echo "keys|code" >&2; exit 2 ;;
esac
